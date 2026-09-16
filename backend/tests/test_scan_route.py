"""/scan/run is the path that stays alive in savings mode, so its guarantees need locking down."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.pipeline.schema import ActionBucket, CandidateEvent, Verdict, VerdictLabel
from app.research.models import ResearchOutcome


def event(ticker="MGLV"):
    return CandidateEvent(ticker=ticker, headline=f"{ticker} rights issue", body="", published_at="",
                          source_url="https://idx.test/a.pdf", bucket=ActionBucket.rights_issue,
                          matched_keywords=["rights issue"])


def outcome(status="completed", label=VerdictLabel.inconclusive, bullets=("Sinyal terverifikasi.",)):
    return ResearchOutcome(
        status=status, case_id="MGLV-test",
        verdict=Verdict(label=label, confidence=0.0, provider="ollama:test", rationale_bullets=list(bullets)),
    )


def report(candidates=2, articles=5):
    return {"run_id": "run123", "coverage": "partial_configured_sources", "articles_checked": articles,
            "announcements_matched": 1, "failures": [], "candidates": [{}] * candidates,
            # Verbatim from Scanner.discover: coverage is partial and the UI must keep saying so.
            "coverage_note": "Tidak ada temuan berarti tidak ditemukan pada sumber yang diperiksa, "
                             "bukan bukti tidak ada aksi korporasi di IDX."}


class ScanRouteTests(unittest.TestCase):
    def setUp(self):
        import app.main as main
        self.main = main
        self.client = TestClient(main.app)

    def run_scan(self, items, discovered=None, **kwargs):
        with patch.object(self.main, "DocumentSource"), \
             patch.object(self.main.Scanner, "discover", return_value=discovered or report()), \
             patch.object(self.main, "research_queue_items", return_value=iter(items)):
            return self.client.post("/scan/run", **kwargs)

    def test_it_runs_while_sectors_is_disabled(self):
        """The whole point: savings mode blocks /pipeline/run but must not block this."""
        blocked = self.client.post("/pipeline/run")
        response = self.run_scan([(event(), outcome(), {})])
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("Mode hemat API", blocked.json()["detail"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["screened_count"], 1)

    def test_it_never_touches_the_sectors_client(self):
        with patch.object(self.main, "SectorsClient", side_effect=AssertionError("Sectors dipanggil")):
            self.assertEqual(self.run_scan([(event(), outcome(), {})]).status_code, 200)

    def test_transaction_language_is_gated_before_it_reaches_the_dashboard(self):
        """Same publication boundary as /pipeline/run; a new route must not become a side door."""
        self.run_scan([(event(), outcome(bullets=["Beli sekarang di harga ini"]), {})])
        events = self.client.get("/events").json()
        self.assertEqual(events[0]["gate_status"], "needs_review")
        self.assertNotIn("Beli", " ".join(events[0]["detail"]["verdict"]["rationale_bullets"]))

    def test_results_are_persisted_so_the_dashboard_can_read_them(self):
        before = len(self.client.get("/events").json())
        self.run_scan([(event("APEX"), outcome(), {}), (event("LAPD"), outcome(), {})])
        after = self.client.get("/events").json()
        self.assertEqual(len(after) - before, 2)
        self.assertIn("APEX", {row["ticker"] for row in after})

    def test_the_scan_stage_is_written_to_the_audit_trail(self):
        self.run_scan([(event(), outcome(), {})])
        stages = [row["stage"] for row in self.client.get("/audit").json()]
        for stage in ("scan", "research", "gate"):
            self.assertIn(stage, stages)

    def test_a_scan_that_finds_nothing_researchable_still_answers(self):
        response = self.run_scan([], discovered=report(candidates=0, articles=7))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual((payload["screened_count"], payload["candidates_found"]), (0, 0))
        self.assertIsNone(payload["provider"])
        self.assertIn("bukan bukti", payload["coverage_note"])

    def test_limit_is_bounded(self):
        for limit in (0, 21):
            self.assertEqual(self.client.post(f"/scan/run?limit={limit}").status_code, 422)

    def test_the_run_lock_is_released_even_when_discovery_explodes(self):
        with patch.object(self.main, "DocumentSource"), \
             patch.object(self.main.Scanner, "discover", side_effect=RuntimeError("jaringan mati")):
            with self.assertRaises(RuntimeError):
                self.client.post("/scan/run")
        self.assertFalse(self.main.run_lock.locked())
        self.assertEqual(self.run_scan([(event(), outcome(), {})]).status_code, 200)


if __name__ == "__main__":
    unittest.main()

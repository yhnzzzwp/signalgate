"""/scan/run is the path that stays alive in savings mode, so its guarantees need locking down."""
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.routes import EVENT_PAGE_LIMIT
from app.pipeline.schema import ActionBucket, CandidateEvent, Verdict, VerdictLabel
from app.pipeline.stream import run_events
from app.research.models import ResearchOutcome
from app.scan import write_json


def event(ticker="MGLV"):
    return CandidateEvent(ticker=ticker, headline=f"{ticker} rights issue", body="", published_at="",
                          source_url="https://idx.test/a.pdf", bucket=ActionBucket.rights_issue,
                          matched_keywords=["rights issue"])


def outcome(status="completed", label=VerdictLabel.inconclusive, bullets=("Sinyal terverifikasi.",)):
    return ResearchOutcome(
        status=status, case_id="MGLV-test",
        verdict=Verdict(label=label, confidence=0.0, provider="ollama:test", rationale_bullets=list(bullets)),
    )


def report(candidates=2, articles=5, failures=(), pages=2, status="pending_pdf_review"):
    return {"run_id": "run123", "coverage": "partial_configured_sources", "articles_checked": articles,
            "announcements_matched": 1, "failures": list(failures),
            "listing_pages": [{"url": f"https://sumber.test/{n}"} for n in range(pages)],
            "candidates": [{"status": status}] * candidates,
            # Verbatim from Scanner.discover: coverage is partial and the UI must keep saying so.
            "coverage_note": "Tidak ada temuan berarti tidak ditemukan pada sumber yang diperiksa, "
                             "bukan bukti tidak ada aksi korporasi di IDX."}


class ScanRouteTests(unittest.TestCase):
    def setUp(self):
        import app.main as main
        self.main = main
        self.client = TestClient(main.app)

    def wait(self, job_id, timeout=10.0):
        """Run berjalan di thread latar; tunggu job-nya ditutup, bukan menebak dengan sleep."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.client.get(f"/runs/{job_id}").json()
            if job["status"] != "running":
                return job
            time.sleep(0.01)
        raise AssertionError(f"Job {job_id} tidak selesai dalam {timeout} detik")

    def run_scan(self, items, discovered=None, **kwargs):
        self.published = []
        # Generator, bukan iter(list): yang asli generator dan rute menutupnya lewat closing(),
        # jadi test double yang bukan generator akan menyembunyikan kesalahan pemakaian.
        def fake_queue(*_args, **_kwargs):
            yield from items

        with patch.object(self.main, "DocumentSource"), \
             patch.object(self.main.Scanner, "discover", return_value=discovered or report()), \
             patch.object(self.main, "research_queue_items", side_effect=fake_queue), \
             patch.object(self.main, "mark_published",
                          side_effect=lambda _dir, item_id, _case_id: self.published.append(item_id)):
            response = self.client.post("/scan/run", **kwargs)
            if response.status_code != 202:
                return response, None
            return response, self.wait(response.json()["id"])

    def test_it_runs_while_sectors_is_disabled(self):
        """The whole point: savings mode blocks /pipeline/run but must not block this."""
        blocked = self.client.post("/pipeline/run")
        _response, job = self.run_scan([(event("RUN1"), outcome(), {"id": "qRUN1"})])
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("Mode hemat API", blocked.json()["detail"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"]["processed"], 1)

    def test_it_never_touches_the_sectors_client(self):
        with patch.object(self.main, "SectorsClient", side_effect=AssertionError("Sectors dipanggil")):
            _response, job = self.run_scan([(event("RUN2"), outcome(), {"id": "qRUN2"})])
        self.assertEqual(job["status"], "completed")

    def test_transaction_language_is_gated_before_it_reaches_the_dashboard(self):
        """Same publication boundary as /pipeline/run; a new route must not become a side door."""
        # Ticker dan dedupe id khusus: kartu dicari lewat filter, bukan diasumsikan yang terbaru.
        self.run_scan([(event("GATE"), outcome(bullets=["Beli sekarang di harga ini"]), {"id": "qGATE"})])
        gated = self.client.get("/events?ticker=GATE").json()["results"]
        self.assertEqual(len(gated), 1)
        self.assertEqual(gated[0]["gate_status"], "needs_review")
        self.assertNotIn("Beli", " ".join(gated[0]["detail"]["verdict"]["rationale_bullets"]))

    def test_results_are_persisted_so_the_dashboard_can_read_them(self):
        self.run_scan([(event("APEX"), outcome(), {"id": "qA"}), (event("LAPD"), outcome(), {"id": "qL"})])
        newest = {row["ticker"] for row in self.client.get("/events?limit=5").json()["results"]}
        self.assertIn("APEX", newest)
        self.assertIn("LAPD", newest)

    def test_the_event_list_is_bounded_so_the_payload_cannot_grow_forever(self):
        self.run_scan([(event(f"A{index:03}"), outcome(), {"id": f"q{index}"}) for index in range(6)])
        page = self.client.get("/events?limit=3").json()
        self.assertEqual(len(page["results"]), 3)
        self.assertGreaterEqual(page["total"], 6)
        self.assertTrue(page["has_more"])
        self.assertEqual(self.client.get("/events?limit=0").status_code, 422)
        self.assertEqual(self.client.get("/events?limit=501").status_code, 422)
        self.assertLessEqual(len(self.client.get("/events").json()["results"]), EVENT_PAGE_LIMIT)

    def test_an_older_label_is_reachable_through_pagination_and_server_filters(self):
        """QA P2 #9: tab red flag menampilkan nol padahal hasilnya ada di halaman berikutnya."""
        self.run_scan([(event("REDD"), outcome(label=VerdictLabel.structural_red_flag), {"id": "qR"})])
        self.run_scan([(event(f"N{index:03}"), outcome(), {"id": f"qn{index}"}) for index in range(4)])
        filtered = self.client.get("/events?label=structural_red_flag&limit=2").json()
        self.assertEqual([row["ticker"] for row in filtered["results"]], ["REDD"])
        self.assertEqual(filtered["total"], 1)
        # Hitungan tab tetap menunjukkan label lain walau hasilnya sedang disaring.
        self.assertGreaterEqual(filtered["counts"].get("inconclusive", 0), 4)

    def test_paging_walks_through_without_repeating_or_skipping(self):
        self.run_scan([(event(f"P{index:03}"), outcome(), {"id": f"qp{index}"}) for index in range(5)])
        first = self.client.get("/events?limit=2&offset=0").json()
        second = self.client.get("/events?limit=2&offset=2").json()
        self.assertEqual(len(first["results"]), 2)
        self.assertTrue(set(r["id"] for r in first["results"]).isdisjoint(r["id"] for r in second["results"]))

    def test_a_ticker_filter_narrows_both_results_and_counts(self):
        self.run_scan([(event("ONLY"), outcome(), {"id": "qO"})])
        scoped = self.client.get("/events?ticker=only").json()
        self.assertEqual({row["ticker"] for row in scoped["results"]}, {"ONLY"})
        self.assertEqual(sum(scoped["counts"].values()), scoped["total"])

    def test_the_scan_stage_is_written_to_the_audit_trail(self):
        self.run_scan([(event("RUN3"), outcome(), {"id": "qRUN3"})])
        stages = [row["stage"] for row in self.client.get("/audit").json()["results"]]
        for stage in ("scan", "research", "gate"):
            self.assertIn(stage, stages)

    def test_a_scan_that_finds_nothing_researchable_still_answers(self):
        _response, job = self.run_scan([], discovered=report(candidates=0, articles=7))
        self.assertEqual(job["status"], "completed")
        self.assertEqual((job["result"]["processed"], job["result"]["discovered"]), (0, 0))
        self.assertIn("bukan bukti", job["result"]["coverage_note"])

    def test_a_fetch_failure_is_reported_instead_of_looking_like_an_empty_market(self):
        """"0 kandidat" must not be how a dead source presents itself."""
        broken = report(candidates=0, articles=0, failures=[{"url": "https://idx.test", "error": "HTTPError"}])
        _response, job = self.run_scan([], discovered=broken)
        self.assertEqual(job["result"]["failed_sources"][0]["error"], "HTTPError")
        self.assertEqual(job["result"]["listing_pages_fetched"], 2)

    def test_candidates_without_a_document_are_counted_separately(self):
        found = report(candidates=3, status="needs_document")
        _response, job = self.run_scan([], discovered=found)
        self.assertEqual((job["result"]["discovered"], job["result"]["without_document"]), (3, 3))

    def test_publication_is_marked_only_after_the_row_is_committed(self):
        """Menandai selesai sebelum commit membuat hasil yang hilang tidak pernah dipulihkan."""
        self.run_scan([(event("APEX"), outcome(), {"id": "qA"})])
        self.assertEqual(self.published, ["qA"])

    def test_a_failed_commit_leaves_the_candidate_unpublished_and_fails_the_job(self):
        with patch.object(self.main, "persist_screened_event", side_effect=RuntimeError("disk penuh")):
            _response, job = self.run_scan([(event("APEX"), outcome(), {"id": "qA"})])
        self.assertEqual(self.published, [])
        self.assertEqual(job["status"], "failed")
        self.assertIn("disk penuh", job["error"])

    def test_publishing_the_same_candidate_twice_updates_one_card(self):
        """QA: pemulihan publikasi dan --retry sama-sama bisa menerbitkan kandidat yang sama dua kali."""
        self.run_scan([(event("IDEA"), outcome(), {"id": "qIDEA"})])
        first = self.client.get("/events?ticker=IDEA").json()
        self.run_scan([(event("IDEA"), outcome(label=VerdictLabel.structural_red_flag), {"id": "qIDEA"})])
        second = self.client.get("/events?ticker=IDEA").json()
        self.assertEqual(first["total"], 1)
        self.assertEqual(second["total"], 1, "kandidat yang sama menghasilkan kartu kedua")
        self.assertEqual(second["results"][0]["id"], first["results"][0]["id"])
        self.assertEqual(second["results"][0]["label"], "structural_red_flag")

    def test_different_candidates_still_get_their_own_cards(self):
        self.run_scan([(event("AAA1"), outcome(), {"id": "qA1"}), (event("AAA2"), outcome(), {"id": "qA2"})])
        self.assertEqual(self.client.get("/events?ticker=AAA1").json()["total"], 1)
        self.assertEqual(self.client.get("/events?ticker=AAA2").json()["total"], 1)

    def test_a_real_queue_run_announces_each_case_before_the_model_reads_it(self):
        """QA 17/09 P2: satu kandidat dulu tampil 'kasus 1 dari 3', dan nomornya terbit setelah model selesai.

        Tidak memalsukan research_queue_items: celahnya justru ada di sambungan rute -> generator -> engine.
        """
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        write_json(root / "queue" / "r1.json", {"id": "r1", "event": event("ONLY").model_dump(mode="json"),
                                                "pdf_urls": [], "status": "needs_document"})

        class Engine:
            settings = None

            def research(self_inner, candidate, *_args, **_kwargs):
                run_events.publish("model", candidate.ticker, {"phase": "start", "role": "analyst"})
                run_events.publish("model", candidate.ticker, {"phase": "end", "role": "analyst"})
                return outcome()

            def close(self_inner):
                pass

        settings = self.main.settings.model_copy(update={"scan_directory": root, "research_cases_dir": root / "cases"})
        with patch.object(self.main, "settings", settings), patch.object(self.main, "DocumentSource"), \
             patch.object(self.main.Scanner, "discover", return_value=report(candidates=0)), \
             patch("app.scan.build_provider", return_value=Engine()), run_events.subscribe() as channel:
            job = self.wait(self.client.post("/scan/run?limit=3").json()["id"])
            received = []
            while not channel.empty():
                received.append(channel.get_nowait())
        self.assertEqual(job["status"], "completed")
        order = [(p["stage"], p["detail"].get("phase"), p["detail"].get("total"))
                 for p in received if p["stage"] in ("case", "model")]
        self.assertEqual(order, [("case", "start", 1), ("model", "start", None), ("model", "end", None),
                                 ("case", "end", 1)])
        self.assertEqual(json.loads((root / "queue" / "r1.json").read_text())["published_case_id"], "MGLV-test")

    def test_limit_is_bounded(self):
        for limit in (0, 21):
            self.assertEqual(self.client.post(f"/scan/run?limit={limit}").status_code, 422)

    def test_the_run_lock_is_released_even_when_discovery_explodes(self):
        with patch.object(self.main, "DocumentSource"), \
             patch.object(self.main.Scanner, "discover", side_effect=RuntimeError("jaringan mati")):
            job = self.wait(self.client.post("/scan/run").json()["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("jaringan mati", job["error"])
        self.assertFalse(self.main.run_lock.locked())
        _response, second = self.run_scan([(event("RUN4"), outcome(), {"id": "qRUN4"})])
        self.assertEqual(second["status"], "completed")


if __name__ == "__main__":
    unittest.main()

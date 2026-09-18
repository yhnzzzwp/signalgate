"""Graph laporan empat panel: dimensi wajib, gerbang bukti, perbaikan terarah, replay dan resume."""
import json
import re
import shutil
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from app.config import Settings
from app.db.models import WorkflowReportRecord, WorkflowRunRecord
from app.db.session import build_session_factory
from app.workflow import nodes
from app.workflow.graph import WorkflowCancelled
from app.workflow.runner import WorkflowError, WorkflowRunner
from app.workflow.snapshot import FixtureGateway
from tests import workflow_fixtures as fx

CLAIM_ID = re.compile(r'"claim_id": ?"([^"]+)"')


def snapshot_responses(**overrides):
    responses = {"company_report": fx.company_report(), "quarterly": fx.quarterly(),
                 "daily": lambda params: fx.daily_rows(sessions=130),
                 "corporate_actions": fx.corporate_actions(),
                 "news": fx.news(("TEST rights issue Rp500 miliar", "PT Test Abadi Tbk (TEST) menggelar rights issue "
                                  "senilai Rp500 miliar untuk menambah armada.", "https://berita.test/1",
                                  "2026-09-15T10:00:00"),
                                 ("TEST rights issue Rp500 Miliar", "Salinan artikel yang sama.",
                                  "https://berita.test/2", "2026-09-15T12:00:00"))}
    responses.update(overrides)
    return responses


def research(statement="Pertumbuhan pendapatan kuartalan menguat dibanding periode yang sama tahun lalu.",
             metric_ids=("fundamental:revenue_growth_yoy",)):
    return {"fundamental": [{"statement": statement, "metric_ids": list(metric_ids), "limitations": []}],
            "valuation": [{"statement": "Penilaian pasar berada di atas median pembanding subsektor.",
                           "metric_ids": ["valuation:pe_vs_peer_median"], "limitations": []}]}


def news_events(quote="menggelar rights issue", statement="Perusahaan mengumumkan rencana rights issue."):
    return lambda text: {"events": [{"event_type": "rights_issue", "event_date": "2026-09-15",
                                     "statement": statement, "quote": quote, "attribution": "company_statement",
                                     "source_ids": [source_id(text)]}]}


def source_id(text: str) -> str:
    found = re.findall(r'"id": ?"(news:[0-9a-f]+)"', text)
    return found[0] if found else "news:unknown"


def verdicts_for(status="supported"):
    def build(text):
        claims = re.findall(r'"claim_id": ?"([^"]+)"', text.split("klaim_analis", 1)[-1])
        return {"verdicts": [{"claim_id": claim_id, "status": status, "reason": "Bukti yang dirujuk memadai."}
                             for claim_id in dict.fromkeys(claims)][:12]}
    return build


NOTES = {"observations": [{"statement": "Pertumbuhan pendapatan terlihat pada metrik yang diberikan.",
                           "metric_ids": ["fundamental:revenue_growth_yoy"], "source_ids": []}], "concerns": []}
SYNTHESIS = lambda text: {"sections": [{"text": "Fundamental dan valuasi dibaca dari metrik yang sama, dan "
                                                "keterbatasan datanya tetap dicatat.",
                                        "claim_ids": re.findall(r'"claim_id": ?"([^"]+)"', text)[:2]}]}


class FakePool:
    """Model palsu: mengembalikan objek schema yang valid, atau error yang sudah ditentukan."""

    def __init__(self, settings, responses=None, errors=(), budget=40_000):
        self.settings, self.responses, self.errors = settings, responses or {}, set(errors)
        self._budget, self.calls, self.runs = budget, [], []

    def name(self, role):
        return {"analyst": "fake:analyst", "reviewer": "fake:reviewer"}[role]

    def reason(self, role):
        return f"model {role} palsu tidak tersedia"

    def budget(self, role):
        return self._budget

    def call(self, role, text, schema):
        self.calls.append((role, schema.__name__))
        if schema.__name__ in self.errors:
            return None, "model palsu gagal"
        payload = self.responses.get(schema.__name__)
        if payload is None:
            return None, f"fixture tidak menyediakan {schema.__name__}"
        self.runs.append({"role": role, "model": self.name(role), "seconds": 0.1, "prompt_chars": len(text),
                          "ok": True, "error": None, "attempt": 1})
        return schema.model_validate(payload(text) if callable(payload) else payload), None

    def close(self):
        pass


def pool(responses=None, errors=()):
    defaults = {"ResearchOutput": research(), "NewsOutput": news_events(), "ReviewNotes": NOTES,
                "ReviewVerdicts": verdicts_for(), "SynthesisOutput": SYNTHESIS}
    defaults.update(responses or {})
    created = {}

    def factory(settings):
        created["pool"] = FakePool(settings, defaults, errors)
        return created["pool"]

    factory.created = created
    return factory


class WorkflowTestBase(unittest.TestCase):
    """Runner dengan DB, direktori snapshot, gateway fixture, dan model palsu; semuanya sementara."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="signalgate-workflow-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.settings = Settings(_env_file=None, llm_backend="off", sectors_api_enabled=False,
                                 workflow_directory=self.root / "workflow",
                                 signalgate_db_path=str(self.root / "db.sqlite"))
        self.session_factory = build_session_factory(self.settings)

    def runner(self, responses=None, errors=(), gateway=None, model_factory=None, progress=None):
        gateway = gateway or FixtureGateway(snapshot_responses())
        factory = model_factory or pool(responses, errors)
        runner = WorkflowRunner(self.settings, self.session_factory, gateway_factory=lambda: gateway,
                                progress=progress, model_pool_factory=factory)
        runner.gateway, runner.model_factory = gateway, factory
        return runner

    def run_once(self, runner, as_of=fx.AS_OF, **kwargs):
        run = runner.create(fx.TICKER, "medium", as_of, **kwargs)
        return run, runner.execute(run["run_id"])

    def report_rows(self):
        with self.session_factory() as session:
            return session.scalar(select(func.count()).select_from(WorkflowReportRecord))


class WorkflowGraphTests(WorkflowTestBase):
    # -- jalur utama ------------------------------------------------------------------------------

    def test_a_run_produces_four_panels_with_traceable_numbers(self):
        runner = self.runner()
        _run, result = self.run_once(runner)
        self.assertEqual(sorted(result["panels"]), ["fundamental", "news", "technical", "valuation"])
        report = runner.report(result["run_id"])
        self.assertEqual(report["ticker"], fx.TICKER)
        self.assertEqual(report["gate"], {"status": "passed", "rejected_terms": []})
        self.assertEqual(report["status"], "completed")
        for claim in (claim for panel in report["panels"].values() for claim in panel["claims"]):
            self.assertEqual(claim["validation_status"], "supported", claim)
            self.assertTrue(claim["metric_ids"] or claim["source_ids"], claim)
        # Setiap metrik membawa formula, periode, dan sumber yang bisa dilacak.
        for metric in report["metrics"].values():
            self.assertTrue(metric["formula"] and metric["period"] is not None)
        self.assertEqual(report["credits_used"], 0, "mode fixture tidak memakai kredit")
        artifact = Path(runner.run_dir(result["run_id"])) / "report-v1.json"
        self.assertEqual(json.loads(artifact.read_text())["run_id"], result["run_id"])

    def test_credits_are_counted_per_request_in_live_mode(self):
        gateway = FixtureGateway(snapshot_responses(), mode="live")
        runner = self.runner(gateway=gateway)
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        by_kind = {}
        for source in report["sources"]:
            by_kind[source["kind"]] = by_kind.get(source["kind"], 0) + (source["credits"] or 0)
        self.assertEqual(by_kind["sectors_company_report"], 4)   # satu kredit per seksi
        self.assertEqual(by_kind["sectors_quarterly"], len(fx.quarterly()))  # satu kredit per kuartal
        self.assertEqual(by_kind["sectors_daily"], 2)            # dua jendela 90 hari
        self.assertEqual(by_kind["sectors_corporate_actions"], 1)
        self.assertEqual(report["credits_used"], sum(by_kind.values()))

    def test_the_four_panels_survive_a_missing_model(self):
        """LLM mati: angka tetap terbit, interpretasi tidak dikarang, dan panelnya jujur perlu diperiksa."""
        runner = self.runner(model_factory=lambda settings: FakePool(settings, {}, errors=()))
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        self.assertEqual(sorted(report["panels"]), ["fundamental", "news", "technical", "valuation"])
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(report["panels"]["fundamental"]["status"], "needs_review")
        self.assertTrue(any("tidak tersedia" in item for item in report["panels"]["fundamental"]["limitations"]))
        self.assertTrue(report["panels"]["technical"]["claims"])
        self.assertEqual(report["synthesis"]["author"], "code")

    def test_technical_panel_reports_missing_prices_without_faking_indicators(self):
        gateway = FixtureGateway(snapshot_responses(daily=RuntimeError("Sectors API returned HTTP 402")))
        runner = self.runner(gateway=gateway)
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        self.assertEqual(report["panels"]["technical"]["status"], "failed")
        self.assertTrue(any("harga harian" in item.lower() for item in report["panels"]["technical"]["missing_data"]))
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["panels"]["fundamental"]["status"], "completed")

    # -- gerbang bukti ----------------------------------------------------------------------------

    def test_an_invented_number_is_rejected_even_when_the_reviewer_approves_it(self):
        responses = {"ResearchOutput": research("Pendapatan kuartalan naik 47,5% dibanding tahun lalu.")}
        runner = self.runner(responses)
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        claim = next(claim for claim in report["panels"]["fundamental"]["claims"] if claim["author"] != "code")
        self.assertEqual(claim["validation_status"], "unsupported")
        self.assertTrue(any("Angka tanpa dasar" in note for note in claim["validation_notes"]))
        self.assertEqual(report["status"], "needs_review")

    def test_an_unknown_metric_reference_is_rejected(self):
        runner = self.runner({"ResearchOutput": research(metric_ids=("fundamental:tidak_ada",)),
                              "RepairOutput": {"claims": []}})
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        claim = next(claim for claim in report["panels"]["fundamental"]["claims"] if claim["author"] != "code")
        self.assertEqual(claim["validation_status"], "unsupported")
        self.assertTrue(any("tidak ditemukan" in note for note in claim["validation_notes"]))

    def test_a_quote_that_is_not_in_the_article_is_rejected(self):
        runner = self.runner({"NewsOutput": news_events(quote="mengumumkan pembelian kembali saham"),
                              "RepairOutput": {"claims": []}})
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        claim = next(claim for claim in report["panels"]["news"]["claims"] if claim["author"] != "code")
        self.assertEqual(claim["validation_status"], "unsupported")
        self.assertTrue(any("Kutipan tidak ditemukan" in note for note in claim["validation_notes"]))

    def test_transaction_language_never_reaches_the_report(self):
        runner = self.runner({"ResearchOutput": research("Metrik pertumbuhan ini membuat sahamnya layak dibeli."),
                              "RepairOutput": {"claims": []}})
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        claim = next(claim for claim in report["panels"]["fundamental"]["claims"] if claim["author"] != "code")
        self.assertEqual(claim["validation_status"], "unsupported")
        self.assertEqual(report["gate"]["status"], "passed", "klaim yang ditahan tidak boleh masuk gerbang akhir")
        self.assertNotIn("layak dibeli", json.dumps(report["synthesis"]))

    def test_synthesis_sections_with_new_numbers_are_dropped_and_fall_back_to_code(self):
        runner = self.runner({"SynthesisOutput": lambda text: {
            "sections": [{"text": "Laba bersih tumbuh 88% sehingga valuasinya wajar dibanding pembanding.",
                          "claim_ids": re.findall(r'"claim_id": ?"([^"]+)"', text)[:1]}]}})
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        self.assertEqual(len(report["synthesis"]["dropped"]), 1)
        self.assertEqual(report["synthesis"]["author"], "code")
        self.assertEqual(report["status"], "needs_review")

    # -- perbaikan terarah ------------------------------------------------------------------------

    def test_one_repair_round_can_fix_a_claim_and_the_loop_stops_there(self):
        repaired = {"claims": [{"claim_id": "fundamental:model:1",
                                "statement": "Pertumbuhan pendapatan kuartalan positif dibanding tahun lalu.",
                                "metric_ids": ["fundamental:revenue_growth_yoy"], "source_ids": [], "quote": "",
                                "drop": False}]}
        runner = self.runner({"ResearchOutput": research("Pendapatan naik 47,5% tahun ini."),
                              "RepairOutput": repaired})
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        claim = next(claim for claim in report["panels"]["fundamental"]["claims"] if claim["author"] != "code")
        self.assertEqual((claim["validation_status"], claim["version"]), ("supported", 2))
        self.assertEqual(report["validation"]["repair_count"], 1)
        self.assertEqual(sum(1 for role, schema in runner.model_factory.created["pool"].calls
                             if schema == "RepairOutput"), 1)

    def test_a_dropped_claim_stays_unsupported_after_the_repair_budget(self):
        runner = self.runner({"ResearchOutput": research("Pendapatan naik 47,5% tahun ini."),
                              "RepairOutput": {"claims": [{"claim_id": "fundamental:model:1", "drop": True}]}})
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        claim = next(claim for claim in report["panels"]["fundamental"]["claims"] if claim["author"] != "code")
        self.assertEqual(claim["validation_status"], "unsupported")
        self.assertTrue(any("menarik klaim" in note for note in claim["validation_notes"]))

    def test_a_reviewer_that_contradicts_a_claim_keeps_it_out_of_the_summary(self):
        runner = self.runner({"ReviewVerdicts": verdicts_for("contradicted"), "RepairOutput": {"claims": []}})
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        model_claims = [claim for panel in report["panels"].values() for claim in panel["claims"]
                        if claim["author"] != "code"]
        self.assertTrue(model_claims)
        self.assertTrue(all(claim["validation_status"] == "contradicted" for claim in model_claims))
        summary = json.dumps(report["synthesis"])
        for claim in model_claims:
            self.assertNotIn(claim["claim_id"], summary)

    # -- tanggal acuan historis --------------------------------------------------------------------

    def test_a_historical_as_of_refuses_the_live_company_report(self):
        runner = self.runner()
        _run, result = self.run_once(runner, as_of=date(2026, 6, 30))
        report = runner.report(result["run_id"])
        self.assertEqual(report["mode"], "historical")
        report_source = next(item for item in report["sources"] if item["source_id"] == "sectors:company_report")
        self.assertEqual(report_source["status"], "excluded")
        self.assertEqual(report["panels"]["valuation"]["status"], "insufficient_data")
        self.assertTrue(any("snapshot terkini" in item for item in report["panels"]["valuation"]["missing_data"]))
        self.assertTrue(any("batas lapor" in item for item in report["panels"]["fundamental"]["limitations"]))

    # -- replay, resume, cancel --------------------------------------------------------------------

    def test_replay_reuses_the_stored_snapshot_without_spending_credits(self):
        runner = self.runner()
        first, result = self.run_once(runner)
        replay = runner.create(fx.TICKER, "short", None, replay_of=first["run_id"])
        offline = WorkflowRunner(self.settings, self.session_factory, model_pool_factory=runner.model_factory)
        outcome = offline.execute(replay["run_id"])
        report = offline.report(replay["run_id"])
        self.assertEqual(replay["as_of"], first["as_of"], "replay wajib memakai tanggal acuan run aslinya")
        self.assertEqual(outcome["credits_used"], 0)
        self.assertEqual(report["data_mode"], "replay")
        live = runner.report(result["run_id"])
        self.assertEqual([source["fetched_at"] for source in report["sources"] if source["kind"] == "sectors_quarterly"],
                         [source["fetched_at"] for source in live["sources"] if source["kind"] == "sectors_quarterly"])

    def test_replay_without_a_stored_snapshot_is_refused(self):
        runner = self.runner()
        with self.session_factory() as session:
            session.add(WorkflowRunRecord(run_id="EMPTY-2026-09-18-x", ticker=fx.TICKER, horizon="medium",
                                          as_of=fx.AS_OF.isoformat(), status="failed", data_mode="live"))
            session.commit()
        replay = runner.create(fx.TICKER, "medium", None, replay_of="EMPTY-2026-09-18-x")
        with self.assertRaises(WorkflowError):
            runner.execute(replay["run_id"])

    def test_a_failed_publish_resumes_into_exactly_one_report(self):
        runner = self.runner()
        run = runner.create(fx.TICKER, "medium", fx.AS_OF)
        with patch.object(nodes, "publish_node", side_effect=RuntimeError("disk penuh")):
            with self.assertRaises(RuntimeError):
                runner.execute(run["run_id"])
        self.assertEqual(self.report_rows(), 0)
        with self.session_factory() as session:
            self.assertEqual(session.get(WorkflowRunRecord, run["run_id"]).status, "failed")
        result = runner.execute(run["run_id"], resume=True)
        self.assertEqual(self.report_rows(), 1)
        self.assertEqual(result["report_version"], 1)
        with self.session_factory() as session:
            self.assertEqual(session.get(WorkflowRunRecord, run["run_id"]).status, result["status"])

    def test_resuming_a_finished_run_does_not_publish_again(self):
        runner = self.runner()
        run, _result = self.run_once(runner)
        again = runner.execute(run["run_id"], resume=True)
        self.assertEqual(again["status"], "already_completed")
        self.assertEqual(self.report_rows(), 1)

    def test_running_the_same_run_id_twice_is_refused(self):
        runner = self.runner()
        run, _result = self.run_once(runner)
        with self.assertRaises(WorkflowError):
            runner.execute(run["run_id"])

    def test_cancellation_stops_the_run_but_keeps_the_checkpoint(self):
        runner = self.runner()
        run = runner.create(fx.TICKER, "medium", fx.AS_OF)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(WorkflowCancelled):
            runner.execute(run["run_id"], cancel=cancel)
        with self.session_factory() as session:
            self.assertEqual(session.get(WorkflowRunRecord, run["run_id"]).status, "failed")
        result = runner.execute(run["run_id"], resume=True)
        self.assertEqual(result["status"], "completed")

    def test_progress_is_announced_before_each_node_runs(self):
        seen = []
        runner = self.runner(progress=lambda phase, node, detail: seen.append((phase, node)))
        self.run_once(runner)
        self.assertEqual(seen[0], ("start", "plan"))
        self.assertIn(("start", "publish"), seen)
        self.assertEqual(seen[-1], ("end", "publish"))
        starts = [node for phase, node in seen if phase == "start"]
        self.assertEqual(starts.index("validate") < starts.index("review"), True)


if __name__ == "__main__":
    unittest.main()


class WorkflowStateChannelTests(WorkflowTestBase):
    """Kunci state yang tidak terdaftar di GraphState akan hilang diam-diam; ini yang menjaganya."""

    def seed_screening(self):
        from app.pipeline.audit import persist_screened_event
        from app.pipeline.presentation import screen_outcome
        from app.pipeline.schema import ActionBucket, CandidateEvent, Verdict, VerdictLabel
        from app.research.models import ResearchOutcome

        event = CandidateEvent(ticker=fx.TICKER, headline="TEST rights issue", body="", published_at="2026-09-10",
                               source_url="https://berita.test/screening", bucket=ActionBucket.rights_issue,
                               matched_keywords=["rights issue"])
        outcome = ResearchOutcome(status="completed", case_id="TEST-screen",
                                  verdict=Verdict(label=VerdictLabel.structural_red_flag, confidence=0.7,
                                                  provider="ollama:test", rationale_bullets=["Inbreng pengendali."]))
        with self.session_factory() as session:
            persist_screened_event(session, screen_outcome(event, None, outcome), dedupe_key="scan:screen")

    def test_corporate_actions_and_the_screening_label_reach_the_news_panel(self):
        self.seed_screening()
        actions = fx.corporate_actions(("stock_split", {"date": "2026-08-10", "split_ratio": 4}),
                                       ("right_issue", {"ex_date": "2026-09-01", "ratio": "2:1"}))
        runner = self.runner(gateway=FixtureGateway(snapshot_responses(corporate_actions=actions)))
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        statements = [claim["statement"] for claim in report["panels"]["news"]["claims"]]
        self.assertTrue(any("Stock split" in item and "2026-08-10" in item for item in statements), statements)
        self.assertTrue(any("Rights issue" in item for item in statements), statements)
        self.assertTrue(any("red flag struktural" in item for item in statements), statements)
        self.assertTrue(any(source["kind"] == "signalgate_screening" for source in report["sources"]))
        # Aksi korporasi memotong seri harga, jadi kualitas seri harus ikut tersimpan di laporan.
        self.assertEqual(report["series"]["breaks_in_window"][0]["type"], "stock_split")
        self.assertGreater(report["series"]["quality"]["sessions"], 0)

    def test_an_action_after_the_as_of_date_is_not_reported_as_history(self):
        actions = fx.corporate_actions(("stock_split", {"date": "2026-12-01", "split_ratio": 4}))
        runner = self.runner(gateway=FixtureGateway(snapshot_responses(corporate_actions=actions)))
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        self.assertFalse(any("Stock split" in claim["statement"] for claim in report["panels"]["news"]["claims"]))

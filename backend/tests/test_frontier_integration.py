"""Integrasi frontier ke kedua jalur dengan model dan klien palsu yang deterministik.

R05 shadow tidak mengubah keputusan, R02 escalation hanya lewat aturan kode, R01 cek mekanis tidak bisa
dianulir, C01 frontier mati, C04 pembanding tunggal lama, T05 konteks koreksi di payload, G05 resume.
"""
import hashlib
import json
import re
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.frontier.service import FrontierService
from app.pipeline.schema import VerdictLabel
from app.research.engine import ResearchEngine
from app.runtime import RuntimeStore, load_binding
from app.workflow import nodes
from app.workflow.models import ModelPool
from app.workflow.runner import WorkflowConfigChanged
from app.workflow.snapshot import FixtureGateway
from tests import workflow_fixtures as fx
from tests.frontier_fakes import FAKE_KEY, NoCallClient, ScriptedClient, completion, review_json, verdict
from tests.test_model_rotation import READERS, RotatingModel
from tests.test_research_engine import ARTICLE, SOURCE_URL, FakeScraper, extraction, make_event
from tests.test_workflow_graph import (NOTES, SYNTHESIS, FakePool, WorkflowTestBase, news_events, pool, research,
                                      snapshot_responses, source_id, verdicts_for)


def article_id(url: str) -> str:
    return "news:" + hashlib.sha256(url.encode()).hexdigest()[:10]


def split_verdicts(text):
    """Pembanding 2 membantah klaim berita model; klaim lain disetujui."""
    claims = re.findall(r'"claim_id": ?"([^"]+)"', text.split("klaim_analis", 1)[-1])
    return {"verdicts": [{"claim_id": claim, "status": "contradicted" if claim.startswith("news:model") else "supported",
                          "reason": "Pembanding 2 membaca angka berbeda."} for claim in dict.fromkeys(claims)][:12]}


class TwoReviewerPool(FakePool):
    def __init__(self, settings, responses, per_role):
        super().__init__(settings, responses)
        self.per_role = per_role

    def reviewer_roles(self):
        return ["reviewer_1", "reviewer_2"]

    def name(self, role):
        return {"analyst": "fake:analyst", "reviewer": "fake:r1", "reviewer_1": "fake:r1", "reviewer_2": "fake:r2"}[role]

    def call(self, role, text, schema):
        if schema.__name__ == "ReviewVerdicts" and role in self.per_role:
            self.calls.append((role, schema.__name__))
            return schema.model_validate(self.per_role[role](text)), None
        return super().call(role, text, schema)


def two_reviewers(responses=None):
    base = {"ResearchOutput": research(), "NewsOutput": news_events(), "ReviewNotes": NOTES,
            "ReviewVerdicts": verdicts_for(), "SynthesisOutput": SYNTHESIS}
    base.update(responses or {})
    created = {}

    def factory(settings):
        created["pool"] = TwoReviewerPool(settings, base, {"reviewer_1": verdicts_for("supported"),
                                                           "reviewer_2": split_verdicts})
        return created["pool"]

    factory.created = created
    return factory


def supported_by_frontier(claim_id="news:model:1"):
    return lambda user: completion(review_json(verdict(claim_id, evidence=(source_id(user),))))


class WorkflowFrontierTests(WorkflowTestBase):
    def frontier_runner(self, mode, client, *, model_factory=None, gateway=None, frontier_dir="frontier"):
        runner = self.runner(model_factory=model_factory or two_reviewers(), gateway=gateway)
        runner.settings = self.settings.model_copy(update={
            "frontier_enabled": True, "frontier_mode": mode, "deepseek_api_key": SecretStr(FAKE_KEY),
            "frontier_directory": self.root / frontier_dir})
        runner.frontier_factory = lambda settings: FrontierService(settings, client=client, sleep=lambda _s: None)
        return runner

    @staticmethod
    def decisions(report):
        return {"status": report["status"], "gate": report["gate"],
                "panels": {domain: panel["status"] for domain, panel in report["panels"].items()},
                "claims": {claim["claim_id"]: claim["validation_status"]
                           for panel in report["panels"].values() for claim in panel["claims"]},
                "synthesis": [section["text"] for section in report["synthesis"]["sections"]],
                "chronology": report["chronology"]}

    def test_c01_disabled_frontier_records_nothing_and_changes_nothing(self):
        runner = self.runner(model_factory=two_reviewers())
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        assert report["frontier"] is None
        claim = next(claim for claim in report["panels"]["news"]["claims"] if claim["claim_id"] == "news:model:1")
        assert claim["reviewer_conflict"] and [item["status"] for item in claim["reviewer_verdicts"]] == \
            ["supported", "contradicted"]
        assert claim["validation_status"] == "contradicted"

    def test_r05_shadow_leaves_every_decision_identical_to_off(self):
        off_runner = self.runner(model_factory=two_reviewers())
        _run, off = self.run_once(off_runner)
        client = ScriptedClient([supported_by_frontier(), supported_by_frontier()])
        shadow_runner = self.frontier_runner("shadow", client)
        _run, shadow = self.run_once(shadow_runner)
        off_report, shadow_report = off_runner.report(off["run_id"]), shadow_runner.report(shadow["run_id"])
        assert self.decisions(off_report) == self.decisions(shadow_report)
        record = shadow_report["frontier"]
        assert record["status"] == "completed" and record["mode"] == "shadow" and not record["applied"]
        assert record["decision_changes"] == ["news:model:1"]  # yang AKAN berubah, tetapi tidak diterapkan
        claim = next(claim for claim in shadow_report["panels"]["news"]["claims"]
                     if claim["claim_id"] == "news:model:1")
        assert claim["frontier"]["rule"] == "R3_tie_break" and claim["frontier"]["applied"] is False
        assert len(client.prompts) == 2

    def test_r02_escalation_applies_only_the_tie_break_rule(self):
        client = ScriptedClient([supported_by_frontier(), supported_by_frontier()])
        runner = self.frontier_runner("escalation", client)
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        claim = next(claim for claim in report["panels"]["news"]["claims"] if claim["claim_id"] == "news:model:1")
        assert claim["validation_status"] == "supported" and claim["frontier"]["applied"]
        assert any("Rekonsiliasi frontier (R3_tie_break)" in note for note in claim["validation_notes"])
        assert report["frontier"]["applied"] and report["frontier"]["totals"]["calls"] == 2

    def test_r03_unstable_frontier_leaves_the_conflict_in_escalation(self):
        flip = lambda user: completion(review_json(verdict("news:model:1", "unsupported", (source_id(user),))))
        runner = self.frontier_runner("escalation", ScriptedClient([supported_by_frontier(), flip]))
        _run, result = self.run_once(runner)
        claim = next(claim for claim in runner.report(result["run_id"])["panels"]["news"]["claims"]
                     if claim["claim_id"] == "news:model:1")
        assert claim["validation_status"] == "contradicted" and claim["frontier"]["rule"] == "R2_frontier_unstable"

    def test_r01_a_mechanically_failed_claim_is_never_escalated(self):
        responses = {"NewsOutput": news_events(quote="kutipan karangan yang tidak ada di artikel mana pun")}
        runner = self.frontier_runner("escalation", NoCallClient(), model_factory=two_reviewers(responses))
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        claim = next(claim for claim in report["panels"]["news"]["claims"] if claim["claim_id"] == "news:model:1")
        assert claim["validation_status"] == "unsupported" and claim["mechanical_issues"]
        assert report["frontier"]["status"] == "not_triggered" and "frontier" not in claim

    def test_frontier_failure_never_drops_the_local_run(self):
        from app.frontier.client import FrontierCallError

        down = FrontierCallError("server_error", "HTTP 503", transient=True, billed="unknown")
        runner = self.frontier_runner("escalation", ScriptedClient([down, down]))
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        assert report["frontier"]["status"] == "failed" and report["status"] == "needs_review"
        claim = next(claim for claim in report["panels"]["news"]["claims"] if claim["claim_id"] == "news:model:1")
        assert claim["validation_status"] == "contradicted"

    def test_t05_the_payload_keeps_a_correction_at_the_end_of_the_article(self):
        filler = "Paragraf latar belakang industri dan kinerja kuartalan perseroan. " * 60
        body = ("PT Test Abadi Tbk (TEST) menggelar rights issue senilai Rp500 miliar untuk menambah armada. "
                + filler + "Ralat: nilai rights issue direvisi menjadi Rp450 miliar.")
        gateway = FixtureGateway(snapshot_responses(news=fx.news(
            ("TEST rights issue Rp500 miliar", body, "https://berita.test/panjang", "2026-09-15T10:00:00"))))
        client = ScriptedClient([supported_by_frontier(), supported_by_frontier()])
        runner = self.frontier_runner("shadow", client, gateway=gateway)
        self.run_once(runner)
        assert client.prompts and "Ralat: nilai rights issue direvisi menjadi Rp450 miliar" in client.prompts[0]
        assert '"dipotong": true' in client.prompts[0]

    def test_timeline_conflict_is_escalated_and_marks_the_news_panel(self):
        first, second = "https://berita.test/pp-1", "https://berita.test/pp-2"
        gateway = FixtureGateway(snapshot_responses(news=fx.news(
            ("TEST private placement 640 juta saham", "PT Test Abadi Tbk (TEST) berencana private placement "
             "640 juta saham.", first, "2026-09-01T10:00:00"),
            ("TEST private placement 868 juta saham", "PT Test Abadi Tbk (TEST) menggelar private placement "
             "868 juta saham.", second, "2026-09-15T10:00:00"))))
        events = {"events": [
            {"event_type": "private_placement", "event_date": "", "attribution": "company_statement",
             "statement": "PT Test Abadi Tbk (TEST) berencana private placement 640 juta saham.",
             "quote": "berencana private placement 640 juta saham", "source_ids": [article_id(first)]},
            {"event_type": "private_placement", "event_date": "", "attribution": "company_statement",
             "statement": "PT Test Abadi Tbk (TEST) menggelar private placement 868 juta saham.",
             "quote": "menggelar private placement 868 juta saham", "source_ids": [article_id(second)]}]}
        answer = lambda user: completion(review_json(
            verdict("news:model:1", evidence=(article_id(first),)), verdict("news:model:2", evidence=(article_id(second),))))
        runner = self.frontier_runner("shadow", ScriptedClient([answer]), gateway=gateway,
                                      model_factory=pool({"NewsOutput": events}))
        _run, result = self.run_once(runner)
        report = runner.report(result["run_id"])
        assert report["panels"]["news"]["status"] == "needs_review"
        assert any(item.startswith("Kronologi ") for item in report["panels"]["news"]["conflicts"])
        assert report["chronology"][0]["metrics"]["shares"]["terms_value"] is None
        assert {item["reason"] for item in report["frontier"]["triggers"]} == {"timeline_conflict"}

    def test_g05_resume_after_a_config_change_needs_approval_and_is_recorded(self):
        store = RuntimeStore(self.root / "runtime")
        runner = self.runner(model_factory=two_reviewers())
        runner.runtime_store = store
        run = runner.create(fx.TICKER, "medium", fx.AS_OF)
        with patch.object(nodes, "publish_node", side_effect=RuntimeError("disk penuh")):
            with pytest.raises(RuntimeError):
                runner.execute(run["run_id"])
        runner.settings = runner.settings.model_copy(update={"ollama_local_url": "http://127.0.0.1:11435"})
        changes = runner.config_changes(run["run_id"])
        assert {item["field"] for item in changes} >= {"ollama_url"}
        with pytest.raises(WorkflowConfigChanged):
            runner.execute(run["run_id"], resume=True)
        result = runner.execute(run["run_id"], resume=True, accept_config_change=True)
        report = runner.report(result["run_id"])
        history = load_binding(runner.run_dir(run["run_id"])).history
        assert [entry["event"] for entry in history] == ["start", "resume_config_changed"]
        assert history[1]["from_nodes"] == ["publish"]
        assert report["runtime"]["bound"]["target"] == "local"
        assert "token-baru" not in json.dumps(report)
        assert {timing["config_index"] for timing in report["node_timings"]} == {0}  # publish (indeks 1) setelah report


class ScreeningFrontierTests:
    """Engine screening: dua pembanding lokal berselisih soal penggunaan dana (F02)."""

    def engine(self, tmp_path, mode=None, client=None):
        settings = Settings(_env_file=None, research_cases_dir=tmp_path / "cases", ollama_offload_between_models=True,
                            research_validator_mode="strict", frontier_enabled=mode is not None,
                            frontier_mode=mode or "shadow", deepseek_api_key=FAKE_KEY,
                            frontier_directory=tmp_path / "frontier")
        log = []
        analyst = RotatingModel("ollama:qwen2.5:7b", [extraction()], log)
        missing_funds = {**extraction(), "use_of_funds": []}
        reviewers = [RotatingModel(name, [output], log) for name, output in zip(READERS, [extraction(), missing_funds])]
        frontier = FrontierService(settings, client=client, sleep=lambda _s: None) if mode else None
        return ResearchEngine(settings, model=analyst, reviewers=reviewers, frontier=frontier,
                              scraper=FakeScraper({SOURCE_URL: ("HATM", ARTICLE, [])}))


def funds_supported(_user):
    return completion(review_json(verdict("F02", evidence=("E002",), reason="E002 menyebut dana untuk armada.")))


def test_screening_c01_without_frontier_keeps_the_old_result(tmp_path):
    outcome = ScreeningFrontierTests().engine(tmp_path).research(make_event(), None, use_cache=False)
    assert outcome.frontier is None and outcome.verdict.label == VerdictLabel.inconclusive
    assert [check["agrees_with_label"] for check in outcome.reviewer_checks] == [True, False]


def test_screening_r05_shadow_simulates_but_does_not_publish(tmp_path):
    client = ScriptedClient([funds_supported, funds_supported])
    outcome = ScreeningFrontierTests().engine(tmp_path, "shadow", client).research(make_event(), None, use_cache=False)
    assert outcome.verdict.label == VerdictLabel.inconclusive and outcome.status == "needs_review"
    record = outcome.frontier
    assert record["decision_changed"] and not record["applied"]
    assert record["reconciled_outcome"]["label"] == "growth_catalyst"
    assert record["local_outcome"]["label"] == "inconclusive"


def test_screening_r02_escalation_resolves_the_split_through_python(tmp_path):
    client = ScriptedClient([funds_supported, funds_supported])
    outcome = ScreeningFrontierTests().engine(tmp_path, "escalation", client).research(make_event(), None,
                                                                                         use_cache=False)
    assert outcome.verdict.label == VerdictLabel.growth_catalyst and outcome.status == "completed"
    assert outcome.frontier["applied"] and outcome.frontier["reconciliation"][0]["rule"] == "R3_tie_break"


def test_screening_escalation_does_not_resolve_when_the_frontier_disagrees_with_both(tmp_path):
    contradict = lambda _user: completion(review_json(verdict("F02", "contradicted", ("E002",))))
    outcome = ScreeningFrontierTests().engine(tmp_path, "escalation", ScriptedClient([contradict, contradict])) \
        .research(make_event(), None, use_cache=False)
    assert outcome.verdict.label == VerdictLabel.inconclusive and not outcome.frontier["applied"]


def test_c04_the_legacy_single_reviewer_setting_still_means_one_reviewer():
    legacy = ModelPool(Settings(_env_file=None, llm_backend="ollama", ollama_reviewer_models=["glm4:9b", "gemma3:12b"],
                                workflow_reviewer_model="gemma3:12b"))
    assert legacy.reviewer_roles() == ["reviewer"] and legacy.name("reviewer") == "gemma3:12b"
    both = ModelPool(Settings(_env_file=None, llm_backend="ollama", ollama_reviewer_models=["glm4:9b", "gemma3:12b"]))
    assert both.reviewer_roles() == ["reviewer_1", "reviewer_2"]
    assert [both.name(role) for role in both.reviewer_roles()] == ["glm4:9b", "gemma3:12b"]
    assert both.name("reviewer") == "glm4:9b"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, workflow_reviewer_model="gemma3:12b", workflow_reviewer_models=["glm4:9b"])


def test_the_resume_endpoint_returns_the_changes_instead_of_starting(tmp_path):
    from fastapi.testclient import TestClient

    import app.main as main

    runner = main.workflow_runner
    run_dir = Path(tempfile.mkdtemp(prefix="signalgate-resume-"))
    changes = [{"field": "frontier.mode", "before": "shadow", "after": "escalation"}]
    with patch.object(runner, "config_changes", return_value=changes), \
            patch.object(runner, "run_dir", return_value=run_dir), \
            patch.object(runner, "report", return_value={"run_id": "x"}):
        response = TestClient(main.app).post("/workflow/runs/X-1/resume")
    assert response.status_code == 409 and response.json()["detail"]["changes"] == changes

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import pytest

from app.config import Settings, sectors_block_reason
from app.pipeline.schema import VerdictLabel
from app.research.engine import ResearchEngine
from tests.test_research_engine import ARTICLE, SOURCE_URL, FakeScraper, extraction, make_event

READERS = ["ollama:glm4:9b", "ollama:llama3.1:8b"]


class RotatingModel:
    def __init__(self, name, outputs, log):
        self.name = name
        self.outputs = list(outputs)
        self.log = log

    def check_ready(self):
        pass

    def run(self, prompt, schema):
        self.log.append(("run", self.name))
        return schema.model_validate(self.outputs.pop(0))

    def unload(self):
        self.log.append(("unload", self.name))
        return True


class ModelRotationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.log = []

    def tearDown(self):
        self.temporary.cleanup()

    def engine(self, reviewer_outputs, offload=True):
        settings = Settings(_env_file=None, research_cases_dir=Path(self.temporary.name),
                            ollama_offload_between_models=offload, research_validator_mode="strict")
        analyst = RotatingModel("ollama:qwen2.5:7b", [extraction()], self.log)
        reviewers = [RotatingModel(name, [output], self.log) for name, output in zip(READERS, reviewer_outputs)]
        return ResearchEngine(settings, model=analyst, reviewers=reviewers,
                              scraper=FakeScraper({SOURCE_URL: ("HATM", ARTICLE, [])}))

    def test_each_model_is_offloaded_before_the_next_one_runs(self):
        outcome = self.engine([extraction(), extraction()]).research(make_event(), None, use_cache=False)
        self.assertEqual(self.log, [
            ("run", "ollama:qwen2.5:7b"), ("unload", "ollama:qwen2.5:7b"),
            ("run", "ollama:glm4:9b"), ("unload", "ollama:glm4:9b"),
            ("run", "ollama:llama3.1:8b"), ("unload", "ollama:llama3.1:8b"),
        ])
        self.assertEqual([run["role"] for run in outcome.model_runs if "seconds" in run],
                         ["analyst", "reviewer_1", "reviewer_2"])

    def test_offload_can_be_disabled(self):
        self.engine([extraction(), extraction()], offload=False).research(make_event(), None, use_cache=False)
        self.assertNotIn("unload", [action for action, _ in self.log])

    def test_label_requires_all_three_models_to_agree(self):
        agreed = self.engine([extraction(), extraction()]).research(make_event(), None, use_cache=False)
        self.assertEqual(agreed.verdict.label, VerdictLabel.growth_catalyst)
        missing_funds = {**extraction(), "use_of_funds": []}
        disputed = self.engine([extraction(), missing_funds]).research(make_event(), None, use_cache=False)
        self.assertEqual(disputed.verdict.label, VerdictLabel.inconclusive)

    def test_sectors_api_switch_blocks_live_calls(self):
        disabled = Settings(_env_file=None, sectors_api_key="key", sectors_api_enabled=False)
        self.assertIn("hemat API", sectors_block_reason(disabled))
        self.assertIsNone(sectors_block_reason(Settings(_env_file=None, sectors_api_key="key")))
        self.assertIn("SECTORS_API_KEY", sectors_block_reason(Settings(_env_file=None)))


def test_models_are_not_called_when_every_source_failed(tmp_path):
    log = []
    analyst = RotatingModel("ollama:qwen2.5:7b", [extraction()], log)
    reviewers = [RotatingModel(name, [extraction()], log) for name in READERS]
    engine = ResearchEngine(Settings(_env_file=None, research_cases_dir=tmp_path), model=analyst,
                            reviewers=reviewers, scraper=FakeScraper({}))
    outcome = engine.research(make_event(), None, use_cache=False)
    assert outcome.status == "insufficient_evidence"
    assert log == []


def test_capture_refuses_to_call_sectors_in_saving_mode(monkeypatch, tmp_path):
    from app import evaluate

    monkeypatch.setattr(evaluate, "get_settings",
                        lambda: Settings(_env_file=None, sectors_api_key="key", sectors_api_enabled=False))

    def fail(*args, **kwargs):
        raise AssertionError("Sectors tidak boleh dipanggil dalam mode hemat API")

    monkeypatch.setattr(evaluate, "SectorsClient", fail)
    with pytest.raises(SystemExit):
        evaluate.capture(Namespace(case=["HATM=https://news.test/hatm"], output=str(tmp_path / "capture")))

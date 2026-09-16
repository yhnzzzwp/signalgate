import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.pipeline.schema import ActionBucket, CandidateEvent, VerdictLabel
from app.research.agents import AgentError
from app.research.engine import ResearchEngine

SOURCE_URL = "https://news.test/hatm-private-placement"
ARTICLE = (
    "PT Habco Trans Maritima Tbk (HATM) menerbitkan 868 juta saham baru melalui private placement.\n"
    "Saham baru diserap PT Multi Sarana Nasional, pemegang saham lama perseroan.\n"
    "Dana digunakan untuk menambah armada kapal curah perseroan."
)
NO_FLAG = {"evidence_id": "", "quote": "", "present": False}


def extraction(counterparty_quote="PT Multi Sarana Nasional, pemegang saham lama perseroan"):
    return {
        "action_type": "private_placement",
        "counterparties": [{"evidence_id": "E002", "quote": counterparty_quote,
                            "name": "PT Multi Sarana Nasional", "relation": "existing_shareholder"}],
        "use_of_funds": [{"evidence_id": "E002", "quote": "Dana digunakan untuk menambah armada kapal curah",
                          "category": "core_expansion"}],
        "business_change": NO_FLAG,
        "old_business_divested": NO_FLAG,
        "asset_injection": NO_FLAG,
    }


def validation(second_status="supported", agrees=True):
    result = extraction()
    if second_status == "not_supported":
        result["use_of_funds"] = []
    elif second_status == "contradicted" or not agrees:
        result["use_of_funds"][0]["category"] = "new_business"
    return result



class ScriptedModel:
    def __init__(self, outputs, name="ollama:fake"):
        self.name = name
        self.outputs = list(outputs)
        self.prompts = []

    def check_ready(self):
        pass

    def run(self, prompt, schema):
        self.prompts.append(prompt)
        return schema.model_validate(self.outputs.pop(0))


class UnavailableModel(ScriptedModel):
    def run(self, prompt, schema):
        raise AgentError("Ollama tidak bisa dihubungi")


class FakeScraper:
    def __init__(self, pages):
        self.pages = pages

    def fetch(self, url):
        if url not in self.pages:
            raise ValueError("halaman tidak ada")
        return self.pages[url]


def make_event():
    return CandidateEvent(ticker="HATM", headline="HATM private placement", body="", source_url=SOURCE_URL,
                          published_at="2026-08-20", bucket=ActionBucket.non_preemptive_capital,
                          matched_keywords=["private placement"])


class ResearchEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.cases_dir = Path(self.temporary.name)
        self.scraper = FakeScraper({SOURCE_URL: ("HATM private placement", ARTICLE, [])})

    def tearDown(self):
        self.temporary.cleanup()

    def engine(self, model, validator=None, mode="lenient"):
        settings = Settings(sectors_api_key="", research_cases_dir=self.cases_dir, research_extraction_attempts=2,
                            research_validator_mode=mode)
        return ResearchEngine(settings, model=model, validator=validator, scraper=self.scraper)

    def test_verified_facts_produce_python_scored_label(self):
        model = ScriptedModel([extraction(), validation()])
        outcome = self.engine(model).research(make_event(), None)
        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.verdict.label, VerdictLabel.growth_catalyst)
        self.assertEqual([fact.topic for fact in outcome.facts], ["counterparty", "use_of_funds"])
        self.assertEqual(len(model.prompts), 2)
        self.assertTrue((self.cases_dir / outcome.case_id / "validation.md").exists())

    def test_separate_validator_model_handles_validation(self):
        analyst = ScriptedModel([extraction()], name="ollama:qwen2.5:7b")
        validator = ScriptedModel([validation()], name="ollama:qwen3:4b")
        engine = self.engine(analyst, validator)
        outcome = engine.research(make_event(), None)
        self.assertEqual(engine.name, "ollama:qwen2.5:7b+ollama:qwen3:4b")
        self.assertEqual((len(analyst.prompts), len(validator.prompts)), (1, 1))
        self.assertEqual(outcome.verdict.label, VerdictLabel.growth_catalyst)

    def test_invalid_quote_triggers_second_extraction(self):
        model = ScriptedModel([extraction("diserap investor strategis dari luar negeri"), extraction(), validation()])
        outcome = self.engine(model).research(make_event(), None)
        self.assertEqual(outcome.extraction_attempts, 2)
        self.assertEqual(outcome.verdict.label, VerdictLabel.growth_catalyst)
        self.assertIn("kutipan tidak ditemukan", model.prompts[1])

    def test_contradicted_fact_is_dropped_and_label_downgraded(self):
        model = ScriptedModel([extraction(), validation(second_status="contradicted")])
        outcome = self.engine(model).research(make_event(), None)
        self.assertEqual(outcome.verdict.label, VerdictLabel.inconclusive)
        self.assertTrue(any("menurunkan" in issue for issue in outcome.issues))

    def test_lenient_mode_never_scores_unsupported_fact(self):
        model = ScriptedModel([extraction(), validation(second_status="not_supported")])
        outcome = self.engine(model).research(make_event(), None)
        self.assertEqual(outcome.verdict.label, VerdictLabel.inconclusive)
        self.assertLess(outcome.verdict.confidence, 0.8)

    def test_strict_mode_drops_unsupported_fact(self):
        model = ScriptedModel([extraction(), validation(second_status="not_supported")])
        outcome = self.engine(model, mode="strict").research(make_event(), None)
        self.assertEqual(outcome.verdict.label, VerdictLabel.inconclusive)

    def test_validator_disagreeing_with_label_downgrades_to_inconclusive(self):
        model = ScriptedModel([extraction(), validation(agrees=False)])
        outcome = self.engine(model).research(make_event(), None)
        self.assertEqual(outcome.verdict.label, VerdictLabel.inconclusive)

    def test_without_model_runs_deterministic_only(self):
        outcome = self.engine(None).research(make_event(), None)
        self.assertEqual(outcome.status, "deterministic_only")
        self.assertEqual(outcome.verdict.label, VerdictLabel.inconclusive)

    def test_unavailable_model_is_reported_and_not_cached(self):
        first = self.engine(UnavailableModel([])).research(make_event(), None)
        self.assertEqual(first.status, "model_unavailable")
        retry = self.engine(ScriptedModel([extraction(), validation()])).research(make_event(), None)
        self.assertFalse(retry.cached)
        self.assertEqual(retry.status, "completed")

    def test_completed_outcome_is_cached(self):
        original = self.engine(ScriptedModel([extraction(), validation()])).research(make_event(), None)
        idle = ScriptedModel([])
        repeated = self.engine(idle).research(make_event(), None)
        self.assertTrue(repeated.cached)
        self.assertEqual(repeated.cached_from_case_id, original.case_id)
        self.assertNotEqual(repeated.case_id, original.case_id)
        self.assertEqual(idle.prompts, [])

    def test_no_asserted_facts_skips_validation_call(self):
        empty = {**extraction(), "counterparties": [], "use_of_funds": []}
        model = ScriptedModel([empty])
        outcome = self.engine(model).research(make_event(), None)
        self.assertEqual(len(model.prompts), 1)
        self.assertEqual(outcome.verdict.label, VerdictLabel.inconclusive)


if __name__ == "__main__":
    unittest.main()

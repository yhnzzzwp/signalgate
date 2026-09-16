import tempfile
import unittest
from pathlib import Path

from app.research.evidence import EvidenceStore
from app.research.facts import apply_validation, locate_quote, verified_facts
from app.research.models import Extraction, Fact, Validation

ARTICLE = (
    "PT Leyand International Tbk (LAPD) akan mendivestasi PT Rusindo Eka Raya yang bergerak di bidang distribusi.\n"
    "Pengendali perseroan, PT JSI Sinergi Mas, akan mengeksekusi haknya melalui inbreng saham PT Bersaudara "
    "Sinergi Sejahtera.\n"
    "Pemegang saham lama yang tidak menggunakan haknya dapat mengalami dilusi."
)
NO_FLAG = {"evidence_id": "", "quote": "", "present": False}


def extraction(counterparties=(), divested=None):
    return Extraction.model_validate({
        "action_type": "rights_issue",
        "counterparties": list(counterparties),
        "use_of_funds": [],
        "business_change": NO_FLAG,
        "old_business_divested": divested or NO_FLAG,
        "asset_injection": NO_FLAG,
    })


def party(name, relation, quote):
    return {"evidence_id": "E001", "quote": quote, "name": name, "relation": relation}


class FactsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()

        class NoScraper:
            def fetch(self, url):
                raise ValueError("tidak dipakai")

        self.store = EvidenceStore(Path(self.temporary.name), scraper=NoScraper())
        self.store.add("scrapling", "https://news.test/lapd", "LAPD", ARTICLE)

    def tearDown(self):
        self.temporary.cleanup()

    def test_exact_quote_is_stored_with_source_casing(self):
        span = locate_quote("pengendali perseroan, pt jsi sinergi mas", ARTICLE)
        self.assertEqual(span, "Pengendali perseroan, PT JSI Sinergi Mas")

    def test_near_verbatim_quote_is_accepted_as_source_span(self):
        edited = "LAPD akan mendivestasi PT Rusindo Eka Raya yang bergerak di bidang distribusi."
        span = locate_quote(edited, ARTICLE)
        self.assertIsNotNone(span)
        self.assertIn(span, " ".join(ARTICLE.split()))

    def test_paraphrased_quote_is_rejected(self):
        self.assertIsNone(locate_quote("LAPD berencana menjual anak usaha distribusinya kepada investor", ARTICLE))

    def test_generic_counterparty_name_is_rejected(self):
        facts, issues = verified_facts(extraction([party(
            "Pemegang saham lama", "existing_shareholder",
            "Pemegang saham lama yang tidak menggunakan haknya dapat mengalami dilusi")]), self.store)
        self.assertEqual(facts, [])
        self.assertIn("terlalu umum", issues[0])

    def test_counterparty_name_missing_from_source_is_rejected(self):
        facts, issues = verified_facts(extraction([party(
            "PT Nextier Datamate Center", "affiliate", "akan mengeksekusi haknya melalui inbreng saham")]), self.store)
        self.assertEqual(facts, [])
        self.assertIn("tidak ada di sumber", issues[0])

    def test_specific_counterparty_and_flag_are_verified(self):
        facts, issues = verified_facts(extraction(
            [party("PT JSI Sinergi Mas", "affiliate", "PT JSI Sinergi Mas, akan mengeksekusi haknya melalui inbreng")],
            {"evidence_id": "E001", "quote": "akan mendivestasi PT Rusindo Eka Raya", "present": True},
        ), self.store)
        self.assertEqual(issues, [])
        self.assertEqual([fact.topic for fact in facts], ["counterparty", "old_business_divested"])


def fact(fact_id):
    return Fact(id=fact_id, topic="use_of_funds", value="core_expansion", claim="", quote="kutipan contoh", evidence_id="E001")


def validation(status):
    return Validation(checks=[{"fact_id": "F01", "status": "supported", "reason": "ok"},
                              {"fact_id": "F02", "status": status, "reason": "cek"}],
                      agrees_with_label=True, issues=[])


class ApplyValidationTests(unittest.TestCase):
    def test_lenient_keeps_unsupported_fact_marked(self):
        kept, _ = apply_validation([fact("F01"), fact("F02")], validation("not_supported"), strict=False)
        self.assertEqual([item.validator_status for item in kept], ["supported", "not_supported"])

    def test_strict_drops_unsupported_fact(self):
        kept, _ = apply_validation([fact("F01"), fact("F02")], validation("not_supported"), strict=True)
        self.assertEqual([item.id for item in kept], ["F01"])

    def test_contradicted_fact_is_always_dropped(self):
        kept, _ = apply_validation([fact("F01"), fact("F02")], validation("contradicted"), strict=False)
        self.assertEqual([item.id for item in kept], ["F01"])


if __name__ == "__main__":
    unittest.main()

import json
import unittest

from app.research.context import excerpt, focus_terms, summarize_company_report


class ContextTests(unittest.TestCase):
    def test_focus_terms_include_ticker_and_company_words(self):
        terms = focus_terms("MGLV", "PT Panca Anugrah Wisesa Tbk")
        self.assertIn("mglv", terms)
        self.assertIn("wisesa", terms)
        self.assertNotIn("tbk", terms)

    def test_excerpt_keeps_first_and_relevant_paragraphs_only(self):
        text = "\n".join([
            "Judul berita pasar.",
            "Cuaca Jakarta cerah hari ini.",
            "MGLV menggelar rights issue Rp2,54 triliun.",
            "Resep kue lebaran.",
        ])
        result = excerpt(text, focus_terms("MGLV"), 500)
        self.assertTrue(result.startswith("Judul berita pasar."))
        self.assertIn("rights issue", result)
        self.assertNotIn("Cuaca", result)
        self.assertNotIn("Resep kue", result)

    def test_excerpt_respects_character_limit(self):
        text = "\n".join(f"MGLV paragraf {index} " + "x" * 300 for index in range(50))
        self.assertLessEqual(len(excerpt(text, ["mglv"], 1000)), 1000)

    def test_company_report_summary_is_compact_and_keeps_ownership(self):
        report = {
            "symbol": "MGLV.JK",
            "company_name": "PT Panca Anugrah Wisesa Tbk",
            "overview": {"sector": "Consumer Cyclicals", "market_cap": 29049472017750, "address": "x" * 5000},
            "valuation": {"last_close_price": 15650, "historical_valuation": [{"year": 2025, "pb": 256.5}]},
            "ownership": {"major_shareholders": [
                {"name": "PT Nextier Datamate Center", "share_percentage": "0.7578", "share_value": 1},
            ]},
            "peers": [{"peers_data": "y" * 20000}],
        }
        summary = summarize_company_report(report)
        self.assertLess(len(summary), 2000)
        data = json.loads(summary)
        self.assertEqual(data["major_shareholders"][0]["name"], "PT Nextier Datamate Center")
        self.assertEqual(data["latest_annual_valuation"]["pb"], 256.5)


if __name__ == "__main__":
    unittest.main()

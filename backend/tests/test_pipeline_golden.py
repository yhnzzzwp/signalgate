import os
import unittest

from dotenv import load_dotenv

from app.llm.factory import build_provider
from app.llm.mock_provider import MockProvider
from app.pipeline.gate import apply_gate
from app.pipeline.reason import reason_about_event
from app.pipeline.schema import VerdictLabel
from app.pipeline.sense import collect_candidate_events, snapshot_company
from app.sectors.client import SectorsClient

load_dotenv()

requires_sectors_key = unittest.skipUnless(os.getenv("SECTORS_API_KEY"), "SECTORS_API_KEY not configured")
requires_real_llm_key = unittest.skipUnless(
    any(os.getenv(name) for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY")),
    "no real LLM provider key configured, only mock available",
)


class PipelineGoldenTests(unittest.TestCase):
    """MGLV (PT NexAI Digital Infrastruktur, dulu Panca Anugrah Wisesa) dan HATM (Habco Trans
    Maritima) adalah kasus riil IDX yang dianalisis manual sebelum pipeline ini dibangun. MGLV:
    bisnis lama didivestasi, bisnis AI baru disuntik, PBV ekstrem — structural_red_flag. HATM:
    private placement oleh pemegang saham afiliasi lama buat danai ekspansi armada, laba naik
    667% YoY — growth_catalyst. Mock provider adalah heuristik kasar (bucket + PBV + nama
    pemegang saham) untuk graceful degradation tanpa API key — cukup buat menangkap MGLV karena
    PBV-nya ekstrem, tapi TIDAK cukup buat membedakan HATM (butuh reasoning LLM asli soal siapa
    pemegang saham dan apakah dia pihak lama atau baru)."""

    @classmethod
    @requires_sectors_key
    def setUpClass(cls):
        cls.client = SectorsClient()
        cls.events = collect_candidate_events(cls.client)

    def _first_event_for(self, ticker: str):
        matching = [event for event in self.events if event.ticker == ticker]
        if not matching:
            self.skipTest(f"no live {ticker} event in current scan window")
        return matching[0]

    def test_mglv_flagged_as_structural_red_flag_even_with_mock_provider(self):
        event = self._first_event_for("MGLV")
        snapshot = snapshot_company(self.client, "MGLV")
        verdict = reason_about_event(MockProvider(), event, snapshot)
        self.assertEqual(verdict.label, VerdictLabel.structural_red_flag)
        self.assertTrue(apply_gate(verdict).rejected_terms == [])

    @requires_real_llm_key
    def test_hatm_flagged_as_growth_catalyst_with_real_llm_provider(self):
        from app.config import get_settings

        event = self._first_event_for("HATM")
        snapshot = snapshot_company(self.client, "HATM")
        provider = build_provider(get_settings())
        verdict = reason_about_event(provider, event, snapshot)
        self.assertEqual(verdict.label, VerdictLabel.growth_catalyst)


if __name__ == "__main__":
    unittest.main()

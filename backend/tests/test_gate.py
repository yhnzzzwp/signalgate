import unittest

from app.pipeline.gate import apply_gate, sanitize_for_display
from app.pipeline.schema import GateStatus, Verdict, VerdictLabel


def make_verdict(rationale_bullets, red_flags=None, growth=None):
    return Verdict(
        label=VerdictLabel.inconclusive,
        confidence=0.5,
        rationale_bullets=rationale_bullets,
        red_flag_signals=red_flags or [],
        growth_signals=growth or [],
        provider="mock",
    )


class GateTests(unittest.TestCase):
    def test_clean_rationale_passes(self):
        verdict = make_verdict(["Pengendali baru belum punya rekam jejak publik yang jelas."])
        result = apply_gate(verdict)
        self.assertEqual(result.status, GateStatus.passed)
        self.assertEqual(result.rejected_terms, [])

    def test_indonesian_buy_sell_language_is_rejected(self):
        verdict = make_verdict(["Sebaiknya investor beli saham ini sebelum harga naik lebih jauh."])
        result = apply_gate(verdict)
        self.assertEqual(result.status, GateStatus.needs_review)
        self.assertIn("beli", result.rejected_terms)

    def test_english_buy_sell_language_is_rejected(self):
        verdict = make_verdict(["This looks like a strong buy given the growth trajectory."])
        result = apply_gate(verdict)
        self.assertEqual(result.status, GateStatus.needs_review)
        self.assertIn("buy", result.rejected_terms)

    def test_target_price_language_is_rejected(self):
        verdict = make_verdict(["Target price for this stock is Rp15,000 within six months."])
        result = apply_gate(verdict)
        self.assertEqual(result.status, GateStatus.needs_review)
        self.assertIn("target price", result.rejected_terms)

    def test_denylist_term_in_red_flag_signals_is_also_caught(self):
        verdict = make_verdict(["Rationale is clean."], red_flags=["Investors should sell immediately."])
        result = apply_gate(verdict)
        self.assertEqual(result.status, GateStatus.needs_review)
        self.assertIn("sell", result.rejected_terms)

    def test_sanitize_hides_raw_text_when_flagged(self):
        verdict = make_verdict(["Beli saham ini sekarang."])
        gate = apply_gate(verdict)
        sanitized = sanitize_for_display(verdict, gate)
        joined = " ".join(sanitized.rationale_bullets)
        self.assertNotIn("Beli", joined)
        self.assertEqual(sanitized.red_flag_signals, [])
        self.assertEqual(sanitized.growth_signals, [])

    def test_sanitize_keeps_text_when_passed(self):
        verdict = make_verdict(["Pola ownership konsisten dengan ekspansi organik."])
        gate = apply_gate(verdict)
        sanitized = sanitize_for_display(verdict, gate)
        self.assertEqual(sanitized.rationale_bullets, verdict.rationale_bullets)

    def test_substring_does_not_false_positive(self):
        verdict = make_verdict(["Perusahaan ini bergerak di sektor sellular tower infrastructure."])
        result = apply_gate(verdict)
        self.assertEqual(result.status, GateStatus.passed)


if __name__ == "__main__":
    unittest.main()

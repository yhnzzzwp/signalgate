import unittest

from app.pipeline.gate import apply_gate, prose_fields, sanitize_for_display
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


class GateGapTests(unittest.TestCase):
    """Dua celah yang ditemukan sebelum sinyal data pasar ditambahkan."""

    def verdict(self, **updates):
        base = dict(label=VerdictLabel.inconclusive, confidence=0.0, provider="ollama:test",
                    rationale_bullets=[], red_flag_signals=[], growth_signals=[])
        return Verdict(**(base | updates))

    def test_value_judgements_are_caught_even_without_transaction_words(self):
        """Celah 1: denylist lama hanya menangkap beli/jual, bukan penilaian atas sahamnya."""
        for sentence in ("Sahamnya bagus dan prospek cerah",
                         "Emiten ini layak dikoleksi",
                         "Valuasinya undervalued dibanding peer",
                         "Berpotensi naik setelah aksi korporasi ini"):
            gate = apply_gate(self.verdict(rationale_bullets=[sentence]))
            self.assertEqual(gate.status, GateStatus.needs_review, sentence)

    def test_factual_screening_language_still_passes(self):
        """Penjaga harus ketat tanpa menahan kalimat yang memang produk ini hasilkan."""
        for sentence in ("Free float 7.5% di bawah 15%; penambahan modal memperbesar konsentrasi kendali",
                         "Ada penyuntikan aset dari pihak terkait [E002]",
                         "Arus kas operasi negatif pada 3 dari 4 kuartal terakhir",
                         "PBV 4.2x di atas median subsektor"):
            gate = apply_gate(self.verdict(rationale_bullets=[sentence]))
            self.assertEqual(gate.status, GateStatus.passed, sentence)

    def test_a_field_the_gate_was_never_told_about_is_still_scanned(self):
        """Celah 2: gate lama hanya memindai tiga field, jadi field baru lolos tanpa diperiksa."""
        scanned = prose_fields(self.verdict())
        for name in ("rationale_bullets", "red_flag_signals", "growth_signals"):
            self.assertIn(name, scanned)
        self.assertNotIn("provider", scanned)
        # Setiap field prosa pada model, bukan hanya yang pernah ditulis tangan di gate.
        for name, field in Verdict.model_fields.items():
            if name not in {"label", "confidence", "provider"}:
                self.assertIn(name, scanned, name)

    def test_every_prose_field_is_emptied_when_the_gate_fails(self):
        blocked = self.verdict(rationale_bullets=["Analisis biasa"], growth_signals=["Rekomendasi beli"],
                               red_flag_signals=["Sinyal merah"])
        cleaned = sanitize_for_display(blocked, apply_gate(blocked))
        self.assertEqual(cleaned.growth_signals, [])
        self.assertEqual(cleaned.red_flag_signals, [])
        self.assertIn("pemeriksaan manual", cleaned.rationale_bullets[0])
        self.assertEqual(cleaned.provider, "ollama:test")  # identitas mesin tetap untuk audit

    def test_the_new_summary_field_is_gated_without_touching_gate_code(self):
        """Bukti bahwa pemindaian refleksif berguna: `summary` ditambahkan belakangan dan langsung terjaga."""
        self.assertIn("summary", prose_fields(self.verdict()))
        blocked = self.verdict(summary="Sahamnya layak dikoleksi setelah aksi korporasi ini.")
        gate = apply_gate(blocked)
        self.assertEqual(gate.status, GateStatus.needs_review)
        self.assertEqual(sanitize_for_display(blocked, gate).summary, "")

    def test_a_factual_summary_passes(self):
        summary = ("MGLV mengumumkan rights issue. Dari sumbernya terverifikasi dana untuk ekspansi bisnis "
                   "inti; di sisi lain ada penyuntikan aset dari pihak terkait. Hasil penyaringan: belum "
                   "cukup untuk disimpulkan dan perlu diperiksa manusia.")
        self.assertEqual(apply_gate(self.verdict(summary=summary)).status, GateStatus.passed)

    def test_rejected_terms_are_reported_once_and_sorted(self):
        gate = apply_gate(self.verdict(rationale_bullets=["Beli sekarang"], growth_signals=["beli lagi"]))
        self.assertEqual(gate.rejected_terms, ["beli"])

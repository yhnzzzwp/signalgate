"""Tahap 1: katalog sinyal harus menggambarkan kode yang benar-benar berjalan.

Spesifikasi yang disalin ke dokumen akan menyimpang dari implementasinya, dan ketika itu terjadi
tidak ada yang tahu mana yang benar. Test ini membuat penyimpangan itu gagal secara otomatis.
"""
import unittest

from app import catalog
from app.pipeline.schema import ActionBucket, VerdictLabel
from app.research import scoring


def fired_signal_texts() -> list[str]:
    """Teks alasan sinyal dari seluruh kasus tersimpan, sebagai bukti apa yang pernah benar-benar jalan."""
    import json
    from pathlib import Path
    from app.config import REPO_ROOT
    texts = []
    for path in Path(REPO_ROOT / "cases").rglob("decision.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        texts += [signal.get("reason", "") for signal in payload.get("signals") or []]
    return texts


def market_codes() -> set[str]:
    """Kode yang benar-benar dipancarkan aturan data pasar, digerakkan lewat jalurnya sendiri."""
    quarters = tuple(
        {"date": date, "operating_cash_flow": -1, "revenue": revenue}
        for date, revenue in zip(
            ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"], [100, 200, 300, 400]
        )
    )
    loud = scoring.MarketContext(
        pb_ratio=256.5, bucket=ActionBucket.rights_issue, free_float=0.05,
        free_float_rank=1, free_float_universe=48, quarters=quarters,
        insider_sales=({"transaction_type": "sell", "holder_type": "insider",
                        "share_percentage_transaction": 3.4},),
    )
    facts = [scoring.Fact(id="F01", topic="use_of_funds", value="core_expansion", claim="",
                          quote="kutipan contoh panjang", evidence_id="E002")]
    return {signal.code for signal in scoring.score_signals(loud, facts) if signal.source.startswith("sectors")}


class CatalogueMatchesImplementationTests(unittest.TestCase):
    def test_every_fact_rule_in_the_code_is_described_in_the_catalogue(self):
        missing = {rule[0] for rule in scoring.FACT_RULES.values()} - {s.code for s in catalog.FACT_SIGNALS}
        self.assertEqual(missing, set(), "aturan fakta tanpa entri katalog")

    def test_the_catalogue_describes_no_fact_rule_that_does_not_exist(self):
        described = {s.code for s in catalog.FACT_SIGNALS}
        implemented = {rule[0] for rule in scoring.FACT_RULES.values()} | {"funds_debt_only"}
        self.assertEqual(described - implemented, set(), "entri katalog tanpa aturan di kode")

    def test_a_planned_signal_has_no_code_in_the_scorer_yet(self):
        """`planned` berarti belum ada kodenya; menempatkannya di FACT_SIGNALS akan berbohong."""
        planned = {s.code for s in catalog.PLANNED_SIGNALS}
        self.assertTrue(planned)
        self.assertEqual(planned & {s.code for s in catalog.FACT_SIGNALS + catalog.MARKET_SIGNALS}, set())
        for signal in catalog.PLANNED_SIGNALS:
            self.assertEqual(signal.status, "planned", signal.code)
            self.assertTrue(signal.blocked_by.strip(), signal.code)

    def test_the_exclusivity_rule_is_described_where_the_scorer_emits_it(self):
        """`funds_debt_only` tidak ada di FACT_RULES karena menilai kombinasi, bukan satu fakta."""
        entry = catalog.by_code("funds_debt_only")
        self.assertIsNotNone(entry)
        self.assertEqual((entry.side, entry.weight), ("red", 2))
        self.assertIn("eksklusivitas", entry.rationale)

    def test_fact_weights_and_directions_match_the_code(self):
        for code, side, weight in {(rule[0], rule[1], rule[2]) for rule in scoring.FACT_RULES.values()}:
            entry = catalog.by_code(code)
            self.assertIsNotNone(entry, code)
            self.assertEqual((entry.side, entry.weight), (side, weight), code)

    def test_every_market_rule_that_fires_is_described(self):
        missing = market_codes() - {s.code for s in catalog.MARKET_SIGNALS}
        self.assertEqual(missing, set(), "aturan data pasar tanpa entri katalog")

    def test_the_catalogue_describes_no_market_rule_that_never_fires(self):
        ghosts = {s.code for s in catalog.MARKET_SIGNALS} - market_codes()
        self.assertEqual(ghosts, set(), "entri katalog data pasar yang tidak pernah menyala")

    def test_label_thresholds_match_the_scorer(self):
        self.assertEqual(catalog.RED_FLAG_THRESHOLD, scoring.RED_FLAG_THRESHOLD)
        self.assertEqual(catalog.GROWTH_THRESHOLD, scoring.GROWTH_THRESHOLD)


class CatalogueCompletenessTests(unittest.TestCase):
    """Tahap 1 selesai jika setiap sinyal dan label punya definisi, bukan hanya nama."""

    def test_every_signal_answers_all_four_questions(self):
        for signal in catalog.SIGNALS:
            self.assertTrue(signal.required_data, f"{signal.code}: data wajib kosong")
            self.assertTrue(signal.rule.strip(), f"{signal.code}: aturan kosong")
            self.assertTrue(signal.exceptions, f"{signal.code}: pengecualian kosong")
            self.assertTrue(signal.rationale.strip(), f"{signal.code}: alasan kosong")

    def test_every_label_has_a_definition_and_a_worked_example(self):
        for label in (*catalog.SCREENING_LABELS, *catalog.DECISIONS):
            self.assertTrue(label.definition.strip(), f"{label.key}: definisi kosong")
            self.assertTrue(label.requires, f"{label.key}: syarat kosong")
            self.assertTrue(label.example.strip(), f"{label.key}: contoh kasus kosong")

    def test_screening_labels_cover_exactly_what_the_scorer_can_produce(self):
        self.assertEqual({label.key for label in catalog.SCREENING_LABELS},
                         {label.value for label in VerdictLabel})

    def test_the_decision_vocabulary_is_exactly_buy_or_nothing(self):
        """`sell` dihapus: pencabutan berarti rekomendasi pembelian tidak lagi berlaku; bukan instruksi menjual."""
        self.assertEqual({label.key for label in catalog.DECISIONS}, {"buy", "no_recommendation"})

    def test_every_lifecycle_state_defines_its_reasons(self):
        for state in catalog.LIFECYCLE_STATES:
            self.assertTrue(state.definition.strip(), f"{state.key}: definisi kosong")
            self.assertTrue(state.reasons, f"{state.key}: alasan kosong")

    def test_lifecycle_covers_suspension_revocation_and_expiry(self):
        self.assertEqual({state.key for state in catalog.LIFECYCLE_STATES},
                         {"active", "suspended", "revoked", "expired"})

    def test_an_issued_recommendation_retains_price_horizon_evidence_and_rule_version(self):
        for name in ("reference_price", "reference_price_at", "horizon_end",
                     "evidence_case_ids", "rule_version", "catalog_version"):
            self.assertIn(name, catalog.RETAINED_FIELDS, name)


class ProductBoundaryTests(unittest.TestCase):
    """Batas yang dijaga sampai Tahap 6 menyepakati metode valuasinya."""

    def test_decisions_are_not_claimed_as_implemented(self):
        """Menandai buy selain 'planned' sebelum metode valuasinya ada adalah klaim palsu."""
        for label in catalog.DECISIONS:
            self.assertEqual(label.status, "planned", label.key)

    def test_absence_is_not_claimed_as_evidence(self):
        """'Tidak disebut' dan 'tidak ada' berbeda; menyamakannya menghasilkan red flag palsu."""
        standby = catalog.by_code("no_standby_buyer")
        self.assertEqual(standby.status, "planned")
        self.assertIn("tidak dapat dibedakan", standby.blocked_by)
        self.assertTrue(any("sebaliknya" in note for note in standby.exceptions))

    def test_a_signal_is_only_called_verified_when_it_has_run_on_real_data(self):
        """Klaim 'sudah berjalan' harus punya bukti, bukan hanya lulus unit test.

        Bukti diambil dari kasus tersimpan di cases/, termasuk run lama yang belum punya kolom
        `code` sehingga dicocokkan lewat teks alasannya.
        """
        fired = fired_signal_texts()
        for signal in catalog.SIGNALS:
            if signal.status != "verified":
                continue
            rule = next((r for r in scoring.FACT_RULES.values() if r[0] == signal.code), None)
            if rule is None:
                continue  # aturan kombinasi tidak punya satu baris FACT_RULES
            reason = rule[3]
            self.assertTrue(any(reason[:40] in text for text in fired),
                            f"{signal.code} ditandai verified tetapi tidak ada di cases/")

    def test_an_untested_signal_says_what_is_blocking_it(self):
        for signal in catalog.SIGNALS:
            if signal.status == "untested":
                self.assertTrue(signal.blocked_by.strip(), f"{signal.code}: penyebab kosong")

    def test_market_signals_are_not_claimed_as_verified_while_sectors_is_off(self):
        """Empat aturan data pasar butuh data Sectors dan belum pernah jalan pada data nyata."""
        for signal in catalog.MARKET_SIGNALS:
            self.assertEqual(signal.status, "untested", signal.code)

    def test_there_is_no_sell_label_at_all(self):
        """Menyarankan jual kepada yang tidak memegang adalah ajakan short, produk yang berbeda."""
        keys = {label.key for label in (*catalog.DECISIONS, *catalog.SCREENING_LABELS)}
        self.assertNotIn("sell", keys)
        self.assertNotIn("sell", {state.key for state in catalog.LIFECYCLE_STATES})

    def test_buy_states_a_margin_that_can_be_computed(self):
        """'Melebihi ketidakpastian asumsinya' tidak dapat dihitung; ambangnya harus bernama."""
        buy = next(label for label in catalog.DECISIONS if label.key == "buy")
        self.assertTrue(any("MARGIN_OF_SAFETY" in requirement for requirement in buy.requires))
        self.assertIn("MARGIN_OF_SAFETY", buy.definition)

    def test_the_margin_is_left_unset_until_the_valuation_method_exists(self):
        """Angka yang dikarang sekarang akan dipakai seolah sudah dikalibrasi."""
        self.assertIsNone(catalog.MARGIN_OF_SAFETY)


    def test_no_recommendation_is_the_default_rather_than_a_hold_call(self):
        default = next(l for l in catalog.DECISIONS if l.key == "no_recommendation")
        self.assertIn("bawaan", default.definition)
        self.assertIn("bukan `hold`", default.definition)
        self.assertIn("Syarat BUY belum terpenuhi", default.definition)

    def test_market_data_alone_cannot_reach_a_label_even_when_it_outweighs_the_threshold(self):
        """Invariannya struktural, bukan aritmetika.

        Bobot data pasar kini melampaui ambang merah bila dijumlahkan. Yang menahannya adalah
        `decide()` yang menolak melabeli apa pun tanpa fakta terverifikasi dari dokumen sumber.
        """
        self.assertGreaterEqual(sum(signal.weight for signal in catalog.MARKET_SIGNALS),
                                catalog.RED_FLAG_THRESHOLD)
        market_only = [scoring.Signal("red", signal.weight, signal.name, "sectors:uji", signal.code)
                       for signal in catalog.MARKET_SIGNALS]
        self.assertEqual(scoring.decide(market_only), (VerdictLabel.inconclusive, 0.0))

    def test_the_gate_still_refuses_the_planned_recommendation_vocabulary(self):
        """Katalog boleh mendefinisikan buy/sell; gate tetap menolaknya sampai Tahap 6."""
        from app.pipeline.gate import apply_gate
        from app.pipeline.schema import GateStatus, Verdict
        for word in ("buy", "sell"):
            verdict = Verdict(label=VerdictLabel.inconclusive, confidence=0.0, provider="test",
                              summary=f"Rekomendasi {word} untuk emiten ini.")
            self.assertEqual(apply_gate(verdict).status, GateStatus.needs_review, word)


if __name__ == "__main__":
    unittest.main()

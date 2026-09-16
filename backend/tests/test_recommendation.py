"""Perubahan status ketika aksi korporasi ditunda, dibatalkan, atau direvisi.

Penerbitan BUY masih menunggu metode valuasi, tetapi mesin transisinya deterministik dan diuji
sekarang: menunda logika yang bisa diuji sampai ada angka valuasi berarti menulisnya terburu-buru
bersamaan dengan hal yang sulit.
"""
import unittest
from datetime import UTC, datetime, timedelta

from app import catalog
from app.recommendation import (ACTIVE, EXPIRED, REVOKED, SUSPENDED, LifecycleError, Recommendation,
                                TRANSITIONS, Trigger, issue)

ISSUED = datetime(2026, 9, 16, tzinfo=UTC)


def buy(**overrides) -> Recommendation:
    fields = dict(
        reference_price=1_250.0, reference_price_at=ISSUED.isoformat(), fair_value=1_800.0,
        margin=0.31, horizon_end=(ISSUED + timedelta(days=182)).isoformat(),
        review_due=(ISSUED + timedelta(days=91)).isoformat(), rule_version="2026.09.1",
        catalog_version="2026.09.1", evidence_case_ids=("MGLV-b24b5e3c9681",),
        evidence_fact_ids=("F01", "F05"),
    )
    return issue("MGLV", ISSUED, **(fields | overrides))


def at(days: int) -> str:
    return (ISSUED + timedelta(days=days)).isoformat()


class PostponedCancelledRevisedTests(unittest.TestCase):
    """Tiga peristiwa yang diminta backlog, dan ketiganya berakhir berbeda."""

    def test_a_postponed_action_suspends_rather_than_revokes(self):
        """Ditunda bukan dibatalkan: dasarnya mungkin masih utuh, jadi ditahan, bukan ditarik."""
        after = buy().apply(Trigger.ACTION_POSTPONED, at(10))
        self.assertEqual(after.state, SUSPENDED)
        self.assertFalse(after.is_actionable)
        self.assertEqual(after.history[-1].reason, "aksi korporasi yang menjadi dasar ditunda")

    def test_a_cancelled_action_revokes_because_the_basis_is_gone(self):
        after = buy().apply(Trigger.ACTION_CANCELLED, at(10))
        self.assertEqual(after.state, REVOKED)
        self.assertEqual(after.history[-1].reason, "aksi korporasi yang menjadi dasar dibatalkan")

    def test_a_revised_report_suspends_pending_reassessment(self):
        after = buy().apply(Trigger.REPORT_REVISED, at(30))
        self.assertEqual(after.state, SUSPENDED)
        self.assertIn("direvisi", after.history[-1].reason)

    def test_a_postponed_action_that_is_later_cancelled_ends_revoked(self):
        after = buy().apply(Trigger.ACTION_POSTPONED, at(10)).apply(Trigger.ACTION_CANCELLED, at(40))
        self.assertEqual([step.state for step in after.history], [ACTIVE, SUSPENDED, REVOKED])

    def test_a_suspension_can_be_cleared_back_to_active(self):
        after = buy().apply(Trigger.REPORT_REVISED, at(30)).apply(Trigger.REVIEW_CLEARED, at(35))
        self.assertEqual(after.state, ACTIVE)
        self.assertTrue(after.is_actionable)


class ExpiryTests(unittest.TestCase):
    def test_a_passed_horizon_expires_rather_than_being_judged_wrong(self):
        after = buy().apply(Trigger.HORIZON_PASSED, at(183))
        self.assertEqual(after.state, EXPIRED)
        self.assertIn("horizon", after.history[-1].reason)

    def test_a_suspended_recommendation_can_still_run_out_of_time(self):
        after = buy().apply(Trigger.ACTION_POSTPONED, at(10)).apply(Trigger.HORIZON_PASSED, at(183))
        self.assertEqual(after.state, EXPIRED)

    def test_horizon_passed_is_refused_before_the_horizon_ends(self):
        """Pemicu kedaluwarsa tidak boleh diterima sebelum horizon terlampaui."""
        with self.assertRaises(LifecycleError) as caught:
            buy().apply(Trigger.HORIZON_PASSED, at(1))
        self.assertIn("sebelum horizon berakhir", str(caught.exception))

    def test_suspended_recommendation_cannot_return_to_active_after_horizon(self):
        """Rekomendasi yang ditangguhkan tidak boleh aktif kembali setelah masa horizonnya berakhir."""
        suspended = buy().apply(Trigger.ACTION_POSTPONED, at(10))
        with self.assertRaises(LifecycleError) as caught:
            suspended.apply(Trigger.REVIEW_CLEARED, at(200))
        self.assertIn("setelah horizon berakhir", str(caught.exception))

    def test_transitions_cannot_move_backwards_in_time(self):
        """Urutan waktu transisi tidak boleh mundur mendahului langkah sebelumnya."""
        suspended = buy().apply(Trigger.ACTION_POSTPONED, at(10))
        with self.assertRaises(LifecycleError) as caught:
            suspended.apply(Trigger.REPORT_REVISED, at(5))
        self.assertIn("tidak boleh mendahului", str(caught.exception))

    def test_active_status_alone_is_not_enough_for_actionability_after_horizon(self):
        """Status aktif saja belum cukup untuk menentukan rekomendasi masih berlaku."""
        record = buy()
        self.assertTrue(record.is_actionable)
        self.assertTrue(record.is_actionable_at(at(100)))
        # Pada hari ke-200, horizon (182 hari) sudah berakhir sehingga tidak lagi berlaku
        self.assertFalse(record.is_actionable_at(at(200)))


class TerminalStateTests(unittest.TestCase):
    """Yang sudah berakhir tidak dihidupkan kembali; penilaian baru menerbitkan rekomendasi baru."""

    def test_a_revoked_recommendation_refuses_every_further_trigger(self):
        revoked = buy().apply(Trigger.ACTION_CANCELLED, at(10))
        for trigger in Trigger:
            with self.assertRaises(LifecycleError, msg=trigger.value):
                revoked.apply(trigger, at(20))

    def test_an_expired_recommendation_cannot_be_revived(self):
        expired = buy().apply(Trigger.HORIZON_PASSED, at(183))
        with self.assertRaises(LifecycleError):
            expired.apply(Trigger.REVIEW_CLEARED, at(184))

    def test_the_error_says_why_instead_of_failing_silently(self):
        revoked = buy().apply(Trigger.ACTION_CANCELLED, at(10))
        with self.assertRaises(LifecycleError) as caught:
            revoked.apply(Trigger.REVIEW_CLEARED, at(20))
        self.assertIn("status akhir", str(caught.exception))

    def test_an_active_recommendation_refuses_a_clearance_it_never_needed(self):
        with self.assertRaises(LifecycleError):
            buy().apply(Trigger.REVIEW_CLEARED, at(5))


class HistoryTests(unittest.TestCase):
    """Riwayat tidak pernah ditimpa: pilot bertanggal Tahap 8 bersandar padanya."""

    def test_every_change_appends_a_step_with_reason_time_and_actor(self):
        after = buy().apply(Trigger.ACTION_POSTPONED, at(10), actor="rini")
        self.assertEqual(len(after.history), 2)
        step = after.history[-1]
        self.assertEqual((step.trigger, step.actor, step.at),
                         (Trigger.ACTION_POSTPONED.value, "rini", at(10)))
        self.assertTrue(step.reason.strip())

    def test_applying_a_trigger_leaves_the_earlier_record_untouched(self):
        original = buy()
        original.apply(Trigger.ACTION_CANCELLED, at(10))
        self.assertEqual(original.state, ACTIVE)
        self.assertEqual(len(original.history), 1)

    def test_the_issued_record_keeps_everything_needed_to_evaluate_it_later(self):
        record = buy()
        for name in catalog.RETAINED_FIELDS:
            self.assertTrue(hasattr(record, name), f"field wajib hilang: {name}")
            self.assertIsNotNone(getattr(record, name), name)

    def test_the_reference_price_carries_its_own_timestamp(self):
        """Harga tanpa waktu tidak dapat diperiksa ulang."""
        record = buy()
        self.assertTrue(record.reference_price_at)
        self.assertNotEqual(record.reference_price_at, record.horizon_end)


class CatalogueAgreementTests(unittest.TestCase):
    def test_every_state_the_machine_produces_is_defined_in_the_catalogue(self):
        produced = {ACTIVE} | {state for state, _reason in TRANSITIONS.values()}
        self.assertEqual(produced, {state.key for state in catalog.LIFECYCLE_STATES})

    def test_every_reason_the_machine_records_is_listed_for_that_state(self):
        allowed = {state.key: set(state.reasons) for state in catalog.LIFECYCLE_STATES}
        for (_current, trigger), (state, reason) in TRANSITIONS.items():
            self.assertIn(reason, allowed[state], f"{trigger.value} -> {state}")

    def test_terminal_states_have_no_way_out(self):
        for state in catalog.LIFECYCLE_STATES:
            if not state.terminal:
                continue
            self.assertEqual([key for key in TRANSITIONS if key[0] == state.key], [], state.key)

    def test_sell_is_not_part_of_the_product(self):
        """Pencabutan berarti rekomendasi pembelian tidak lagi berlaku; bukan instruksi menjual."""
        self.assertNotIn("sell", {decision.key for decision in catalog.DECISIONS})

    def test_decisions_and_lifecycle_are_separate_vocabularies(self):
        overlap = {d.key for d in catalog.DECISIONS} & {s.key for s in catalog.LIFECYCLE_STATES}
        self.assertEqual(overlap, set())

    def test_issuing_is_still_planned_until_the_valuation_method_exists(self):
        for decision in catalog.DECISIONS:
            self.assertEqual(decision.status, "planned", decision.key)
        self.assertIsNone(catalog.MARGIN_OF_SAFETY)


if __name__ == "__main__":
    unittest.main()

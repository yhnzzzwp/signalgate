import unittest

from app.pipeline.schema import ActionBucket, CompanySnapshot, VerdictLabel
from app.research.models import Fact
from app.research.scoring import Signal
from app.research.scoring import (
    MarketContext,
    debt_only_signals,
    compose_summary,
    decide,
    fact_signals,
    market_signals,
    score_signals,
    sectors_signals,
)


def quarters(*, ocf=(), revenue=()):
    dates = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"]
    rows = []
    for index, date in enumerate(dates):
        row = {"date": date}
        if index < len(ocf):
            row["operating_cash_flow"] = ocf[index]
        if index < len(revenue):
            row["revenue"] = revenue[index]
        rows.append(row)
    return tuple(rows)


def context(bucket=ActionBucket.rights_issue, **kwargs):
    return MarketContext(bucket=bucket, **kwargs)


def fact(topic, value, claim=""):
    return Fact(id="F01", topic=topic, value=value, claim=claim, quote="kutipan contoh panjang", evidence_id="E002")


class ScoringTests(unittest.TestCase):
    def test_business_pivot_with_asset_injection_is_structural_red_flag(self):
        signals = sectors_signals(256.5) + fact_signals([
            fact("business_change", "present"),
            fact("asset_injection", "present"),
            fact("use_of_funds", "new_business"),
        ])
        label, confidence = decide(signals)
        self.assertEqual(label, VerdictLabel.structural_red_flag)
        self.assertGreater(confidence, 0.4)

    def test_existing_shareholder_funding_core_expansion_is_growth_catalyst(self):
        signals = fact_signals([
            fact("counterparty", "affiliate", "PT Multi Sarana Nasional"),
            fact("use_of_funds", "core_expansion"),
        ])
        label, _ = decide(signals)
        self.assertEqual(label, VerdictLabel.growth_catalyst)
        self.assertIn("PT Multi Sarana Nasional", signals[0].reason)

    def test_extreme_pbv_alone_is_inconclusive(self):
        label, confidence = decide(sectors_signals(256.5))
        self.assertEqual(label, VerdictLabel.inconclusive)
        self.assertEqual(confidence, 0.0)

    def test_same_fact_type_is_counted_once(self):
        signals = fact_signals([fact("use_of_funds", "core_expansion"), fact("use_of_funds", "core_expansion")])
        self.assertEqual(len(signals), 1)

    def test_affiliate_and_existing_shareholder_count_as_one_signal(self):
        signals = fact_signals([
            fact("counterparty", "affiliate", "PT A"),
            fact("counterparty", "existing_shareholder", "PT B"),
        ])
        self.assertEqual(len(signals), 1)

    def test_mixed_signals_stay_inconclusive(self):
        signals = fact_signals([
            fact("business_change", "present"),
            fact("counterparty", "existing_shareholder", "PT A"),
            fact("use_of_funds", "core_expansion"),
        ])
        self.assertEqual(decide(signals)[0], VerdictLabel.inconclusive)


class FloatRiskTests(unittest.TestCase):
    def test_thin_float_on_a_capital_raise_is_a_red_signal(self):
        signals = market_signals(context(free_float=0.0749, free_float_rank=1, free_float_universe=48))
        self.assertEqual([(signal.side, signal.code) for signal in signals], [("red", "float_risk")])
        self.assertIn("7.5%", signals[0].reason)
        self.assertIn("tertipis ke-1 dari 48", signals[0].reason)

    def test_thin_float_without_a_capital_raise_stays_silent(self):
        self.assertEqual(market_signals(context(ActionBucket.general_action, free_float=0.05)), [])

    def test_healthy_float_stays_silent(self):
        self.assertEqual(market_signals(context(free_float=0.44)), [])

    def test_missing_float_stays_silent(self):
        self.assertEqual(market_signals(context()), [])

    def test_rank_is_omitted_when_the_subsector_is_unknown(self):
        signals = market_signals(context(free_float=0.10))
        self.assertNotIn("tertipis", signals[0].reason)

    def test_thin_float_alone_is_not_enough_to_label(self):
        label, confidence = decide(market_signals(context(free_float=0.05)))
        self.assertEqual(label, VerdictLabel.inconclusive)
        self.assertEqual(confidence, 0.0)


class CashBurnTests(unittest.TestCase):
    def test_three_negative_quarters_during_a_raise_is_a_red_signal(self):
        signals = market_signals(context(quarters=quarters(ocf=(-5, -4, -3, 2))))
        self.assertEqual([signal.code for signal in signals], ["cash_burn"])
        self.assertIn("3 dari 4 kuartal", signals[0].reason)

    def test_two_negative_quarters_is_not_enough(self):
        self.assertEqual(market_signals(context(quarters=quarters(ocf=(-5, -4, 3, 2)))), [])

    def test_positive_cash_flow_stays_silent(self):
        self.assertEqual(market_signals(context(quarters=quarters(ocf=(111, 115, 98, 90)))), [])

    def test_an_incomplete_window_stays_silent(self):
        self.assertEqual(market_signals(context(quarters=quarters(ocf=(-5, -4, -3)))), [])

    def test_a_quarter_missing_the_field_stays_silent(self):
        rows = list(quarters(ocf=(-5, -4, -3, -2)))
        rows[2].pop("operating_cash_flow")
        self.assertEqual(market_signals(context(quarters=tuple(rows))), [])

    def test_cash_burn_without_a_capital_raise_stays_silent(self):
        self.assertEqual(market_signals(context(ActionBucket.general_action, quarters=quarters(ocf=(-5, -4, -3, -2)))), [])


class ContradictionTests(unittest.TestCase):
    def test_core_expansion_claim_against_falling_revenue_is_flagged(self):
        signals = score_signals(context(quarters=quarters(revenue=(100, 200, 300, 400))),
                                [fact("use_of_funds", "core_expansion")])
        self.assertIn("expansion_contradiction", [signal.code for signal in signals])

    def test_rising_revenue_leaves_the_claim_alone(self):
        signals = score_signals(context(quarters=quarters(revenue=(400, 300, 200, 100))),
                                [fact("use_of_funds", "core_expansion")])
        self.assertNotIn("expansion_contradiction", [signal.code for signal in signals])

    def test_one_flat_quarter_breaks_the_decline(self):
        signals = score_signals(context(quarters=quarters(revenue=(100, 200, 200, 400))),
                                [fact("use_of_funds", "core_expansion")])
        self.assertNotIn("expansion_contradiction", [signal.code for signal in signals])

    def test_no_expansion_claim_means_no_contradiction(self):
        signals = score_signals(context(quarters=quarters(revenue=(100, 200, 300, 400))),
                                [fact("use_of_funds", "working_capital")])
        self.assertNotIn("expansion_contradiction", [signal.code for signal in signals])


class SummaryTests(unittest.TestCase):
    """Satu paragraf agar pembaca menangkap intinya tanpa membaca daftar sinyal."""

    def signals(self):
        return [Signal("growth", 2, "Dana untuk ekspansi bisnis inti [E002]", "F01", "funds_core"),
                Signal("red", 2, "Ada penyuntikan aset dari pihak terkait [E002]", "F05", "asset_injection")]

    def test_both_sides_are_stated_in_one_paragraph(self):
        text = compose_summary("MGLV", ActionBucket.rights_issue, self.signals(), VerdictLabel.inconclusive)
        self.assertIn("MGLV mengumumkan rights issue", text)
        self.assertIn("dana untuk ekspansi bisnis inti", text)
        self.assertIn("di sisi lain ada penyuntikan aset", text)
        self.assertIn("perlu diperiksa manusia", text)
        self.assertEqual(text.count("\n"), 0)

    def test_evidence_markers_are_stripped_from_prose(self):
        self.assertNotIn("[E002]", compose_summary("MGLV", ActionBucket.rights_issue, self.signals(),
                                                   VerdictLabel.inconclusive))

    def test_it_always_says_what_the_screening_is_not(self):
        for label in VerdictLabel:
            text = compose_summary("APEX", ActionBucket.control_change, self.signals(), label)
            self.assertIn("bukan penilaian atas sahamnya", text)

    def test_no_verified_fact_is_stated_plainly_rather_than_dressed_up(self):
        text = compose_summary("LAPD", ActionBucket.rights_issue, [], VerdictLabel.inconclusive)
        self.assertIn("Belum ada fakta yang bisa diverifikasi", text)

    def test_a_missing_bucket_still_reads_as_a_sentence(self):
        self.assertIn("mengumumkan aksi korporasi", compose_summary("XXXX", None, [], VerdictLabel.inconclusive))

    def test_repeated_reasons_are_not_repeated_in_the_prose(self):
        duplicated = [Signal("growth", 2, "Dana untuk modal kerja [E002]", "F01", "a"),
                      Signal("growth", 1, "Dana untuk modal kerja [E007]", "F02", "b")]
        text = compose_summary("MGLV", ActionBucket.rights_issue, duplicated, VerdictLabel.inconclusive)
        self.assertEqual(text.count("dana untuk modal kerja"), 1)

    def test_the_summary_never_contains_transaction_language(self):
        """Dirangkai Python, jadi seharusnya selalu lolos gate-nya sendiri."""
        from app.pipeline.gate import apply_gate
        from app.pipeline.schema import GateStatus, Verdict
        for label in VerdictLabel:
            text = compose_summary("MGLV", ActionBucket.rights_issue, self.signals(), label)
            verdict = Verdict(label=label, confidence=0.0, provider="test", summary=text)
            self.assertEqual(apply_gate(verdict).status, GateStatus.passed, text)


class PeerValuationTests(unittest.TestCase):
    """EXTREME_PBV=20 adalah ambang buta: PB wajar bank 2026 ada di 0,8x, jadi tidak pernah menyala."""

    def test_expensive_against_its_own_subsector_is_flagged_even_when_far_below_twenty(self):
        signals = market_signals(context(pb_ratio=4.2, subsector_pb=0.8, subsector_slug="banks"))
        self.assertEqual([signal.code for signal in signals], ["valuation_gap"])
        self.assertIn("5.2x median subsektor (banks) 0.80x", signals[0].reason)

    def test_in_line_with_peers_stays_silent_even_at_a_high_absolute_pbv(self):
        self.assertEqual(market_signals(context(pb_ratio=25.0, subsector_pb=12.0)), [])

    def test_the_absolute_threshold_still_covers_a_missing_peer_figure(self):
        signals = market_signals(context(pb_ratio=256.5))
        self.assertEqual([signal.code for signal in signals], ["valuation_gap"])
        self.assertIn("di atas 20x", signals[0].reason)

    def test_a_sane_valuation_without_peers_stays_silent(self):
        self.assertEqual(market_signals(context(pb_ratio=2.5)), [])

    def test_a_zero_or_missing_peer_figure_falls_back_instead_of_dividing_by_zero(self):
        self.assertEqual(market_signals(context(pb_ratio=2.5, subsector_pb=0.0)), [])
        self.assertEqual([s.code for s in market_signals(context(pb_ratio=99.0, subsector_pb=0.0))],
                         ["valuation_gap"])

    def test_peer_valuation_alone_is_not_enough_to_label(self):
        label, confidence = decide(market_signals(context(pb_ratio=4.2, subsector_pb=0.8)))
        self.assertEqual((label, confidence), (VerdictLabel.inconclusive, 0.0))


class DebtOnlyTests(unittest.TestCase):
    """`debt_repayment` sudah lama diekstrak model tetapi tidak punya aturan skor sama sekali."""

    def test_funds_only_for_debt_is_a_red_signal(self):
        signals = debt_only_signals([fact("use_of_funds", "debt_repayment")])
        self.assertEqual([(s.side, s.weight, s.code) for s in signals], [("red", 2, "funds_debt_only")])

    def test_repaying_debt_while_expanding_is_a_different_story(self):
        facts = [fact("use_of_funds", "debt_repayment"), fact("use_of_funds", "core_expansion")]
        self.assertEqual(debt_only_signals(facts), [])

    def test_working_capital_alongside_debt_also_breaks_exclusivity(self):
        facts = [fact("use_of_funds", "debt_repayment"), fact("use_of_funds", "working_capital")]
        self.assertEqual(debt_only_signals(facts), [])

    def test_no_debt_repayment_means_no_signal(self):
        self.assertEqual(debt_only_signals([fact("use_of_funds", "core_expansion")]), [])

    def test_a_new_business_use_does_not_count_as_building_the_core(self):
        """Dana ke bisnis baru tidak menambah kemampuan bisnis berjalan menghasilkan kas."""
        facts = [fact("use_of_funds", "debt_repayment"), fact("use_of_funds", "new_business")]
        self.assertEqual([s.code for s in debt_only_signals(facts)], ["funds_debt_only"])


class ControllerExitTests(unittest.TestCase):
    """Pengendali melepas posisi justru ketika dana publik diminta masuk."""

    def sale(self, percent, holder="insider", direction="sell"):
        return {"transaction_type": direction, "holder_type": holder,
                "share_percentage_transaction": percent}

    def test_a_meaningful_insider_sale_during_a_raise_is_flagged(self):
        signals = market_signals(context(insider_sales=(self.sale(3.4),)))
        self.assertEqual([(s.side, s.weight, s.code) for s in signals], [("red", 2, "controller_exit")])
        self.assertIn("3.40%", signals[0].reason)

    def test_small_sales_below_the_threshold_stay_silent(self):
        self.assertEqual(market_signals(context(insider_sales=(self.sale(0.2),))), [])

    def test_sales_are_added_up_across_filings(self):
        sales = (self.sale(0.6), self.sale(0.6))
        self.assertEqual([s.code for s in market_signals(context(insider_sales=sales))], ["controller_exit"])

    def test_purchases_are_not_counted_as_exits(self):
        self.assertEqual(market_signals(context(insider_sales=(self.sale(5.0, direction="buy"),))), [])

    def test_ordinary_institutions_are_not_treated_as_controllers(self):
        self.assertEqual(market_signals(context(insider_sales=(self.sale(5.0, holder="institution"),))), [])

    def test_outside_a_capital_raise_it_is_not_this_pattern(self):
        quiet = context(ActionBucket.general_action, insider_sales=(self.sale(5.0),))
        self.assertEqual(market_signals(quiet), [])

    def test_market_signals_still_cannot_label_without_a_verified_fact(self):
        loud = context(pb_ratio=256.5, free_float=0.05, insider_sales=(self.sale(9.0),))
        self.assertEqual(decide(market_signals(loud)), (VerdictLabel.inconclusive, 0.0))


class MarketContextTests(unittest.TestCase):
    def test_reads_the_snapshot_fields(self):
        snapshot = CompanySnapshot(ticker="APEX", company_name="PT Apex", free_float=0.07,
                                   free_float_rank=2, free_float_universe=30, subsector_pb=0.8,
                                   subsector_slug="energy",
                                   quarterly_financials=[{"date": "2026-06-30", "operating_cash_flow": -1}])
        built = MarketContext.from_snapshot(snapshot, 3.2, ActionBucket.rights_issue)
        self.assertEqual((built.free_float, built.free_float_rank, built.free_float_universe), (0.07, 2, 30))
        self.assertEqual((built.subsector_pb, built.subsector_slug), (0.8, "energy"))
        self.assertEqual(built.pb_ratio, 3.2)
        self.assertEqual(len(built.quarters), 1)

    def test_a_missing_snapshot_still_carries_pb_and_bucket(self):
        built = MarketContext.from_snapshot(None, 21.0, ActionBucket.rights_issue)
        self.assertEqual([signal.source for signal in market_signals(built)], ["sectors"])

    def test_market_signals_combine_and_can_reach_a_red_flag_with_facts(self):
        built = context(free_float=0.05, quarters=quarters(ocf=(-5, -4, -3, -2)))
        signals = score_signals(built, [fact("business_change", "present"), fact("asset_injection", "present")])
        label, _ = decide(signals)
        self.assertEqual(label, VerdictLabel.structural_red_flag)

    def test_market_reds_block_a_growth_label(self):
        facts = [fact("counterparty", "affiliate", "PT A"), fact("use_of_funds", "core_expansion")]
        self.assertEqual(decide(score_signals(context(), facts))[0], VerdictLabel.growth_catalyst)
        blocked = context(free_float=0.05, quarters=quarters(ocf=(-5, -4, -3, -2)))
        self.assertEqual(decide(score_signals(blocked, facts))[0], VerdictLabel.inconclusive)


if __name__ == "__main__":
    unittest.main()

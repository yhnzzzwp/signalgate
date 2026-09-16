import unittest

from app.pipeline.schema import ActionBucket, CompanySnapshot, VerdictLabel
from app.research.models import Fact
from app.research.scoring import (
    MarketContext,
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


class MarketContextTests(unittest.TestCase):
    def test_reads_the_snapshot_fields(self):
        snapshot = CompanySnapshot(ticker="APEX", company_name="PT Apex", free_float=0.07,
                                   free_float_rank=2, free_float_universe=30,
                                   quarterly_financials=[{"date": "2026-06-30", "operating_cash_flow": -1}])
        built = MarketContext.from_snapshot(snapshot, 3.2, ActionBucket.rights_issue)
        self.assertEqual((built.free_float, built.free_float_rank, built.free_float_universe), (0.07, 2, 30))
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

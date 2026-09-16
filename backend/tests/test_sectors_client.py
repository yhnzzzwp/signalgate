import os
import unittest
from unittest.mock import patch

from app.pipeline.sense import FreeFloatLookup, fetch_quarterly_financials, normalize_ticker, snapshot_company
from app.sectors.client import REPORT_SECTIONS, SectorsAPIError, SectorsClient


class FakeClient:
    """Records every call so tests can assert on credit-bearing requests."""

    def __init__(self, **responses):
        self.responses = responses
        self.calls = []

    def _reply(self, name):
        value = self.responses.get(name, [])
        if isinstance(value, Exception):
            raise value
        return value

    def free_float(self, sector=None, sub_sector=None, industry=None, sub_industry=None):
        self.calls.append(("free_float", sub_sector))
        return self._reply(f"free_float:{sub_sector}")

    def quarterly_financials(self, ticker, n_quarters=4):
        self.calls.append(("quarterly", ticker, n_quarters))
        return self._reply("quarterly")

    def company_report(self, ticker, sections=None):
        self.calls.append(("report", ticker, sections))
        return self._reply("report")


class SectorsClientTests(unittest.TestCase):
    @patch.dict(os.environ, {"SECTORS_API_KEY": "test-key"}, clear=True)
    def test_uses_environment_key(self):
        self.assertEqual(SectorsClient().api_key, "test-key")

    @patch.dict(os.environ, {}, clear=True)
    def test_requires_api_key(self):
        with self.assertRaises(ValueError):
            SectorsClient()

    @patch.dict(os.environ, {"SECTORS_API_KEY": "test-key"}, clear=True)
    def test_company_report_requests_only_the_sections_the_pipeline_reads(self):
        client = SectorsClient()
        seen = {}
        client.get = lambda path, **params: seen.update({"path": path, **params}) or {"company_name": "PT A"}
        client.company_report("BBCA")
        self.assertEqual(seen["sections"], ",".join(REPORT_SECTIONS))
        self.assertNotIn("peers", seen["sections"])  # eight sections would bill eight credits

    @patch.dict(os.environ, {"SECTORS_API_KEY": "test-key"}, clear=True)
    def test_free_float_and_quarterly_hit_the_documented_paths(self):
        client = SectorsClient()
        seen = []
        client.get = lambda path, **params: seen.append((path, params)) or []
        client.free_float(sub_sector="banks")
        client.quarterly_financials("APEX", n_quarters=4)
        self.assertEqual(seen[0][0], "free-float")
        self.assertEqual(seen[0][1]["sub_sector"], "banks")
        self.assertEqual(seen[1], ("financials/quarterly/APEX", {"n_quarters": 4}))


class FreeFloatLookupTests(unittest.TestCase):
    ROWS = [
        {"symbol": "BCIC.JK", "free_float": 0.0749},
        {"symbol": "NISP.JK", "free_float": 0.076},
        {"symbol": "INPC.JK", "free_float": 0.591},
    ]

    def test_finds_the_symbol_and_ranks_it_from_the_thinnest_float(self):
        lookup = FreeFloatLookup(FakeClient(**{"free_float:banks": self.ROWS}))
        self.assertEqual(lookup.find("BCIC", ["banks"]), (0.0749, 1, 3))
        self.assertEqual(lookup.find("INPC", ["banks"]), (0.591, 3, 3))

    def test_matches_regardless_of_the_jk_suffix_or_case(self):
        lookup = FreeFloatLookup(FakeClient(**{"free_float:banks": self.ROWS}))
        self.assertEqual(lookup.find("nisp.jk", ["banks"])[0], 0.076)

    def test_one_call_per_subsector_no_matter_how_many_tickers(self):
        client = FakeClient(**{"free_float:banks": self.ROWS})
        lookup = FreeFloatLookup(client)
        lookup.find("BCIC", ["banks"])
        lookup.find("NISP", ["banks"])
        lookup.find("MISSING", ["banks"])
        self.assertEqual(client.calls, [("free_float", "banks")])

    def test_an_unknown_ticker_returns_nothing(self):
        lookup = FreeFloatLookup(FakeClient(**{"free_float:banks": self.ROWS}))
        self.assertEqual(lookup.find("TLKM", ["banks"]), (None, None, None))

    def test_no_subsector_means_no_call_at_all(self):
        client = FakeClient()
        self.assertEqual(FreeFloatLookup(client).find("TLKM", []), (None, None, None))
        self.assertEqual(client.calls, [])

    def test_an_api_failure_degrades_to_no_signal(self):
        client = FakeClient(**{"free_float:banks": SectorsAPIError("HTTP 500")})
        self.assertEqual(FreeFloatLookup(client).find("BCIC", ["banks"]), (None, None, None))

    def test_rows_without_a_float_value_are_skipped(self):
        rows = [{"symbol": "AAAA.JK", "free_float": None}, *self.ROWS]
        lookup = FreeFloatLookup(FakeClient(**{"free_float:banks": rows}))
        self.assertEqual(lookup.find("BCIC", ["banks"]), (0.0749, 1, 3))


class QuarterlyFetchTests(unittest.TestCase):
    def test_orders_newest_quarter_first(self):
        client = FakeClient(quarterly=[{"date": "2025-12-31"}, {"date": "2026-06-30"}, {"date": "2026-03-31"}])
        dates = [row["date"] for row in fetch_quarterly_financials(client, "APEX")]
        self.assertEqual(dates, ["2026-06-30", "2026-03-31", "2025-12-31"])

    def test_an_api_failure_degrades_to_an_empty_window(self):
        self.assertEqual(fetch_quarterly_financials(FakeClient(quarterly=SectorsAPIError("HTTP 402")), "APEX"), [])

    def test_window_size_is_passed_through_because_each_quarter_costs_a_credit(self):
        client = FakeClient(quarterly=[])
        fetch_quarterly_financials(client, "APEX", n_quarters=2)
        self.assertEqual(client.calls, [("quarterly", "APEX", 2)])


class SnapshotCompanyTests(unittest.TestCase):
    REPORT = {
        "company_name": "PT Apex Tbk",
        "overview": {"industry": "Energy", "market_cap": 1_000},
        "valuation": {"historical_valuation": [{"pb": 2.5}], "forward_pe": 8.0},
        "ownership": {"major_shareholders": [{"name": "PT Induk", "share_percentage": "0.9"}]},
    }

    def test_attaches_float_and_quarterly_data_to_the_snapshot(self):
        client = FakeClient(report=self.REPORT, quarterly=[{"date": "2026-06-30", "operating_cash_flow": -5}],
                            **{"free_float:energy": [{"symbol": "APEX.JK", "free_float": 0.09}]})
        snapshot = snapshot_company(client, "APEX", ["energy"], FreeFloatLookup(client))
        self.assertEqual(snapshot.free_float, 0.09)
        self.assertEqual((snapshot.free_float_rank, snapshot.free_float_universe), (1, 1))
        self.assertEqual(len(snapshot.quarterly_financials), 1)
        self.assertEqual(snapshot.pb_ratio, 2.5)

    def test_works_without_a_lookup_and_skips_quarterly_when_asked(self):
        client = FakeClient(report=self.REPORT)
        snapshot = snapshot_company(client, "APEX", n_quarters=0)
        self.assertIsNone(snapshot.free_float)
        self.assertEqual(snapshot.quarterly_financials, [])
        self.assertEqual([call[0] for call in client.calls], ["report"])

    def test_a_failed_report_still_returns_none(self):
        self.assertIsNone(snapshot_company(FakeClient(report=SectorsAPIError("HTTP 500")), "APEX"))


class NormalizeTickerTests(unittest.TestCase):
    def test_strips_suffix_and_case(self):
        self.assertEqual(normalize_ticker(" apex.jk "), "APEX")
        self.assertEqual(normalize_ticker(""), "")


if __name__ == "__main__":
    unittest.main()

"""Angka laporan harus cocok dengan perhitungan acuan yang diturunkan terpisah, bukan dengan dirinya sendiri."""
import math
from datetime import date

import pytest

from app.workflow import calculations as calc
from tests import workflow_fixtures as fx


def by_id(metrics):
    return {metric["metric_id"]: metric for metric in metrics}


# ---- Indikator: acuan analitis / aritmetika manual ----------------------------------------------

def test_rsi_matches_hand_derived_wilder_steps():
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    # 14 perubahan: gain total 3,34; loss total 1,40 (dijumlah manual dari deret di atas).
    first = 100 - 100 / (1 + (3.34 / 14) / (1.40 / 14))
    assert calc.rsi_wilder(closes) == pytest.approx(first, abs=1e-6)
    # Langkah Wilder berikutnya dengan close 46,00 (perubahan -0,28).
    gain, loss = (3.34 / 14 * 13 + 0) / 14, (1.40 / 14 * 13 + 0.28) / 14
    assert calc.rsi_wilder(closes + [46.00]) == pytest.approx(100 - 100 / (1 + gain / loss), abs=1e-6)


def test_rsi_edges():
    assert calc.rsi_wilder([1.0] * 5) is None
    assert calc.rsi_wilder([float(i) for i in range(1, 40)]) == 100.0
    assert calc.rsi_wilder([5.0] * 40) == 50.0


def test_ema_of_a_linear_series_has_the_analytic_lag_so_macd_is_constant():
    closes = [float(i) for i in range(1, 81)]
    # EMA_p pada deret linear kemiringan 1 tertinggal (p-1)/2: EMA12 = x-5,5 dan EMA26 = x-12,5.
    line, signal, histogram = calc.macd(closes)
    assert line == pytest.approx(7.0)
    assert signal == pytest.approx(7.0)
    assert histogram == pytest.approx(0.0, abs=1e-9)
    assert calc.ema_series(closes, 12)[-1] == pytest.approx(80 - 5.5)


def test_atr_of_constant_true_range():
    bars = [{"high": 11.0, "low": 9.0, "close": 10.0} for _ in range(40)]
    assert calc.atr_wilder(bars) == pytest.approx(2.0)


def test_constant_growth_has_zero_volatility_and_exact_return():
    rows = [{"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "open": 100 * 1.01 ** i, "high": 100 * 1.01 ** i,
             "low": 100 * 1.01 ** i, "close": 100 * 1.01 ** i, "volume": 10} for i in range(70)]
    bars, quality = calc.clean_daily(rows, date(2026, 12, 31))
    metrics, _missing, _limits, _info = calc.technical_metrics(bars, quality, [], ["daily:1"], None)
    values = by_id(metrics)
    assert values["technical:return_20"]["value"] == pytest.approx(1.01 ** 20 - 1)
    assert values["technical:volatility_20"]["value"] == pytest.approx(0.0, abs=1e-9)
    assert values["technical:volume_ratio_20"]["value"] == pytest.approx(1.0)


# ---- Kualitas data harian ----------------------------------------------------------------------

def test_clean_daily_never_fills_or_keeps_bad_rows_silently():
    rows = [
        {"date": "2026-09-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 5},
        {"date": "2026-09-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 5},   # duplikat identik
        {"date": "2026-09-02", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 5},
        {"date": "2026-09-02", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 5},   # duplikat berbeda
        {"date": "2026-09-03", "open": 10, "high": 9, "low": 11, "close": 10, "volume": 5},   # high < low
        {"date": "2026-09-04", "open": 10, "high": 11, "low": 9, "close": None, "volume": 5}, # kosong
        {"date": "2026-09-05", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 0},   # volume nol tetap
        {"date": "2026-09-30", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 5},   # setelah as_of
    ]
    bars, quality = calc.clean_daily(rows, date(2026, 9, 20))
    assert [bar["date"].isoformat() for bar in bars] == ["2026-09-01", "2026-09-02", "2026-09-05"]
    assert (quality["duplicates"], quality["conflicting_duplicates"], quality["invalid"], quality["after_as_of"]) == (2, 1, 2, 1)
    assert quality["zero_volume_days"] == 1
    assert quality["stale"] is True and quality["stale_days"] == 15


def test_a_split_inside_the_window_cuts_the_series_instead_of_mixing_price_bases():
    rows = fx.daily_rows(sessions=130)
    bars, quality = calc.clean_daily(rows, fx.AS_OF)
    split_day = bars[100]["date"].isoformat()
    breaks = calc.corporate_action_breaks(fx.corporate_actions(("stock_split", {"date": split_day, "split_ratio": 5})))
    metrics, missing, limitations, info = calc.technical_metrics(bars, quality, breaks, ["daily:1"], "actions:1")
    values = by_id(metrics)
    assert info["segment_start"] == split_day
    assert values["technical:sma50"]["status"] == "insufficient_data"  # 30 sesi setelah split
    assert values["technical:sma20"]["status"] == "ok"
    assert "actions:1" in values["technical:sma20"]["source_ids"]
    assert any("belum disesuaikan" in item for item in limitations)
    assert any("SMA50" in item for item in missing)


def test_an_unexplained_huge_jump_is_treated_as_a_price_basis_break():
    rows = fx.daily_rows(sessions=40)
    rows[-5]["close"] = rows[-5]["open"] = rows[-6]["close"] * 0.2
    rows[-5]["high"], rows[-5]["low"] = rows[-5]["close"] + 1, rows[-5]["close"] - 1
    bars, _quality = calc.clean_daily(rows, fx.AS_OF)
    assert [item["type"] for item in calc.suspected_breaks(bars)] == ["suspected_unadjusted_jump"] * 2


def test_indicators_report_insufficient_history_instead_of_a_number():
    bars, quality = calc.clean_daily(fx.daily_rows(sessions=25), fx.AS_OF)
    metrics, missing, _limitations, _info = calc.technical_metrics(bars, quality, [], ["daily:1"], None)
    values = by_id(metrics)
    assert values["technical:rsi14"]["value"] is None and values["technical:rsi14"]["status"] == "insufficient_data"
    assert values["technical:macd_histogram"]["value"] is None
    assert values["technical:sma20"]["value"] is not None
    assert any("RSI14" in item for item in missing)


def test_daily_windows_respect_the_silent_90_day_clamp():
    assert calc.daily_windows(date(2026, 9, 18), 180) == [("2026-06-21", "2026-09-18"), ("2026-03-23", "2026-06-20")]
    for start, end in calc.daily_windows(date(2026, 9, 18), 400):
        assert (date.fromisoformat(end) - date.fromisoformat(start)).days <= 89


# ---- Fundamental -------------------------------------------------------------------------------

def test_fundamental_growth_compares_the_same_quarter_and_ttm_needs_consecutive_quarters():
    metrics, missing, limitations = calc.fundamental_metrics(fx.company_report(), fx.quarterly(), "report:1", "quarterly:1")
    values = by_id(metrics)
    assert values["fundamental:revenue_growth_yoy"]["value"] == pytest.approx(1_200 / 1_000 - 1)
    assert values["fundamental:revenue_growth_yoy"]["period"] == "Q2 2026 vs Q2 2025"
    assert values["fundamental:earnings_growth_yoy"]["value"] == pytest.approx(120 / 100 - 1)
    assert values["fundamental:net_margin_latest"]["value"] == pytest.approx(0.1)
    assert values["fundamental:revenue_ttm"]["value"] == pytest.approx((1_200 + 1_100 + 1_050 + 1_000) * 1e9)
    assert values["fundamental:earnings_ttm"]["period"] == "TTM Q3 2025–Q2 2026"
    assert values["fundamental:liabilities_to_equity"]["value"] == pytest.approx(1.5)
    assert values["fundamental:revenue_growth_fy"]["value"] == pytest.approx(4_140 / 3_600 - 1)
    assert not missing
    assert any("tanggal publikasi" in item for item in limitations)


def test_a_gap_in_quarters_blocks_ttm_and_a_negative_base_blocks_growth():
    rows = [fx.quarter("2026-06-30", 1_200e9, 50e9), fx.quarter("2025-12-31", 1_050e9, 85e9),
            fx.quarter("2025-09-30", 1_000e9, 80e9), fx.quarter("2025-06-30", 1_000e9, -20e9)]
    metrics, missing, _limits = calc.fundamental_metrics(None, rows, None, "quarterly:1")
    values = by_id(metrics)
    assert values["fundamental:revenue_ttm"]["value"] is None
    assert values["fundamental:earnings_growth_yoy"]["status"] == "not_meaningful"
    assert any("Empat kuartal berurutan" in item for item in missing)


def test_negative_equity_makes_leverage_not_meaningful():
    rows = [fx.quarter("2026-06-30", 10e9, -1e9, equity=-7e9, liabilities=833e9)]
    values = by_id(calc.fundamental_metrics(None, rows, None, "q")[0])
    assert values["fundamental:liabilities_to_equity"]["status"] == "not_meaningful"


def test_financial_companies_get_sector_metrics_not_industrial_ratios():
    metrics_now = {"net_interest_income": 21e12, "net_loan": 940e12, "total_deposit": 1_276e12}
    metrics_prior = {"net_interest_income": 20e12, "net_loan": 900e12, "total_deposit": 1_200e12}
    rows = [fx.quarter("2026-06-30", 28e12, 14e12, sector_metrics=metrics_now),
            fx.quarter("2025-06-30", 26e12, 13e12, sector_metrics=metrics_prior)]
    values = by_id(calc.fundamental_metrics(fx.company_report(sector="Financials"), rows, "r", "q")[0])
    assert "fundamental:net_margin_latest" not in values and "fundamental:liabilities_to_equity" not in values
    assert values["fundamental:net_interest_income_growth_yoy"]["value"] == pytest.approx(21 / 20 - 1)
    assert values["fundamental:loan_to_deposit"]["value"] == pytest.approx(940 / 1_276)


# ---- Valuation ---------------------------------------------------------------------------------

def valuation(report=None, quarterly=None, point_in_time=True):
    report = report or fx.company_report()
    fundamental, _m, _l = calc.fundamental_metrics(report, quarterly or fx.quarterly(), "report:1", "quarterly:1")
    return calc.valuation_metrics(report, fundamental, "report:1", "quarterly:1", fx.TICKER, point_in_time)


def test_pe_is_recomputed_and_peer_median_ignores_negative_pe():
    metrics, missing, limitations, conflicts = valuation()
    values = by_id(metrics)
    earnings_ttm = (120 + 90 + 85 + 80) * 1e9
    assert values["valuation:pe_ttm_calc"]["value"] == pytest.approx(6_000e9 / earnings_ttm)
    # Peer sub_sector ber-PE positif: 8, 12, 20 (CCCC -5 dikecualikan; EEEE hanya grup sector).
    assert values["valuation:peer_pe_median"]["value"] == pytest.approx(12.0)
    assert values["valuation:pe_vs_peer_median"]["value"] == pytest.approx((6_000e9 / earnings_ttm) / 12.0 - 1)
    assert values["valuation:pb_mrq_calc"]["value"] == pytest.approx(6_000e9 / 4_000e9)
    # PE historis positif: 3,5; 5; 10; 15 (2024 negatif dikecualikan).
    assert values["valuation:pe_history_median"]["value"] == pytest.approx(7.5)
    assert any("dikecualikan dari median" in item for item in limitations)
    assert any("target harga" in item for item in limitations)
    assert conflicts == []


def test_a_large_gap_between_own_and_sectors_pe_is_kept_as_a_conflict():
    report = fx.company_report()
    report["peers"][0]["peers_data"]["companies"][0]["pe_ttm"] = 40.0
    _metrics, _missing, _limitations, conflicts = valuation(report)
    assert len(conflicts) == 1 and "PE TTM" in conflicts[0]


def test_historical_as_of_refuses_a_live_company_report():
    metrics, missing, _limitations, _conflicts = valuation(point_in_time=False)
    assert metrics == []
    assert "snapshot terkini" in missing[0]


def test_too_few_positive_peers_is_explicit():
    report = fx.company_report()
    report["peers"][0]["peers_data"]["companies"] = report["peers"][0]["peers_data"]["companies"][:2]
    metrics, missing, _l, _c = valuation(report)
    assert "valuation:peer_pe_median" not in by_id(metrics)
    assert any("kurang dari 3" in item for item in missing)


def test_nan_never_leaks_as_a_number():
    metric = calc._metric("x", "technical", "x", math.nan, "x", "f", "p", [])
    assert metric["value"] is None and metric["status"] == "not_meaningful"


# ---- Pemeriksaan angka pada klaim ---------------------------------------------------------------

def claim_metrics():
    metrics, _m, _l = calc.fundamental_metrics(fx.company_report(), fx.quarterly(), "report:1", "quarterly:1")
    valuation, _m2, _l2, _c = calc.valuation_metrics(fx.company_report(), metrics, "report:1", "quarterly:1",
                                                     fx.TICKER, True)
    return {metric["metric_id"]: metric for metric in metrics + valuation}


def check(statement, metric_ids, **extra):
    from app.workflow.evidence import check_claim
    claim = {"claim_id": "c1", "domain": "valuation", "kind": "interpretation", "statement": statement,
             "metric_ids": list(metric_ids), "source_ids": [], "limitations": [], "assumptions": [], **extra}
    sources = {"report:1": {"source_id": "report:1", "ticker": fx.TICKER, "status": "ok"},
               "quarterly:1": {"source_id": "quarterly:1", "ticker": fx.TICKER, "status": "ok"}}
    return check_claim(claim, claim_metrics(), sources, {}, fx.TICKER, fx.AS_OF)


def test_a_claim_may_cite_the_inputs_of_the_metric_it_references():
    """Angka pembanding milik metrik turunan tetap tertelusur lewat `input_metric_ids`."""
    issues = check("PE TTM 16,00x berada 33,3% di atas median PE peer 12,00x.", ["valuation:pe_vs_peer_median"])
    assert issues == []


def test_a_number_from_an_unreferenced_metric_is_still_rejected():
    issues = check("PE TTM 16,00x, sementara PB MRQ 1,50x.", ["valuation:pe_ttm_calc"])
    assert issues and "1,50" in issues[0]


def test_currency_and_percent_formats_are_understood():
    assert check("Pendapatan TTM Rp4,35 triliun.", ["fundamental:revenue_ttm"]) == []
    assert check("Pendapatan TTM Rp4 triliun.", ["fundamental:revenue_ttm"]) == []
    assert check("Pendapatan tumbuh 20,0% YoY.", ["fundamental:revenue_growth_yoy"]) == []
    assert check("Pendapatan tumbuh 47,5% YoY.", ["fundamental:revenue_growth_yoy"]) != []


def test_input_metrics_are_collected_once_and_ignore_unknown_ids():
    from app.workflow.evidence import with_input_metrics
    metrics = claim_metrics()
    collected = with_input_metrics(["valuation:pe_vs_peer_median", "valuation:peer_pe_median", "tidak:ada"], metrics)
    ids = [metric["metric_id"] for metric in collected]
    assert ids.count("valuation:peer_pe_median") == 1
    assert "valuation:pe_ttm_calc" in ids  # masukan dari metrik selisih

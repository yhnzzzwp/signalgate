"""Fixture sintetis berbentuk respons Sectors nyata (kunci dan struktur disalin, angkanya karangan).

Respons API asli tidak disimpan di repo publik. Bentuknya diambil dari respons company report yang
pernah diterima 16 Sep 2026 dan contoh dokumentasi quarterly/daily/news/corporate-actions.
"""
from __future__ import annotations

from datetime import date, timedelta

TICKER = "TEST"
AS_OF = date(2026, 9, 18)


def quarter(day: str, revenue, earnings, *, equity=4_000e9, liabilities=6_000e9, debt=2_000e9, ocf=150e9,
            sector_metrics=None) -> dict:
    return {"symbol": f"{TICKER}.JK", "date": day, "revenue": revenue, "earnings": earnings,
            "total_assets": equity + liabilities, "total_equity": equity, "total_liabilities": liabilities,
            "total_debt": debt, "operating_cash_flow": ocf, "investing_cash_flow": -50e9, "financing_cash_flow": -20e9,
            "net_cash_flow": 80e9, "free_cash_flow": 100e9, "financials_sector_metrics": sector_metrics}


def quarterly() -> list[dict]:
    # Sengaja tidak urut: klien harus mengurutkan sendiri.
    return [quarter("2026-03-31", 1_100e9, 90e9), quarter("2026-06-30", 1_200e9, 120e9),
            quarter("2025-09-30", 1_000e9, 80e9), quarter("2025-12-31", 1_050e9, 85e9),
            quarter("2025-06-30", 1_000e9, 100e9)]


def peer(symbol, pe, pb, groups=("sub_sector", "sector", "industry", "sub_industry"), year=2025):
    return {"year": year, "group": list(groups), "pb_mrq": pb, "pe_ttm": pe, "symbol": f"{symbol}.JK",
            "market_cap": 1e12, "net_income": 1e11, "company_name": f"PT {symbol} Tbk", "total_equity": 1e12}


def company_report(*, sector="Consumer Cyclicals", market_cap=6_000e9) -> dict:
    return {
        "symbol": f"{TICKER}.JK", "company_name": "PT Test Abadi Tbk",
        "overview": {"sector": sector, "sub_sector": "Apparel & Luxury Goods", "industry": "Apparel & Luxury Goods",
                     "market_cap": market_cap, "last_close_price": 1_500, "latest_close_date": "2026-09-17"},
        "valuation": {"last_close_price": 1_500, "latest_close_date": "2026-09-17", "forward_pe": None,
                      "intrinsic_value": -1019,
                      "historical_valuation": [
                          {"year": 2022, "pe": 3.5, "pb": 0.6, "pe_peer_avg": -0.48, "pb_peer_avg": 0.65},
                          {"year": 2023, "pe": 5.0, "pb": 0.8, "pe_peer_avg": -1.1, "pb_peer_avg": 0.7},
                          {"year": 2024, "pe": -2.0, "pb": 0.7, "pe_peer_avg": 0.1, "pb_peer_avg": 0.6},
                          {"year": 2025, "pe": 10.0, "pb": 3.0, "pe_peer_avg": 5.1, "pb_peer_avg": 0.75},
                          {"year": 2026, "pe": 15.0, "pb": 1.5, "pe_peer_avg": 3.5, "pb_peer_avg": 1.0}]},
        "financials": {"historical_financials": [{"year": 2024, "revenue": 3_600e9, "earnings": 300e9},
                                                 {"year": 2025, "revenue": 4_140e9, "earnings": 360e9}],
                       "yoy_quarter_revenue_growth": 0.2},
        "peers": [{"peers_data": {
            "group_name": {"sector": "Consumer Cyclicals", "sub_sector": "Apparel & Luxury Goods"},
            "companies": [peer(TICKER, 14.0, 1.4, groups=("self",)),
                          peer("AAAA", 8.0, 1.0), peer("BBBB", 12.0, 2.0), peer("CCCC", -5.0, 0.5),
                          peer("DDDD", 20.0, None), peer("EEEE", 6.0, 0.8, groups=("sector",))]}}],
    }


def daily_rows(sessions=130, start=100.0, step=1.0, last_day=date(2026, 9, 17)) -> list[dict]:
    """Seri harga naik linear pada hari kerja, terbaru di akhir, dengan volume konstan."""
    days, day = [], last_day
    while len(days) < sessions:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    rows = []
    for index, day in enumerate(reversed(days)):
        close = start + step * index
        rows.append({"symbol": f"{TICKER}.JK", "date": day.isoformat(), "open": close, "high": close + 1,
                     "low": close - 1, "close": close, "volume": 1_000_000, "market_cap": close * 4e9})
    return rows


def corporate_actions(*events) -> dict:
    actions = {"agm": None, "bonus": None, "warrant": None, "dividend": None, "right_issue": None, "stock_split": None}
    for kind, entry in events:
        actions[kind] = (actions[kind] or []) + [entry]
    return {"symbol": f"{TICKER}.JK", "corporate_actions": actions}


def news(*items) -> dict:
    results = [{"title": title, "body": body, "source": url, "timestamp": stamp, "sector": "consumer-cyclicals",
                "sub_sector": ["apparel-luxury-goods"], "tags": [], "symbols": [f"{TICKER}.JK"]}
               for title, body, url, stamp in items]
    return {"results": results, "pagination": {"total_count": len(results), "has_next": False}}

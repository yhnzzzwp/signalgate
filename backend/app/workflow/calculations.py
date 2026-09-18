"""Perhitungan deterministik untuk panel fundamental, valuation, dan technical.

Model tidak pernah menghitung. Setiap angka yang tampil di laporan lahir di sini lengkap dengan
formula, periode, unit, dan ID sumber; model hanya boleh menafsirkannya. Data kosong bukan nol:
denominator yang tidak ada atau tidak positif menghasilkan status eksplisit, bukan angka.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import median

CALC_VERSION = "calc-2026-09-18.1"

# Minimum observasi per indikator. Warm-up EMA/Wilder membuat nilai awal belum stabil, jadi batasnya
# sengaja di atas panjang jendela nominal.
MIN_OBS = {"return_20": 21, "return_60": 61, "sma20": 20, "sma50": 50, "rsi14": 30, "atr14": 30,
           "macd": 60, "volume_ratio_20": 21, "volatility_20": 21}
# Lompatan harga harian sebesar ini hampir mustahil dari perdagangan biasa di IDX (batas ARA/ARB), jadi
# dianggap tanda seri belum disesuaikan untuk aksi korporasi yang tidak tercatat.
SUSPECT_JUMP = 0.5
STALE_DAYS = 7
PEER_MINIMUM = 3
VALUATION_MISMATCH = 0.15


def _metric(metric_id, domain, name, value, unit, formula, period, source_ids, *, status="ok", note=None,
            price_basis=None, inputs=()):
    if value is not None and (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        value, status = None, "not_meaningful"
    return {"metric_id": metric_id, "domain": domain, "name": name,
            "value": None if value is None else round(float(value), 10), "unit": unit, "formula": formula,
            "period": period, "source_ids": list(source_ids), "input_metric_ids": list(inputs),
            "price_basis": price_basis, "status": "insufficient_data" if value is None and status == "ok" else status,
            "note": note}


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def quarter_label(day: date) -> str:
    return f"Q{(day.month - 1) // 3 + 1} {day.year}"


# ---------------------------------------------------------------------------------------------------
# Fundamental
# ---------------------------------------------------------------------------------------------------

def quarterly_series(rows) -> tuple[list[dict], list[str]]:
    """Kuartal terurut dari terbaru, satu baris per tanggal. Duplikat yang berbeda isi dicatat."""
    by_date: dict[date, dict] = {}
    problems = []
    for row in rows or []:
        day = _day((row or {}).get("date"))
        if day is None:
            continue
        if day in by_date and by_date[day] != row:
            problems.append(f"Dua baris kuartal berbeda untuk {day.isoformat()}; baris pertama dipakai.")
            continue
        by_date.setdefault(day, row)
    ordered = [dict(row, _day=day) for day, row in sorted(by_date.items(), reverse=True)]
    return ordered, problems


def _months_apart(later: date, earlier: date) -> int:
    return (later.year - earlier.year) * 12 + later.month - earlier.month


def _same_quarter_last_year(series, latest):
    return next((row for row in series[1:] if _months_apart(latest["_day"], row["_day"]) == 12), None)


def _trailing_four(series):
    window = series[:4]
    if len(window) < 4:
        return None
    if any(_months_apart(a["_day"], b["_day"]) != 3 for a, b in zip(window, window[1:])):
        return None
    return window


def _growth(current, previous):
    if current is None or previous is None:
        return None, "insufficient_data", None
    if previous <= 0:
        return None, "not_meaningful", "Basis periode pembanding nol atau negatif; persentase pertumbuhan menyesatkan."
    return current / previous - 1, "ok", None


def is_financial_company(report: dict, series: list[dict]) -> bool:
    sector = str(((report or {}).get("overview") or {}).get("sector") or "").lower()
    return sector == "financials" or any(row.get("financials_sector_metrics") for row in series)


def fundamental_metrics(report: dict | None, quarterly: list[dict] | None, report_source: str | None,
                        quarterly_source: str | None) -> tuple[list[dict], list[str], list[str]]:
    metrics, missing, limitations = [], [], []
    series, problems = quarterly_series(quarterly)
    limitations += problems
    q_src = [quarterly_source] if quarterly_source else []
    financial = is_financial_company(report or {}, series)
    if financial:
        limitations.append("Emiten sektor keuangan: margin laba dan rasio utang nonkeuangan tidak dipakai.")

    if not series:
        missing.append("Laporan keuangan kuartalan Sectors tidak tersedia.")
    else:
        latest = series[0]
        prior = _same_quarter_last_year(series, latest)
        label = quarter_label(latest["_day"])
        if prior is None:
            missing.append(f"Kuartal yang sama tahun sebelumnya untuk {label} tidak tersedia; pertumbuhan YoY tidak dihitung.")
        compare = f"{label} vs {quarter_label(prior['_day'])}" if prior else label
        for field, name in (("revenue", "Pertumbuhan pendapatan YoY"), ("earnings", "Pertumbuhan laba bersih YoY")):
            current = _number(latest.get(field))
            previous = _number(prior.get(field)) if prior else None
            value, status, note = _growth(current, previous)
            metrics.append(_metric(f"fundamental:{field}_growth_yoy", "fundamental", name, value, "ratio",
                                   f"{field}[{label}] / {field}[kuartal sama tahun lalu] - 1", compare, q_src,
                                   status=status, note=note))
        if not financial:
            for row, suffix in ((latest, "latest"), (prior, "prior_year")):
                if row is None:
                    continue
                revenue, earnings = _number(row.get("revenue")), _number(row.get("earnings"))
                value = earnings / revenue if revenue and revenue > 0 and earnings is not None else None
                metrics.append(_metric(f"fundamental:net_margin_{suffix}", "fundamental",
                                       f"Margin laba bersih {quarter_label(row['_day'])}", value, "ratio",
                                       "earnings / revenue (kuartal tunggal)", quarter_label(row["_day"]), q_src))
        window = _trailing_four(series)
        ttm_period = (f"TTM {quarter_label(window[-1]['_day'])}–{quarter_label(window[0]['_day'])}"
                      if window else "TTM")
        if window is None:
            missing.append("Empat kuartal berurutan tidak tersedia; nilai TTM tidak dihitung.")
        for field, name in (("revenue", "Pendapatan TTM"), ("earnings", "Laba bersih TTM"),
                            ("operating_cash_flow", "Arus kas operasi TTM")):
            values = [_number(row.get(field)) for row in window] if window else []
            total = sum(values) if values and all(v is not None for v in values) else None
            metrics.append(_metric(f"fundamental:{field}_ttm", "fundamental", name, total, "IDR",
                                   f"jumlah {field} empat kuartal berurutan terakhir", ttm_period, q_src))
        earnings_ttm = next((m["value"] for m in metrics if m["metric_id"] == "fundamental:earnings_ttm"), None)
        ocf_ttm = next((m["value"] for m in metrics if m["metric_id"] == "fundamental:operating_cash_flow_ttm"), None)
        if earnings_ttm is not None and ocf_ttm is not None:
            if earnings_ttm > 0:
                metrics.append(_metric("fundamental:cash_conversion_ttm", "fundamental", "Konversi kas (OCF/laba) TTM",
                                       ocf_ttm / earnings_ttm, "x", "operating_cash_flow_ttm / earnings_ttm", ttm_period,
                                       q_src, inputs=("fundamental:operating_cash_flow_ttm", "fundamental:earnings_ttm")))
            else:
                metrics.append(_metric("fundamental:cash_conversion_ttm", "fundamental", "Konversi kas (OCF/laba) TTM",
                                       None, "x", "operating_cash_flow_ttm / earnings_ttm", ttm_period, q_src,
                                       status="not_meaningful", note="Laba TTM nol atau negatif."))
        equity = _number(latest.get("total_equity"))
        metrics.append(_metric("fundamental:total_equity_latest", "fundamental", f"Total ekuitas {label}", equity, "IDR",
                               "total_equity kuartal terakhir", label, q_src))
        if not financial:
            for field, name, metric_id in (("total_liabilities", "Liabilitas terhadap ekuitas", "liabilities_to_equity"),
                                           ("total_debt", "Utang berbunga terhadap ekuitas", "debt_to_equity")):
                numerator = _number(latest.get(field))
                if numerator is None:
                    metrics.append(_metric(f"fundamental:{metric_id}", "fundamental", name, None, "x",
                                           f"{field} / total_equity", label, q_src))
                elif equity is None or equity <= 0:
                    metrics.append(_metric(f"fundamental:{metric_id}", "fundamental", name, None, "x",
                                           f"{field} / total_equity", label, q_src, status="not_meaningful",
                                           note="Ekuitas nol atau negatif."))
                else:
                    metrics.append(_metric(f"fundamental:{metric_id}", "fundamental", name, numerator / equity, "x",
                                           f"{field} / total_equity", label, q_src))
        else:
            sector_now = latest.get("financials_sector_metrics") or {}
            sector_prior = (prior or {}).get("financials_sector_metrics") or {}
            value, status, note = _growth(_number(sector_now.get("net_interest_income")),
                                          _number(sector_prior.get("net_interest_income")))
            metrics.append(_metric("fundamental:net_interest_income_growth_yoy", "fundamental",
                                   "Pertumbuhan pendapatan bunga bersih YoY", value, "ratio",
                                   "net_interest_income[kuartal terakhir] / net_interest_income[kuartal sama tahun lalu] - 1",
                                   compare, q_src, status=status, note=note))
            loans, deposits = _number(sector_now.get("net_loan")), _number(sector_now.get("total_deposit"))
            metrics.append(_metric("fundamental:loan_to_deposit", "fundamental", "Kredit bersih terhadap simpanan",
                                   loans / deposits if loans is not None and deposits else None, "ratio",
                                   "net_loan / total_deposit", label, q_src))
        if equity is not None and equity <= 0:
            limitations.append(f"Ekuitas {label} nol atau negatif; rasio berbasis ekuitas tidak bermakna.")

    annual = ((report or {}).get("financials") or {}).get("historical_financials") or []
    years = sorted((row for row in annual if isinstance(row, dict) and isinstance(row.get("year"), int)),
                   key=lambda row: row["year"])
    r_src = [report_source] if report_source else []
    if len(years) >= 2:
        last, before = years[-1], years[-2]
        value, status, note = _growth(_number(last.get("revenue")), _number(before.get("revenue")))
        metrics.append(_metric("fundamental:revenue_growth_fy", "fundamental", "Pertumbuhan pendapatan tahunan", value,
                               "ratio", "revenue[FY terakhir] / revenue[FY sebelumnya] - 1",
                               f"FY{last['year']} vs FY{before['year']}", r_src, status=status, note=note))
    elif report is not None:
        missing.append("Laporan tahunan historis di company report kurang dari dua tahun.")
    if series:
        limitations.append("Sectors tidak menyertakan tanggal publikasi laporan kuartalan; ketersediaan historis tidak dapat dipastikan.")
    return metrics, missing, limitations


# ---------------------------------------------------------------------------------------------------
# Valuation
# ---------------------------------------------------------------------------------------------------

def _peer_rows(report):
    peers = (report or {}).get("peers") or []
    data = (peers[0] or {}).get("peers_data") if peers and isinstance(peers[0], dict) else None
    return (data or {}).get("companies") or []


def valuation_metrics(report: dict | None, fundamental: list[dict], report_source: str | None,
                      quarterly_source: str | None, ticker: str, point_in_time: bool) -> tuple[list[dict], list[str], list[str], list[str]]:
    metrics, missing, limitations, conflicts = [], [], [], []
    if not point_in_time:
        return metrics, ["Company report adalah snapshot terkini, bukan data per tanggal acuan historis; "
                         "valuasi relatif tidak dihitung."], limitations, conflicts
    if report is None:
        return metrics, ["Company report Sectors tidak tersedia; valuasi tidak dapat dihitung."], limitations, conflicts
    r_src = [report_source] if report_source else []
    overview = report.get("overview") or {}
    market_cap = _number(overview.get("market_cap"))
    close_date = overview.get("latest_close_date") or "?"
    basis = f"harga penutupan {close_date}"
    metrics.append(_metric("valuation:market_cap", "valuation", "Kapitalisasi pasar", market_cap, "IDR",
                           "overview.market_cap", close_date, r_src, price_basis=basis))

    by_id = {m["metric_id"]: m for m in fundamental}
    earnings_ttm = by_id.get("fundamental:earnings_ttm", {}).get("value")
    equity_metric = by_id.get("fundamental:total_equity_latest") or {}
    equity = equity_metric.get("value")
    self_row = next((row for row in _peer_rows(report) if "self" in (row.get("group") or [])), None)
    calc_sources = r_src + ([quarterly_source] if quarterly_source else [])
    if market_cap is not None and earnings_ttm is not None:
        if earnings_ttm > 0:
            metrics.append(_metric("valuation:pe_ttm_calc", "valuation", "PE TTM (dihitung)", market_cap / earnings_ttm,
                                   "x", "market_cap / earnings_ttm", by_id["fundamental:earnings_ttm"]["period"],
                                   calc_sources, price_basis=basis, inputs=("valuation:market_cap", "fundamental:earnings_ttm")))
        else:
            metrics.append(_metric("valuation:pe_ttm_calc", "valuation", "PE TTM (dihitung)", None, "x",
                                   "market_cap / earnings_ttm", "TTM", calc_sources, status="not_meaningful",
                                   note="Laba TTM nol atau negatif; PE tidak bermakna.", price_basis=basis))
    if self_row is not None:
        for field, metric_id, name in (("pe_ttm", "pe_ttm_sectors", "PE TTM (Sectors)"),
                                       ("pb_mrq", "pb_mrq_sectors", "PB MRQ (Sectors)")):
            value = _number(self_row.get(field))
            note = None
            status = "ok"
            if value is not None and value <= 0:
                value, status, note = None, "not_meaningful", "Nilai nol/negatif dari laba atau ekuitas negatif."
            metrics.append(_metric(f"valuation:{metric_id}", "valuation", name, value, "x", f"peers.self.{field}",
                                   f"basis tahun data {self_row.get('year')}", r_src, status=status, note=note,
                                   price_basis=basis))
    else:
        missing.append("Baris emiten sendiri tidak ada di data peers Sectors.")

    pe_calc = next((m for m in metrics if m["metric_id"] == "valuation:pe_ttm_calc"), None)
    pe_sectors = next((m for m in metrics if m["metric_id"] == "valuation:pe_ttm_sectors"), None)
    if pe_calc and pe_sectors and pe_calc["value"] and pe_sectors["value"]:
        gap = pe_calc["value"] / pe_sectors["value"] - 1
        if abs(gap) > VALUATION_MISMATCH:
            conflicts.append(f"PE TTM hitungan sendiri berbeda {gap:+.0%} dari PE TTM Sectors; basis laba/periode "
                             "kemungkinan berbeda, keduanya ditampilkan.")
    if market_cap is not None and equity is not None:
        if equity > 0:
            metrics.append(_metric("valuation:pb_mrq_calc", "valuation", "PB MRQ (dihitung)", market_cap / equity, "x",
                                   "market_cap / total_equity kuartal terakhir", equity_metric.get("period", "MRQ"),
                                   calc_sources, price_basis=basis,
                                   inputs=("valuation:market_cap", "fundamental:total_equity_latest")))
        else:
            metrics.append(_metric("valuation:pb_mrq_calc", "valuation", "PB MRQ (dihitung)", None, "x",
                                   "market_cap / total_equity kuartal terakhir", equity_metric.get("period", "MRQ"),
                                   calc_sources, status="not_meaningful", note="Ekuitas nol atau negatif.",
                                   price_basis=basis))

    own_pe = (pe_calc or {}).get("value") or (pe_sectors or {}).get("value")
    own_pe_id = (pe_calc if (pe_calc or {}).get("value") else pe_sectors or {}).get("metric_id")
    own_pb_metric = next((m for m in metrics if m["metric_id"] == "valuation:pb_mrq_sectors"), None)
    peers = [row for row in _peer_rows(report) if "self" not in (row.get("group") or [])
             and str(row.get("symbol", "")).removesuffix(".JK").upper() != ticker]
    for scope in ("sub_sector", "sector"):
        scoped = [row for row in peers if scope in (row.get("group") or [])]
        positive_pe = [v for v in (_number(row.get("pe_ttm")) for row in scoped) if v is not None and v > 0]
        if len(positive_pe) >= PEER_MINIMUM or scope == "sector":
            break
    excluded = len(scoped) - len(positive_pe)
    group_label = f"peer {scope} Sectors ({len(positive_pe)} dengan PE positif, {excluded} dikecualikan)"
    if len(positive_pe) >= PEER_MINIMUM:
        peer_pe = median(positive_pe)
        metrics.append(_metric("valuation:peer_pe_median", "valuation", "Median PE TTM peer", peer_pe, "x",
                               "median(pe_ttm peer > 0)", group_label, r_src))
        if own_pe:
            metrics.append(_metric("valuation:pe_vs_peer_median", "valuation", "Selisih PE terhadap median peer",
                                   own_pe / peer_pe - 1, "ratio", f"{own_pe_id} / valuation:peer_pe_median - 1",
                                   group_label, r_src, inputs=(own_pe_id, "valuation:peer_pe_median")))
        if excluded:
            limitations.append(f"{excluded} peer dengan PE nol/negatif/kosong dikecualikan dari median; rata-rata PE peer "
                               "versi Sectors yang memasukkan PE negatif tidak dipakai.")
    else:
        missing.append(f"Peer dengan PE positif kurang dari {PEER_MINIMUM}; perbandingan relatif PE tidak dihitung.")
    positive_pb = [v for v in (_number(row.get("pb_mrq")) for row in scoped) if v is not None and v > 0]
    if len(positive_pb) >= PEER_MINIMUM:
        peer_pb = median(positive_pb)
        metrics.append(_metric("valuation:peer_pb_median", "valuation", "Median PB peer", peer_pb, "x",
                               "median(pb_mrq peer > 0)", f"peer {scope} Sectors ({len(positive_pb)} dengan PB positif)", r_src))
        if own_pb_metric and own_pb_metric["value"]:
            metrics.append(_metric("valuation:pb_vs_peer_median", "valuation", "Selisih PB terhadap median peer",
                                   own_pb_metric["value"] / peer_pb - 1, "ratio",
                                   "valuation:pb_mrq_sectors / valuation:peer_pb_median - 1", f"peer {scope}", r_src,
                                   inputs=("valuation:pb_mrq_sectors", "valuation:peer_pb_median")))

    history = [row for row in ((report.get("valuation") or {}).get("historical_valuation") or [])
               if isinstance(row, dict) and isinstance(row.get("year"), int)]
    pes = sorted((row["year"], _number(row.get("pe"))) for row in history)
    positive = [(year, value) for year, value in pes if value is not None and value > 0]
    if len(positive) >= 3:
        values = [value for _year, value in positive]
        span = f"{positive[0][0]}–{positive[-1][0]}"
        metrics.append(_metric("valuation:pe_history_median", "valuation", "Median PE historis", median(values), "x",
                               "median(historical_valuation.pe > 0)", span, r_src))
        metrics.append(_metric("valuation:pe_history_min", "valuation", "PE historis terendah", min(values), "x",
                               "min(historical_valuation.pe > 0)", span, r_src))
        metrics.append(_metric("valuation:pe_history_max", "valuation", "PE historis tertinggi", max(values), "x",
                               "max(historical_valuation.pe > 0)", span, r_src))
        if len(positive) < len(pes):
            limitations.append(f"{len(pes) - len(positive)} tahun dengan PE nol/negatif dikecualikan dari rentang historis.")
    else:
        missing.append("PE historis positif kurang dari tiga tahun; rentang historis tidak dihitung.")
    limitations.append("Analisis relatif saja; tidak ada nilai wajar atau target harga yang dihitung.")
    return metrics, missing, limitations, conflicts


# ---------------------------------------------------------------------------------------------------
# Technical
# ---------------------------------------------------------------------------------------------------

def clean_daily(rows, as_of: date) -> tuple[list[dict], dict]:
    """Seri harian bersih dan laporan kualitasnya. Tidak ada nilai yang diisi diam-diam."""
    quality = {"received": 0, "duplicates": 0, "conflicting_duplicates": 0, "invalid": 0, "after_as_of": 0,
               "zero_volume_days": 0, "sessions": 0, "first_date": None, "last_date": None, "stale": False,
               "stale_days": None}
    by_date: dict[date, dict] = {}
    for row in rows or []:
        quality["received"] += 1
        day = _day((row or {}).get("date"))
        if day is None:
            quality["invalid"] += 1
            continue
        if day > as_of:
            quality["after_as_of"] += 1
            continue
        values = {key: _number(row.get(key)) for key in ("open", "high", "low", "close", "volume")}
        o, h, l, c, v = (values[k] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c, v) or min(o, h, l, c) <= 0 or v < 0 or h < l or h < max(o, c) or l > min(o, c):
            quality["invalid"] += 1
            continue
        bar = {"date": day, "open": o, "high": h, "low": l, "close": c, "volume": v,
               "market_cap": _number(row.get("market_cap"))}
        if day in by_date:
            quality["duplicates"] += 1
            if {k: by_date[day][k] for k in values} != values:
                quality["conflicting_duplicates"] += 1
            continue
        by_date[day] = bar
    bars = [by_date[day] for day in sorted(by_date)]
    quality["sessions"] = len(bars)
    quality["zero_volume_days"] = sum(1 for bar in bars if bar["volume"] == 0)
    if bars:
        quality["first_date"], quality["last_date"] = bars[0]["date"].isoformat(), bars[-1]["date"].isoformat()
        quality["stale_days"] = (as_of - bars[-1]["date"]).days
        quality["stale"] = quality["stale_days"] > STALE_DAYS
    return bars, quality


def _action_dates(entry: dict) -> list[date]:
    preferred = [key for key in entry if "ex" in key and "date" in key] or [key for key in entry if "date" in key]
    return [day for day in (_day(entry.get(key)) for key in preferred) if day]


def corporate_action_breaks(payload: dict | None) -> list[dict]:
    """Aksi yang mengubah basis harga (split, rights, bonus, waran). Dividen tunai tidak."""
    actions = ((payload or {}).get("corporate_actions") or {}) if isinstance(payload, dict) else {}
    breaks = []
    for kind in ("stock_split", "right_issue", "bonus", "warrant", "reverse_split"):
        for entry in actions.get(kind) or []:
            if isinstance(entry, dict):
                for day in _action_dates(entry)[:1]:
                    breaks.append({"date": day.isoformat(), "type": kind})
    return sorted(breaks, key=lambda item: item["date"])


def suspected_breaks(bars: list[dict]) -> list[dict]:
    found = []
    for previous, current in zip(bars, bars[1:]):
        change = current["close"] / previous["close"] - 1
        if abs(change) >= SUSPECT_JUMP:
            found.append({"date": current["date"].isoformat(), "type": "suspected_unadjusted_jump",
                          "change": round(change, 4)})
    return found


def sma(values: list[float], window: int) -> float | None:
    return sum(values[-window:]) / window if len(values) >= window else None


def ema_series(values: list[float], period: int) -> list[float | None]:
    """EMA disemai SMA `period` nilai pertama; alpha = 2/(period+1). Posisi sebelum warm-up bernilai None."""
    output: list[float | None] = [None] * len(values)
    if len(values) < period:
        return output
    alpha = 2 / (period + 1)
    current = sum(values[:period]) / period
    output[period - 1] = current
    for index in range(period, len(values)):
        current = alpha * values[index] + (1 - alpha) * current
        output[index] = current
    return output


def rsi_wilder(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain, avg_loss = sum(gains[:period]) / period, sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def atr_wilder(bars: list[dict], period: int = 14) -> float | None:
    if len(bars) <= period:
        return None
    ranges = [max(bar["high"] - bar["low"], abs(bar["high"] - prev["close"]), abs(bar["low"] - prev["close"]))
              for prev, bar in zip(bars, bars[1:])]
    atr = sum(ranges[:period]) / period
    for value in ranges[period:]:
        atr = (atr * (period - 1) + value) / period
    return atr


def macd(closes: list[float], fast=12, slow=26, signal=9) -> tuple[float, float, float] | None:
    fast_ema, slow_ema = ema_series(closes, fast), ema_series(closes, slow)
    line = [f - s for f, s in zip(fast_ema, slow_ema) if f is not None and s is not None]
    signal_series = ema_series(line, signal)
    if not line or signal_series[-1] is None:
        return None
    return line[-1], signal_series[-1], line[-1] - signal_series[-1]


def technical_metrics(bars: list[dict], quality: dict, breaks: list[dict], daily_sources: list[str],
                      action_source: str | None) -> tuple[list[dict], list[str], list[str], dict]:
    metrics, missing, limitations = [], [], []
    sources = list(daily_sources)
    segment = bars
    info = {"segment_start": None, "breaks_in_window": []}
    if bars:
        first, last = bars[0]["date"].isoformat(), bars[-1]["date"].isoformat()
        inside = [item for item in breaks if first < item["date"] <= last]
        info["breaks_in_window"] = inside
        if inside:
            cut = inside[-1]["date"]
            segment = [bar for bar in bars if bar["date"].isoformat() >= cut]
            info["segment_start"] = cut
            kinds = ", ".join(sorted({item["type"] for item in inside}))
            limitations.append(f"Harga Sectors belum disesuaikan dan ada peristiwa pengubah basis harga ({kinds}) pada "
                               f"{', '.join(item['date'] for item in inside)}; indikator hanya memakai {len(segment)} "
                               f"sesi sejak {cut}.")
            if action_source and any(item["type"] != "suspected_unadjusted_jump" for item in inside):
                sources.append(action_source)
    if not bars:
        missing.append("Data harga harian Sectors tidak tersedia.")
        return metrics, missing, limitations, info
    if quality.get("stale"):
        limitations.append(f"Data harga terakhir {quality['last_date']}, {quality['stale_days']} hari sebelum tanggal acuan.")
    if quality.get("conflicting_duplicates"):
        limitations.append(f"{quality['conflicting_duplicates']} tanggal punya baris ganda berbeda isi; baris pertama dipakai.")
    if quality.get("invalid"):
        limitations.append(f"{quality['invalid']} baris harga tidak valid dibuang.")
    if quality.get("zero_volume_days"):
        limitations.append(f"{quality['zero_volume_days']} sesi bervolume nol tetap dihitung apa adanya.")

    closes = [bar["close"] for bar in segment]
    last = segment[-1]
    period = f"{segment[0]['date'].isoformat()}–{last['date'].isoformat()} ({len(segment)} sesi)"
    basis = f"close tidak disesuaikan, {last['date'].isoformat()}"
    n = len(segment)

    def add(key, name, value, unit, formula, inputs=()):
        needed = MIN_OBS.get(key)
        if needed and n < needed:
            metrics.append(_metric(f"technical:{key}", "technical", name, None, unit, formula, period, sources,
                                   note=f"Butuh minimal {needed} sesi, tersedia {n}.", price_basis=basis))
            missing.append(f"{name}: butuh minimal {needed} sesi, tersedia {n}.")
            return
        metrics.append(_metric(f"technical:{key}", "technical", name, value, unit, formula, period, sources,
                               price_basis=basis, inputs=inputs))

    metrics.append(_metric("technical:close", "technical", f"Harga penutupan {last['date'].isoformat()}", last["close"],
                           "IDR", "close sesi terakhir", last["date"].isoformat(), sources, price_basis=basis))
    add("return_20", "Return 20 sesi", closes[-1] / closes[-21] - 1 if n >= 21 else None, "ratio",
        "close[t] / close[t-20] - 1")
    add("return_60", "Return 60 sesi", closes[-1] / closes[-61] - 1 if n >= 61 else None, "ratio",
        "close[t] / close[t-60] - 1")
    sma20, sma50 = sma(closes, 20), sma(closes, 50)
    add("sma20", "SMA20", sma20, "IDR", "rata-rata close 20 sesi")
    add("sma50", "SMA50", sma50, "IDR", "rata-rata close 50 sesi")
    if sma20:
        add("close_vs_sma20", "Jarak close terhadap SMA20", closes[-1] / sma20 - 1, "ratio", "close / SMA20 - 1",
            inputs=("technical:close", "technical:sma20"))
    if sma50:
        add("close_vs_sma50", "Jarak close terhadap SMA50", closes[-1] / sma50 - 1, "ratio", "close / SMA50 - 1",
            inputs=("technical:close", "technical:sma50"))
    if sma20 and sma50:
        add("sma20_vs_sma50", "Jarak SMA20 terhadap SMA50", sma20 / sma50 - 1, "ratio", "SMA20 / SMA50 - 1",
            inputs=("technical:sma20", "technical:sma50"))
    add("rsi14", "RSI14", rsi_wilder(closes) if n >= MIN_OBS["rsi14"] else None, "indeks 0-100",
        "RSI Wilder 14 (rata-rata awal sederhana, lalu penghalusan Wilder); zona baca: 30 ke bawah rendah, "
        "70 ke atas tinggi")
    atr = atr_wilder(segment) if n >= MIN_OBS["atr14"] else None
    add("atr14_pct", "ATR14 relatif terhadap close", atr / closes[-1] if atr is not None else None, "ratio",
        "ATR Wilder 14 / close")
    result = macd(closes) if n >= MIN_OBS["macd"] else None
    add("macd_histogram", "Histogram MACD(12,26,9)", result[2] if result else None, "IDR",
        "(EMA12 - EMA26) - EMA9 dari garis MACD; EMA disemai SMA")
    if n >= MIN_OBS["volume_ratio_20"]:
        previous = [bar["volume"] for bar in segment[-21:-1]]
        average = sum(previous) / len(previous)
        add("volume_ratio_20", "Volume relatif 20 sesi", segment[-1]["volume"] / average if average > 0 else None,
            "x", "volume[t] / rata-rata volume 20 sesi sebelumnya")
    else:
        add("volume_ratio_20", "Volume relatif 20 sesi", None, "x", "volume[t] / rata-rata volume 20 sesi sebelumnya")
    if n >= MIN_OBS["volatility_20"]:
        returns = [math.log(b / a) for a, b in zip(closes[-21:-1], closes[-20:])]
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        add("volatility_20", "Volatilitas tahunan 20 sesi", math.sqrt(variance) * math.sqrt(252), "ratio",
            "stdev sampel log return 20 sesi x akar(252)")
    else:
        add("volatility_20", "Volatilitas tahunan 20 sesi", None, "ratio", "stdev sampel log return 20 sesi x akar(252)")
    limitations.append("Indikator dihitung dari harga harian penutupan yang tidak disesuaikan; bukan data intraday.")
    return metrics, missing, limitations, info


def daily_windows(as_of: date, calendar_days: int, chunk: int = 90) -> list[tuple[str, str]]:
    """Rentang request berurutan dari terbaru. Endpoint memangkas rentang >90 hari tanpa error."""
    windows, end = [], as_of
    start_limit = as_of - timedelta(days=calendar_days - 1)
    while end >= start_limit:
        start = max(start_limit, end - timedelta(days=chunk - 1))
        windows.append((start.isoformat(), end.isoformat()))
        end = start - timedelta(days=1)
    return windows

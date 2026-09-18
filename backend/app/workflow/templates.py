"""Klaim yang ditulis kode, bukan model: angka perhitungan dan ringkasan technical.

Kalimatnya dibentuk dari metrik yang sudah dihitung, jadi angka di dalamnya selalu bisa dilacak.
Kosakata sengaja deskriptif ("di atas rata-rata 50 sesi"), bukan sinyal transaksi.
"""
from __future__ import annotations

from app.workflow.evidence import display

CALCULATION_METRICS = {
    "fundamental": ("revenue_growth_yoy", "earnings_growth_yoy", "net_margin_latest", "revenue_ttm", "earnings_ttm",
                    "operating_cash_flow_ttm", "cash_conversion_ttm", "liabilities_to_equity", "debt_to_equity",
                    "net_interest_income_growth_yoy", "loan_to_deposit", "revenue_growth_fy"),
    "valuation": ("pe_ttm_calc", "pe_ttm_sectors", "pb_mrq_calc", "pb_mrq_sectors", "peer_pe_median",
                  "pe_vs_peer_median", "peer_pb_median", "pb_vs_peer_median", "pe_history_median"),
}


def calculation_claims(domain: str, metrics: dict[str, dict]) -> list[dict]:
    claims = []
    for key in CALCULATION_METRICS[domain]:
        metric = metrics.get(f"{domain}:{key}")
        if not metric or metric["status"] != "ok":
            continue
        claims.append(_claim(f"{domain}:calc:{key}", domain, "calculation",
                             f"{metric['name']} ({metric['period']}): {display(metric)}.", [metric["metric_id"]]))
    return claims


def _claim(claim_id, domain, kind, statement, metric_ids, source_ids=(), limitations=(), attribution="data"):
    return {"claim_id": claim_id, "domain": domain, "kind": kind, "statement": statement, "attribution": attribution,
            "metric_ids": list(metric_ids), "source_ids": list(source_ids), "quote": None, "period": None,
            "supports_claim_ids": [], "assumptions": [], "limitations": list(limitations),
            "validation_status": "pending", "validation_notes": [], "mechanical_issues": [], "review_notes": [],
            "version": 1, "author": "code"}


def _ok(metrics, key):
    metric = metrics.get(f"technical:{key}")
    return metric if metric and metric["status"] == "ok" and metric["value"] is not None else None


def technical_claims(metrics: dict[str, dict]) -> tuple[list[dict], str]:
    claims = []
    close = _ok(metrics, "close")
    vs20, vs50, cross = _ok(metrics, "close_vs_sma20"), _ok(metrics, "close_vs_sma50"), _ok(metrics, "sma20_vs_sma50")
    if close and vs20 and vs50:
        where20 = "di atas" if vs20["value"] > 0 else "di bawah"
        where50 = "di atas" if vs50["value"] > 0 else "di bawah"
        claims.append(_claim("technical:trend:price_vs_sma", "technical", "calculation",
                             f"Harga penutupan {display(close)} berada {where20} SMA20 ({display(vs20)}) dan "
                             f"{where50} SMA50 ({display(vs50)}).",
                             [close["metric_id"], vs20["metric_id"], vs50["metric_id"]]))
    if cross:
        order = "di atas" if cross["value"] > 0 else "di bawah"
        claims.append(_claim("technical:trend:sma_order", "technical", "calculation",
                             f"SMA20 berada {order} SMA50 ({display(cross)}).", [cross["metric_id"]]))
    for key, label in (("return_20", "20 sesi"), ("return_60", "60 sesi")):
        metric = _ok(metrics, key)
        if metric:
            claims.append(_claim(f"technical:return:{key}", "technical", "calculation",
                                 f"Perubahan harga {label}: {display(metric)}.", [metric["metric_id"]]))
    rsi = _ok(metrics, "rsi14")
    if rsi:
        zone = "zona tinggi (70 atau lebih)" if rsi["value"] >= 70 else (
            "zona rendah (30 atau kurang)" if rsi["value"] <= 30 else "zona tengah (antara 30 dan 70)")
        claims.append(_claim("technical:momentum:rsi14", "technical", "calculation",
                             f"RSI14 bernilai {display(rsi)}, berada di {zone}.", [rsi["metric_id"]]))
    histogram = _ok(metrics, "macd_histogram")
    if histogram:
        side = "positif" if histogram["value"] > 0 else "negatif atau nol"
        claims.append(_claim("technical:momentum:macd", "technical", "calculation",
                             f"Histogram MACD(12,26,9) {side}: garis MACD {'di atas' if histogram['value'] > 0 else 'tidak di atas'} "
                             "garis sinyal.", [histogram["metric_id"]]))
    volume = _ok(metrics, "volume_ratio_20")
    if volume:
        claims.append(_claim("technical:volume:ratio20", "technical", "calculation",
                             f"Volume sesi terakhir {display(volume)} rata-rata 20 sesi sebelumnya.", [volume["metric_id"]]))
    for key, label in (("volatility_20", "Volatilitas tahunan dari 20 sesi terakhir"),
                       ("atr14_pct", "ATR14 relatif terhadap harga")):
        metric = _ok(metrics, key)
        if metric:
            claims.append(_claim(f"technical:volatility:{key}", "technical", "calculation",
                                 f"{label}: {display(metric)}.", [metric["metric_id"]]))
    headline = claims[0]["statement"] if claims else "Indikator technical belum dapat dihitung."
    return claims, headline


def news_code_claims(corporate_actions: list[dict], action_source: str | None, screening: dict | None,
                     news_count: int, duplicate_count: int, news_source: str | None) -> list[dict]:
    """Klaim berita dari data terstruktur. Jumlah artikel harus dirujuk lewat metrik `news:*_articles`."""
    claims = []
    for index, action in enumerate(corporate_actions):
        if not action_source:
            break
        label = {"stock_split": "Stock split", "right_issue": "Rights issue", "bonus": "Saham bonus",
                 "warrant": "Waran", "reverse_split": "Reverse split"}.get(action["type"], action["type"])
        claims.append(_claim(f"news:action:{index}", "news", "observation",
                             f"{label} tercatat di data corporate actions Sectors pada {action['date']}.",
                             [], [action_source], attribution="data"))
    if screening:
        label = {"structural_red_flag": "red flag struktural", "growth_catalyst": "katalis pertumbuhan",
                 "inconclusive": "belum simpulan"}.get(screening["label"], screening["label"])
        review = " dan masih perlu pemeriksaan manusia" if screening["gate_status"] != "passed" else ""
        claims.append(_claim("news:screening:latest", "news", "observation",
                             f"Screening aksi korporasi SignalGate terakhir ({screening['date']}) berlabel {label}{review}.",
                             [], [screening["source_id"]], attribution="data"))
    if news_source is not None:
        metric_ids = ["news:unique_articles"] + (["news:duplicate_articles"] if duplicate_count else [])
        extra = f"; {duplicate_count} salinan artikel yang sama tidak dihitung ulang" if duplicate_count else ""
        claims.append(_claim("news:coverage", "news", "calculation",
                             f"{news_count} artikel berita unik untuk emiten ini tersedia pada rentang yang diambil{extra}.",
                             metric_ids, [news_source], attribution="data"))
    return claims

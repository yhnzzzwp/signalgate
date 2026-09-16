from __future__ import annotations

from collections.abc import Sequence

from app.pipeline.hypothesize import classify
from app.pipeline.schema import CandidateEvent, CompanySnapshot
from app.sectors.client import SectorsClient

CORPORATE_ACTION_KEYWORDS = [
    "rights issue", "private placement", "akuisisi", "acquisition",
    "perubahan pengendali", "merger",
]


def collect_candidate_events(client: SectorsClient, limit_per_keyword: int = 20) -> list[CandidateEvent]:
    seen_sources: set[str] = set()
    events: list[CandidateEvent] = []

    for keyword in CORPORATE_ACTION_KEYWORDS:
        response = client.news(keyword=keyword, limit=limit_per_keyword)
        for item in response.get("results", []):
            source_url = item.get("source", "")
            if source_url in seen_sources:
                continue
            seen_sources.add(source_url)

            bucket, matched_keywords = classify(item.get("title", ""), item.get("body", ""))
            if bucket is None:
                continue

            for symbol in item.get("symbols") or [None]:
                events.append(
                    CandidateEvent(
                        ticker=(symbol or "").removesuffix(".JK"),
                        headline=item.get("title", ""),
                        body=item.get("body", ""),
                        source_url=source_url,
                        published_at=item.get("timestamp", ""),
                        bucket=bucket,
                        matched_keywords=matched_keywords,
                        sector=item.get("sector"),
                        sub_sector=item.get("sub_sector") or [],
                    )
                )

    return [event for event in events if event.ticker]


def normalize_ticker(symbol: str) -> str:
    return (symbol or "").strip().upper().removesuffix(".JK")


class FreeFloatLookup:
    """Free float per subsector, fetched once and reused for the whole run.

    /v2/free-float/ bills 1 credit per 100 companies and has no per-symbol filter, so asking per
    subsector and caching it keeps three events in the same subsector at one credit, not three.
    """

    def __init__(self, client: SectorsClient) -> None:
        self.client = client
        self._by_sub_sector: dict[str, list[dict]] = {}

    def _rows(self, sub_sector: str) -> list[dict]:
        if sub_sector not in self._by_sub_sector:
            try:
                rows = self.client.free_float(sub_sector=sub_sector)
            except Exception:
                rows = []
            self._by_sub_sector[sub_sector] = rows if isinstance(rows, list) else []
        return self._by_sub_sector[sub_sector]

    def find(self, ticker: str, sub_sectors: Sequence[str]) -> tuple[float | None, int | None, int | None]:
        """Return (free_float, rank, universe_size); rank 1 is the thinnest float in the subsector."""
        wanted = normalize_ticker(ticker)
        for sub_sector in sub_sectors:
            if not sub_sector:
                continue
            rows = [row for row in self._rows(sub_sector) if isinstance(row.get("free_float"), (int, float))]
            ordered = sorted(rows, key=lambda row: row["free_float"])
            for position, row in enumerate(ordered, start=1):
                if normalize_ticker(row.get("symbol", "")) == wanted:
                    return float(row["free_float"]), position, len(ordered)
        return None, None, None


class SubsectorValuationLookup:
    """PB agregat subsektor, satu panggilan per subsektor untuk satu run (1 kredit per seksi).

    Bentuknya berbeda dari company report: `historical_valuation` di sini dict berkunci tahun,
    bukan list. Tahun terbesar yang dipakai.
    """

    def __init__(self, client: SectorsClient) -> None:
        self.client = client
        self._by_sub_sector: dict[str, float | None] = {}

    def _latest_pb(self, sub_sector: str) -> float | None:
        try:
            report = self.client.subsector_report(sub_sector, sections=("valuation",))
        except Exception:
            return None
        history = ((report or {}).get("valuation") or {}).get("historical_valuation") or {}
        years = [year for year in history if str(year).isdigit()]
        if not years:
            return None
        value = (history[max(years, key=int)] or {}).get("pb")
        return float(value) if isinstance(value, (int, float)) and value > 0 else None

    def find(self, sub_sectors: Sequence[str]) -> tuple[float | None, str | None]:
        """Return (subsector_pb, subsector_slug) for the first subsector that reports one."""
        for sub_sector in sub_sectors:
            if not sub_sector:
                continue
            if sub_sector not in self._by_sub_sector:
                self._by_sub_sector[sub_sector] = self._latest_pb(sub_sector)
            if self._by_sub_sector[sub_sector] is not None:
                return self._by_sub_sector[sub_sector], sub_sector
        return None, None


def fetch_quarterly_financials(client: SectorsClient, ticker: str, n_quarters: int = 4) -> list[dict]:
    """Newest quarter first. Bills 1 credit per quarter, so the window stays deliberately small."""
    try:
        rows = client.quarterly_financials(ticker, n_quarters=n_quarters)
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    return sorted((row for row in rows if isinstance(row, dict)), key=lambda row: row.get("date") or "", reverse=True)


def fetch_insider_sales(client: SectorsClient, ticker: str, limit: int = 30) -> list[dict]:
    """Penjualan oleh insider dan investor korporasi, terbaru dulu. 1 kredit.

    Hanya arah jual yang diminta: yang dicari adalah pengendali yang melepas posisi menjelang
    penggalangan dana, bukan seluruh riwayat kepemilikan.
    """
    try:
        payload = client.filings(ticker, limit=limit, transaction_type="sell")
    except Exception:
        return []
    rows = (payload or {}).get("results") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)
            and row.get("holder_type") in {"insider", "corporate-investor"}]


def snapshot_company(
    client: SectorsClient,
    ticker: str,
    sub_sectors: Sequence[str] = (),
    float_lookup: FreeFloatLookup | None = None,
    n_quarters: int = 4,
    valuation_lookup: "SubsectorValuationLookup | None" = None,
    with_filings: bool = False,
) -> CompanySnapshot | None:
    try:
        report = client.company_report(ticker)
    except Exception:
        return None

    snapshot = snapshot_from_report(report, ticker)
    if snapshot is None:
        return None

    free_float, rank, universe = (
        float_lookup.find(ticker, sub_sectors) if float_lookup is not None else (None, None, None)
    )
    subsector_pb, subsector_slug = (
        valuation_lookup.find(sub_sectors) if valuation_lookup is not None else (None, None)
    )
    return snapshot.model_copy(update={
        "free_float": free_float,
        "free_float_rank": rank,
        "free_float_universe": universe,
        "quarterly_financials": fetch_quarterly_financials(client, ticker, n_quarters) if n_quarters else [],
        "subsector_pb": subsector_pb,
        "subsector_slug": subsector_slug,
        "insider_sales": fetch_insider_sales(client, ticker) if with_filings else [],
    })


def snapshot_from_report(report: dict, ticker: str) -> CompanySnapshot | None:
    if not isinstance(report, dict) or not report.get("company_name") or not any(
        report.get(key) for key in ("overview", "valuation", "ownership", "financials")
    ):
        return None

    overview = report.get("overview") or {}
    valuation = report.get("valuation") or {}
    ownership = report.get("ownership") or {}

    return CompanySnapshot(
        ticker=ticker,
        company_name=report.get("company_name", ticker),
        business_description=overview.get("industry"),
        market_cap=overview.get("market_cap"),
        major_shareholders=ownership.get("major_shareholders", []),
        pb_ratio=(valuation.get("historical_valuation") or [{}])[-1].get("pb"),
        pe_ratio=valuation.get("forward_pe"),
        intrinsic_value=valuation.get("intrinsic_value"),
        last_close_price=valuation.get("last_close_price"),
    )

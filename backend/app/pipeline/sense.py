from __future__ import annotations

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


def snapshot_company(client: SectorsClient, ticker: str) -> CompanySnapshot | None:
    try:
        report = client.company_report(ticker)
    except Exception:
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

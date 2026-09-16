from __future__ import annotations

import json
import re

ACTION_TERMS = (
    "rights issue", "hmetd", "pmhmetd", "pmthmetd", "private placement", "penempatan terbatas",
    "akuisisi", "pengambilalihan", "pengendali", "divestasi", "inbreng", "penambahan modal",
    "dilusi", "pembeli siaga", "standby buyer", "rupslb", "merger", "acquisition", "controlling",
)

COMPANY_STOPWORDS = {"indonesia", "persero", "group"}
FINANCIAL_KEYS = ("year", "revenue", "earnings", "income", "profit", "equity", "assets", "liabilities")


def focus_terms(ticker: str, company_name: str | None = None) -> list[str]:
    terms = [ticker.lower(), *ACTION_TERMS]
    if company_name:
        name = re.sub(r"\b(pt|tbk)\b\.?", " ", company_name.lower())
        terms += [word for word in re.findall(r"[a-z0-9]{4,}", name) if word not in COMPANY_STOPWORDS]
    return list(dict.fromkeys(terms))


def excerpt(text: str, terms: list[str], limit: int) -> str:
    paragraphs = [part.strip() for part in re.split(r"\n+", text) if part.strip()]
    if not paragraphs:
        return ""
    lowered_terms = [term.lower() for term in terms]
    picked = [paragraphs[0]] + [
        paragraph for paragraph in paragraphs[1:] if any(term in paragraph.lower() for term in lowered_terms)
    ]
    kept: list[str] = []
    used = 0
    for paragraph in picked:
        if used + len(paragraph) > limit:
            remaining = limit - used
            if remaining > 200:
                kept.append(paragraph[:remaining])
            break
        kept.append(paragraph)
        used += len(paragraph) + 1
    return "\n".join(kept)


def _financial_rows(report: dict) -> list[dict]:
    rows = (report.get("financials") or {}).get("historical_financials") or []
    return [
        {key: value for key, value in row.items() if any(word in key.lower() for word in FINANCIAL_KEYS)}
        for row in rows[-3:]
        if isinstance(row, dict)
    ]


def summarize_company_report(report: dict) -> str:
    overview = report.get("overview") or {}
    valuation = report.get("valuation") or {}
    history = valuation.get("historical_valuation") or []
    ownership = report.get("ownership") or {}
    management = report.get("management") or {}
    summary = {
        "symbol": report.get("symbol"),
        "company_name": report.get("company_name"),
        "sector": overview.get("sector"),
        "sub_sector": overview.get("sub_sector"),
        "industry": overview.get("industry"),
        "listing_date": overview.get("listing_date"),
        "market_cap": overview.get("market_cap"),
        "last_close_price": valuation.get("last_close_price"),
        "latest_close_date": valuation.get("latest_close_date"),
        "forward_pe": valuation.get("forward_pe"),
        "latest_annual_valuation": history[-1] if history else None,
        "recent_financials": _financial_rows(report),
        "major_shareholders": [
            {"name": holder.get("name"), "share_percentage": holder.get("share_percentage")}
            for holder in (ownership.get("major_shareholders") or [])[:8]
        ],
        "key_executives": [executive.get("name") for executive in (management.get("key_executives") or [])[:6]],
    }
    return json.dumps(summary, ensure_ascii=False)

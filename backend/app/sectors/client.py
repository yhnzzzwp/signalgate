"""Python client for the Sectors Financial API v2."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# company/report bills one credit per section, so only the sections the pipeline actually reads are
# requested: summarize_company_report() uses overview, valuation, ownership, management and financials.
# Asking for all eight (the API default) would bill 8 credits and hand the model data nobody reads.
REPORT_SECTIONS = ("overview", "valuation", "ownership", "management", "financials")
ALL_REPORT_SECTIONS = ("dividend", "financials", "future", "management", "overview", "ownership", "peers", "valuation")


class SectorsAPIError(RuntimeError):
    """Raised when Sectors returns an error or cannot be reached."""


class SectorsClient:
    def __init__(self, api_key: str | None = None, timeout: int = 30) -> None:
        self.api_key = api_key or os.getenv("SECTORS_API_KEY")
        if not self.api_key:
            raise ValueError("SECTORS_API_KEY environment variable is required")
        self.timeout = timeout
        self.base_url = "https://api.sectors.app/v2"
        self.last_report = None

    def get(self, path: str, **params: Any) -> Any:
        clean_path = path.strip("/")
        query = {key: value for key, value in params.items() if value is not None}
        url = f"{self.base_url}/{clean_path}/"
        if query:
            url = f"{url}?{urlencode(query)}"

        request = Request(
            url,
            headers={
                "Authorization": self.api_key,
                "Accept": "application/json",
                "User-Agent": "signalgate-sectors-client/1.0",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise SectorsAPIError(f"Sectors API returned HTTP {error.code}: {body[:500]}") from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise SectorsAPIError(f"Sectors API request failed: {error}") from error

    def list_subsectors(self) -> list[dict[str, str]]:
        return self.get("subsectors")

    def company_report(self, ticker: str, sections: Sequence[str] | None = None) -> dict[str, Any]:
        self.last_report = None
        report = self.get(f"company/report/{ticker}", sections=",".join(sections or REPORT_SECTIONS))
        self.last_report = report
        return report

    def free_float(
        self,
        sector: str | None = None,
        sub_sector: str | None = None,
        industry: str | None = None,
        sub_industry: str | None = None,
    ) -> list[dict[str, Any]]:
        """Public ownership share per company. Costs 1 credit per 100 companies returned."""
        return self.get("free-float", sector=sector, sub_sector=sub_sector,
                        industry=industry, sub_industry=sub_industry)

    def quarterly_financials(self, ticker: str, n_quarters: int = 4) -> list[dict[str, Any]]:
        """Full quarterly statements, newest first. Costs 1 credit per quarter returned."""
        return self.get(f"financials/quarterly/{ticker}", n_quarters=n_quarters)

    def filings(self, ticker: str, limit: int = 30, transaction_type: str | None = None,
                holder_type: str | None = None) -> dict[str, Any]:
        """Laporan keterbukaan kepemilikan IDX (insider dan pemegang saham besar). 1 kredit."""
        return self.get("filings", symbol=ticker, limit=limit,
                        transaction_type=transaction_type, holder_type=holder_type)

    def subsector_report(self, sub_sector: str, sections: Sequence[str] = ("valuation",)) -> dict[str, Any]:
        """Subsector aggregates. Costs 1 credit per section; the default asks for the one we read."""
        return self.get(f"subsector/report/{sub_sector}", sections=",".join(sections))

    def daily(self, ticker: str, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
        return self.get(f"daily/{ticker}", start=start, end=end)

    def idx_total(self, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
        return self.get("idx-total", start=start, end=end)

    def news(
        self,
        keyword: str | None = None,
        limit: int = 20,
        offset: int = 0,
        start: str | None = None,
        end: str | None = None,
        symbols: str | None = None,
    ) -> dict[str, Any]:
        """Artikel berita; `symbols` menyaring per emiten. 1 kredit per request, limit maksimum 30."""
        return self.get("news", keyword=keyword, limit=limit, offset=offset, start=start, end=end, symbols=symbols)

    def corporate_actions(self, ticker: str) -> dict[str, Any]:
        """Split, rights issue, bonus, waran, dividen, RUPS untuk satu emiten. 1 kredit."""
        return self.get(f"company/corporate-actions/{ticker}")


if __name__ == "__main__":
    import pprint

    pprint.pp(SectorsClient().list_subsectors())

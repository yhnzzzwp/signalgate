"""Python client for the Sectors Financial API v2."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPORT_SECTIONS = ("overview", "valuation", "ownership", "management", "financials")
ALL_REPORT_SECTIONS = ("dividend", "financials", "future", "management", "overview", "ownership", "peers", "valuation")

QUOTA_ERROR_CODES = frozenset({
    "insufficient_credits",
    "monthly_limit_exceeded",
    "subscription_not_active",
    "subscription_does_not_allow",
})


class SectorsAPIError(RuntimeError):
    """Raised when Sectors returns an error or cannot be reached."""


def _normalize_keys(api_key: str | Sequence[str] | None) -> list[str]:
    raw = [os.getenv("SECTORS_API_KEY")] if api_key is None else (
        [api_key] if isinstance(api_key, str) else list(api_key)
    )
    keys: list[str] = []
    for key in raw:
        if key and key not in keys:
            keys.append(key)
    return keys


class SectorsClient:
    def __init__(self, api_key: str | Sequence[str] | None = None, timeout: int = 30) -> None:
        self._keys = _normalize_keys(api_key)
        if not self._keys:
            raise ValueError("SECTORS_API_KEY environment variable is required")
        self._key_index = 0
        self.timeout = timeout
        self.base_url = "https://api.sectors.app/v2"
        self.last_report = None

    @property
    def api_key(self) -> str:
        """The key currently in use; advances past keys that reported quota exhaustion."""
        return self._keys[self._key_index]

    def get(self, path: str, **params: Any) -> Any:
        clean_path = path.strip("/")
        query = {key: value for key, value in params.items() if value is not None}
        url = f"{self.base_url}/{clean_path}/"
        if query:
            url = f"{url}?{urlencode(query)}"

        while True:
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
                has_backup = self._key_index + 1 < len(self._keys)
                if has_backup and self._is_quota_error(error.code, body):
                    self._key_index += 1
                    continue
                raise SectorsAPIError(f"Sectors API returned HTTP {error.code}: {body[:500]}") from error
            except (URLError, TimeoutError, json.JSONDecodeError) as error:
                raise SectorsAPIError(f"Sectors API request failed: {error}") from error

    @staticmethod
    def _is_quota_error(status: int, body: str) -> bool:
        if status == 429:
            return True
        try:
            code = json.loads(body).get("code")
        except (json.JSONDecodeError, AttributeError):
            return False
        return code in QUOTA_ERROR_CODES

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

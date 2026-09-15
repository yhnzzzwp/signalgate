"""Python client for the Sectors Financial API v2."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class SectorsAPIError(RuntimeError):
    """Raised when Sectors returns an error or cannot be reached."""


class SectorsClient:
    def __init__(self, api_key: str | None = None, timeout: int = 30) -> None:
        self.api_key = api_key or os.getenv("SECTORS_API_KEY")
        if not self.api_key:
            raise ValueError("SECTORS_API_KEY environment variable is required")
        self.timeout = timeout
        self.base_url = "https://api.sectors.app/v2"

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

    def company_report(self, ticker: str) -> dict[str, Any]:
        return self.get(f"company/report/{ticker}")

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
    ) -> dict[str, Any]:
        return self.get("news", keyword=keyword, limit=limit, offset=offset, start=start, end=end)


if __name__ == "__main__":
    import pprint

    pprint.pp(SectorsClient().list_subsectors())

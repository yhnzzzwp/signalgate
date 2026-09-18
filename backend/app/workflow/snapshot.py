"""Snapshot sumber bersama untuk keempat panel.

Setiap respons mentah disimpan sebelum ditransformasi, lengkap dengan parameter request, waktu ambil,
hash, status, dan perkiraan kredit. Keempat panel membaca snapshot yang sama sehingga tidak ada report
yang diambil dua kali. Replay memakai snapshot run lain apa adanya: tanggal ambil aslinya dipertahankan
dan tidak pernah disajikan sebagai data live.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.workflow.calculations import daily_windows

REPORT_SECTIONS = ("overview", "valuation", "financials", "peers")
WIB = timezone(timedelta(hours=7))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_wib() -> date:
    return datetime.now(WIB).date()


def canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def request_key(endpoint: str, params: dict) -> str:
    return canonical({"endpoint": endpoint, "params": params})


class SourceUnavailable(RuntimeError):
    pass


class LiveGateway:
    mode = "live"

    def __init__(self, client) -> None:
        self.client = client

    def fetch(self, endpoint: str, params: dict) -> tuple[Any, str]:
        ticker = params["ticker"]
        if endpoint == "company_report":
            payload = self.client.company_report(ticker, sections=params["sections"])
        elif endpoint == "quarterly":
            payload = self.client.quarterly_financials(ticker, n_quarters=params["n_quarters"])
        elif endpoint == "daily":
            payload = self.client.daily(ticker, start=params["start"], end=params["end"])
        elif endpoint == "corporate_actions":
            payload = self.client.corporate_actions(ticker)
        elif endpoint == "news":
            payload = self.client.news(symbols=ticker, start=params["start"], end=params["end"], limit=params["limit"])
        else:
            raise ValueError(f"Endpoint tidak dikenal: {endpoint}")
        return payload, now_iso()


class ReplayGateway:
    """Membaca ulang snapshot run lain. Request yang tidak pernah diambil tidak dikarang."""

    mode = "replay"

    def __init__(self, run_dir: Path) -> None:
        self.index: dict[str, Path] = {}
        for path in sorted((Path(run_dir) / "sources").glob("*.json")):
            stored = json.loads(path.read_text(encoding="utf-8"))
            source = stored.get("source") or {}
            if source.get("status") == "ok" and stored.get("request_key"):
                self.index[stored["request_key"]] = path
        if not self.index:
            raise SourceUnavailable(f"Tidak ada snapshot yang bisa diputar ulang di {run_dir}.")

    def fetch(self, endpoint: str, params: dict) -> tuple[Any, str]:
        path = self.index.get(request_key(endpoint, params))
        if path is None:
            raise SourceUnavailable(f"Snapshot replay tidak memuat {endpoint} dengan parameter {canonical(params)}.")
        stored = json.loads(path.read_text(encoding="utf-8"))
        return stored["payload"], stored["source"]["fetched_at"]


class FixtureGateway:
    """Untuk test: `responses[endpoint]` berupa nilai, exception, atau fungsi(params)."""

    mode = "fixture"

    def __init__(self, responses: dict[str, Any], fetched_at: str = "2026-09-18T01:00:00+00:00",
                 mode: str = "fixture") -> None:
        self.responses, self.fetched_at, self.calls = responses, fetched_at, []
        self.mode = mode

    def fetch(self, endpoint: str, params: dict) -> tuple[Any, str]:
        self.calls.append((endpoint, params))
        if endpoint not in self.responses:
            raise SourceUnavailable(f"Fixture tidak menyediakan {endpoint}.")
        response = self.responses[endpoint]
        if isinstance(response, Exception):
            raise response
        return (response(params) if callable(response) else response), self.fetched_at


class SnapshotStore:
    def __init__(self, run_dir: Path) -> None:
        self.directory = Path(run_dir) / "sources"

    def _path(self, source_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in source_id)
        return self.directory / f"{safe}.json"

    def save(self, source: dict, payload: Any, key: str | None) -> dict:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(source["source_id"])
        source = {**source, "sha256": hashlib.sha256(canonical(payload).encode()).hexdigest(), "path": str(path)}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"source": source, "request_key": key, "payload": payload},
                                        ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(path)
        return source

    def payload(self, source: dict) -> Any:
        if not source or not source.get("path"):
            return None
        try:
            return json.loads(Path(source["path"]).read_text(encoding="utf-8"))["payload"]
        except (OSError, ValueError, KeyError):
            return None

    def text(self, source: dict) -> str:
        payload = self.payload(source)
        if source.get("kind") == "sectors_news" and isinstance(payload, dict):
            return f"{payload.get('title') or ''}\n{payload.get('body') or ''}"
        return canonical(payload) if payload is not None else ""


def _symbol(value) -> str:
    return str(value or "").upper().removesuffix(".JK")


def _credits(endpoint: str, payload: Any, params: dict) -> int:
    if endpoint == "company_report":
        return len(params["sections"])
    if endpoint == "quarterly":
        return len(payload) if isinstance(payload, list) else 0
    return 1


def fetch_snapshot(gateway, store: SnapshotStore, ticker: str, as_of: date, live_as_of: bool, *, quarters: int,
                   price_days: int, news_days: int, news_limit: int) -> dict[str, dict]:
    """Ambil semua sumber yang dibutuhkan keempat panel. Kegagalan satu sumber tidak menghentikan yang lain."""
    sources: dict[str, dict] = {}
    base = {"ticker": ticker, "mode": gateway.mode}

    def record(source_id, kind, endpoint, params, *, available_at=None, period=None, validate=None):
        key = request_key(endpoint, params)
        try:
            payload, fetched_at = gateway.fetch(endpoint, params)
        except Exception as error:  # noqa: BLE001 - status sumber harus eksplisit, bukan menghentikan run
            detail = str(error)[:400]
            credits = 1 if endpoint == "daily" and "HTTP 404" in detail else 0
            sources[source_id] = {**base, "source_id": source_id, "kind": kind, "endpoint": endpoint, "params": params,
                                  "fetched_at": now_iso(), "status": "error", "error": detail,
                                  "credits": credits if gateway.mode == "live" else 0}
            return None
        problem = validate(payload) if validate else None
        empty = payload in (None, [], {}) or (isinstance(payload, dict) and payload.get("results") == [])
        source = {**base, "source_id": source_id, "kind": kind, "endpoint": endpoint, "params": params,
                  "fetched_at": fetched_at, "available_at": available_at(payload, fetched_at) if available_at else None,
                  "period": period(payload) if period else None,
                  "status": "error" if problem else ("empty" if empty else "ok"), "error": problem,
                  "credits": _credits(endpoint, payload, params) if gateway.mode == "live" else 0}
        sources[source_id] = store.save(source, payload, key)
        return None if problem else payload

    def same_ticker(payload):
        if isinstance(payload, dict) and payload.get("symbol") and _symbol(payload["symbol"]) != ticker:
            return f"Respons milik {_symbol(payload['symbol'])}, bukan {ticker}."
        if isinstance(payload, list) and any(_symbol(row.get("symbol")) not in ("", ticker) for row in payload
                                             if isinstance(row, dict)):
            return f"Sebagian baris bukan milik {ticker}."
        return None

    if live_as_of:
        record("sectors:company_report", "sectors_company_report", "company_report",
               {"ticker": ticker, "sections": list(REPORT_SECTIONS)},
               available_at=lambda _payload, fetched: fetched, validate=same_ticker)
    else:
        sources["sectors:company_report"] = {
            **base, "source_id": "sectors:company_report", "kind": "sectors_company_report", "endpoint": "company_report",
            "params": {"ticker": ticker}, "fetched_at": now_iso(), "status": "excluded", "credits": 0,
            "error": "Company report hanya berisi keadaan terkini; tidak diambil untuk tanggal acuan historis."}

    record("sectors:quarterly", "sectors_quarterly", "quarterly", {"ticker": ticker, "n_quarters": quarters},
           period=lambda rows: ", ".join(sorted({str(row.get("date")) for row in rows if isinstance(row, dict)}))
           if isinstance(rows, list) else None, validate=same_ticker)

    for start, end in daily_windows(as_of, price_days):
        record(f"sectors:daily:{start}:{end}", "sectors_daily", "daily", {"ticker": ticker, "start": start, "end": end},
               available_at=lambda rows, _fetched: max((str(row.get("date")) for row in rows if isinstance(row, dict)),
                                                       default=None) if isinstance(rows, list) else None,
               period=lambda _rows, s=start, e=end: f"{s}–{e}", validate=same_ticker)

    record("sectors:corporate_actions", "sectors_corporate_actions", "corporate_actions", {"ticker": ticker},
           validate=same_ticker)

    news_start = (as_of - timedelta(days=news_days - 1)).isoformat()
    listing = record("sectors:news", "sectors_news", "news",
                     {"ticker": ticker, "start": news_start, "end": as_of.isoformat(), "limit": news_limit},
                     period=lambda _payload: f"{news_start}–{as_of.isoformat()}")
    for article in ((listing or {}).get("results") or []) if isinstance(listing, dict) else []:
        if not isinstance(article, dict):
            continue
        symbols = {_symbol(symbol) for symbol in article.get("symbols") or []}
        identity = article.get("source") or article.get("title") or ""
        article_id = "news:" + hashlib.sha256(identity.encode()).hexdigest()[:10]
        stamp = str(article.get("timestamp") or "")
        source = {**base, "source_id": article_id, "kind": "sectors_news", "endpoint": "news",
                  "params": {"url": article.get("source")}, "fetched_at": sources["sectors:news"]["fetched_at"],
                  "available_at": stamp or None, "period": stamp[:10] or None,
                  "status": "ok" if ticker in symbols else "error",
                  "error": None if ticker in symbols else f"Artikel tidak menandai {ticker}.", "credits": 0}
        sources[article_id] = store.save(source, article, None)
    return sources

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import socket
import time
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from scrapling.fetchers import Fetcher

from app.research.context import excerpt
from app.research.models import Evidence

USER_AGENT = "SignalGateResearch/1.0"


def public_url(url: str) -> str:
    url = urldefrag(url)[0]
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Sumber harus berupa URL HTTP(S) publik tanpa kredensial.")
    if parsed.port not in {None, 80, 443}:
        raise ValueError("Port sumber tidak didukung.")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Alamat sumber nonpublik ditolak.")
    return url


class ScraplingSource:
    def __init__(self):
        self.robots: dict[str, RobotFileParser] = {}
        self.last_request: dict[str, float] = {}

    def _get(self, url: str):
        public_url(url)
        # One retry: idxchannel timed out twice with 0 bytes, then answered in 0.4 s with the same user agent.
        return Fetcher.get(url, timeout=20, retries=1, follow_redirects=False,
                           stealthy_headers=False, headers={"User-Agent": USER_AGENT})

    def response(self, url: str):
        """Public response after robots, throttling and redirect checks."""
        for _ in range(5):
            url = public_url(url)
            parsed = urlsplit(url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin not in self.robots:
                page = self._get(origin + "/robots.txt")
                robot = RobotFileParser()
                if page.status in {404, 410}:
                    robot.parse([])
                elif page.status == 200:
                    robot.parse(page.body.decode("utf-8", errors="replace").splitlines())
                else:
                    raise ValueError(f"robots.txt tidak tersedia (HTTP {page.status}).")
                self.robots[origin] = robot
            robot = self.robots[origin]
            if not robot.can_fetch(USER_AGENT, url):
                raise ValueError("robots.txt melarang pengambilan sumber ini.")
            delay = max(1, robot.crawl_delay(USER_AGENT) or 0)
            if delay > 20:
                raise ValueError("Crawl delay melebihi batas sesi; sumber dilewati.")
            remaining = delay - (time.monotonic() - self.last_request.get(origin, 0))
            if remaining > 0:
                time.sleep(remaining)
            self.last_request[origin] = time.monotonic()
            page = self._get(url)
            if page.status in {301, 302, 303, 307, 308}:
                location = page.headers.get("location") or page.headers.get("Location")
                if not location:
                    raise ValueError("Redirect tanpa lokasi.")
                url = urljoin(url, location)
                continue
            if page.status != 200:
                raise ValueError(f"Sumber mengembalikan HTTP {page.status}.")
            return page, url
        raise ValueError("Terlalu banyak redirect.")

    def fetch(self, url: str) -> tuple[str, str, list[str]]:
        page, url = self.response(url)
        content_type = page.headers.get("content-type", page.headers.get("Content-Type", ""))
        if "html" not in content_type.lower():
            raise ValueError("Versi pertama hanya mengekstrak HTML; PDF/biner belum didukung.")
        if len(page.body) > 2_000_000:
            raise ValueError("Halaman melebihi batas 2 MB.")
        nodes = page.css("article") or page.css("main") or page.css("body")
        text = "\n".join(node.get_all_text(strip=True, ignore_tags=("script", "style", "nav", "footer"))
                         for node in nodes)[:16000]
        if len(text.strip()) < 80:
            raise ValueError("Teks sumber terlalu pendek atau membutuhkan JavaScript.")
        links = list(dict.fromkeys(urldefrag(urljoin(url, link))[0]
                     for link in page.css("a::attr(href)").getall()
                     if urlsplit(urljoin(url, link)).scheme in {"http", "https"}))[:80]
        title = page.css("title::text").get() or url
        return title, text, links


class EvidenceStore:
    def __init__(
        self,
        directory: Path,
        max_pages: int = 6,
        scraper=None,
        terms: list[str] | None = None,
        context_chars: int = 3000,
        total_context_chars: int = 16000,
    ):
        self.directory = directory
        (directory / "evidence").mkdir(parents=True, exist_ok=True)
        self.scraper = scraper or ScraplingSource()
        self.max_pages = max_pages
        self.terms = terms or []
        self.context_chars = context_chars
        self.total_context_chars = total_context_chars
        self.items: list[Evidence] = []
        self.attempted: set[str] = set()
        self.failures: list[dict] = []
        self.document_urls: dict[str, str] = {}
        self.duplicates: list[dict] = []

    def write(self, relative: str, data):
        path = self.directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(data, "model_dump"):
            data = data.model_dump(mode="json")
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)

    def add(self, kind: str, url: str, title: str, text: str, links=None, **metadata):
        retrieved_at = metadata.pop("retrieved_at", datetime.now(timezone.utc).isoformat())
        item = Evidence(id=f"E{len(self.items) + 1:03}", kind=kind, url=url, title=title,
                        text=text, links=links or [], retrieved_at=retrieved_at,
                        sha256=hashlib.sha256(text.encode()).hexdigest(), **metadata)
        self.items.append(item)
        self.write(f"evidence/{item.id}.json", item)
        return item

    def collect(self, urls: list[str]):
        for url in urls:
            url = urldefrag(url)[0]
            if url in self.attempted:
                continue
            if len(self.attempted) - len(self.duplicates) >= self.max_pages:
                self.failures.append({"url": url, "error": "Batas halaman tercapai."})
                continue
            self.attempted.add(url)
            try:
                if hasattr(self.scraper, "fetch_document"):
                    document = self.scraper.fetch_document(url)
                    # IDX lists the English and Indonesian attachment under separate URLs, often byte-identical.
                    if document.sha256 in self.document_urls:
                        self.duplicates.append({"url": url, "same_as": self.document_urls[document.sha256]})
                        continue
                    self.document_urls[document.sha256] = url
                    for page in document.pages:
                        number = page["number"]
                        self.add("pdf" if document.format == "pdf" else "scrapling",
                                 document.url + (f"#page={number}" if number else ""), document.title,
                                 page["text"], [link.url for link in document.links],
                                 retrieved_at=document.fetched_at, page_number=number,
                                 document_sha256=document.sha256)
                    self.failures.extend({"url":url, "error":warning} for warning in document.warnings)
                else:
                    title, text, links = self.scraper.fetch(url)
                    self.add("scrapling", url, title, text, links)
            except Exception as error:
                safe_detail = str(error) if isinstance(error, ValueError) else type(error).__name__
                self.failures.append({"url": url, "error": safe_detail})
        self.write("evidence/fetch_failures.json", self.failures)
        if self.duplicates:
            self.write("evidence/duplicate_documents.json", self.duplicates)

    def context(self) -> dict:
        if any(item.kind == "pdf" for item in self.items):
            from app.research.retrieval import retrieve
            selected = retrieve(self.items, self.terms, self.context_chars, self.total_context_chars)
            self.write("retrieval.json", {"selected_chunks":selected,
                       "total_evidence_pages":len(self.items), "method":"keyword_chunks"})
            return {"evidence":selected, "fetch_failures":self.failures}
        evidence = [
            {
                "id": item.id,
                "kind": item.kind,
                "url": item.url,
                "title": item.title,
                "text": excerpt(item.text, self.terms, self.context_chars)
                if item.kind == "scrapling"
                else item.text[: self.context_chars],
            }
            for item in self.items
        ]
        return {"evidence": evidence, "fetch_failures": self.failures}

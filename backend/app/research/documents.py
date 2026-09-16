"""Reusable HTML/PDF snapshots. Scraping happens once, inference can replay offline."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit

from pydantic import BaseModel, Field
from pypdf import PdfReader

from app.research.evidence import ScraplingSource


class Link(BaseModel):
    url: str
    text: str = ''


class Document(BaseModel):
    extractor_version: str = 'html-pdf-v1'
    url: str
    title: str
    format: str
    sha256: str
    fetched_at: str
    pages: list[dict]
    links: list[Link] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def text(self):
        return '\n'.join(page['text'] for page in self.pages)


def extract_pdf(body: bytes, max_pages: int = 150) -> tuple[list[dict], list[str]]:
    if len(body) > 12_000_000:
        raise ValueError('PDF melebihi batas 12 MB.')
    if not body.lstrip().startswith(b'%PDF-'):
        raise ValueError('Sumber bukan berkas PDF yang valid.')
    reader = PdfReader(BytesIO(body))
    if reader.is_encrypted:
        raise ValueError('PDF terenkripsi tidak diproses.')
    if len(reader.pages) > max_pages:
        raise ValueError(f'PDF melebihi batas {max_pages} halaman; tidak dipotong diam-diam.')
    pages, warnings = [], []
    for number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ''
        if len(text.strip()) < 40:
            warnings.append(f'Halaman {number} minim teks; periksa halaman kosong atau kebutuhan OCR.')
        if len(text) > 50000:
            raise ValueError('Teks satu halaman PDF melebihi batas 50.000 karakter.')
        pages.append({'number': number, 'text': text})
    return pages, warnings


class DocumentSource:
    def __init__(self, directory: Path, mode='prefer_cache', ttl=86400, max_pdf_pages=150, transport=None):
        self.directory = Path(directory)
        self.mode, self.ttl, self.max_pdf_pages = mode, ttl, max_pdf_pages
        self.transport = transport or ScraplingSource()
        self.hits = self.fetches = 0

    def fetch_document(self, url: str) -> Document:
        url = urldefrag(url)[0]
        parsed = urlsplit(url)
        if parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Dokumen harus memakai URL HTTP(S) tanpa kredensial.')
        key = hashlib.sha256(url.encode()).hexdigest()
        index = self.directory / 'urls' / f'{key}.json'
        if self.mode != 'refresh' and index.exists():
            try:
                cached = Document.model_validate_json(index.read_text())
                if cached.extractor_version != 'html-pdf-v1':
                    raise ValueError('Versi ekstraksi snapshot tidak cocok.')
                if cached.format == 'pdf' and len(cached.pages) > self.max_pdf_pages:
                    raise ValueError('PDF cache melebihi batas halaman saat ini.')
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached.fetched_at)).total_seconds()
                if self.mode == 'offline' or 0 <= age <= self.ttl:
                    self.hits += 1
                    return cached
            except (ValueError, OSError, TypeError):
                if self.mode == 'offline':
                    raise ValueError('Snapshot offline rusak.') from None
        if self.mode == 'offline':
            raise ValueError('Snapshot belum tersedia; mode offline tidak mengakses jaringan.')
        page, final_url = self.transport.response(url)
        body = bytes(page.body)
        digest = hashlib.sha256(body).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        content_type = str(page.headers.get('content-type', page.headers.get('Content-Type', ''))).lower()
        if body.lstrip().startswith(b'%PDF-') or 'application/pdf' in content_type:
            pages, warnings = extract_pdf(body, self.max_pdf_pages)
            document = Document(url=final_url, title=Path(urlsplit(final_url).path).name or 'PDF',
                                format='pdf', sha256=digest, fetched_at=now, pages=pages, warnings=warnings)
            extension = 'pdf'
        else:
            if 'html' not in content_type:
                raise ValueError('Format sumber belum didukung; gunakan HTML atau PDF.')
            if len(body) > 2_000_000:
                raise ValueError('HTML melebihi batas 2 MB.')
            nodes = page.css('article') or page.css('main') or page.css('body')
            text = '\n'.join(node.get_all_text(strip=True, ignore_tags=('script', 'style', 'nav', 'footer')) for node in nodes)
            if len(text.strip()) < 80:
                raise ValueError('Halaman minim teks atau membutuhkan JavaScript.')
            links, seen = [], set()
            for node in page.css('a[href]'):
                target = urldefrag(urljoin(final_url, node.attrib['href']))[0]
                if urlsplit(target).scheme in {'http', 'https'} and target not in seen:
                    links.append(Link(url=target, text=node.get_all_text(strip=True)[:500]))
                    seen.add(target)
            warnings = []
            if len(text) > 150000:
                raise ValueError('HTML melebihi batas ekstraksi; gunakan halaman artikel spesifik.')
            document = Document(url=final_url, title=page.css('title::text').get() or final_url, format='html',
                                sha256=digest, fetched_at=now, pages=[{'number':None, 'text':text}],
                                links=links, warnings=warnings)
            extension = 'html'
        self.fetches += 1
        # Content-addressed immutable originals plus a replaceable URL index.
        raw = self.directory / 'objects' / f'{digest}.{extension}'
        raw.parent.mkdir(parents=True, exist_ok=True)
        if not raw.exists():
            raw.write_bytes(body)
        metadata = raw.with_suffix('.json')
        if not metadata.exists():
            metadata.write_text(document.model_dump_json(indent=2))
        index.parent.mkdir(parents=True, exist_ok=True)
        temporary = index.with_suffix('.tmp')
        temporary.write_text(document.model_dump_json(indent=2))
        temporary.replace(index)
        return document

    def fetch(self, url):
        document = self.fetch_document(url)
        return document.title, document.text, [link.url for link in document.links]

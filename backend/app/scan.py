"""Scan disclosure/news listings by keyword, queue candidates, then review PDFs.

Development workflow: zero Sectors requests. Coverage is explicitly limited to the
listing pages and ticker universe supplied, never reported as a complete IDX crawl.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

from app.config import REPO_ROOT, get_settings
from app.llm.factory import build_provider
from app.pipeline.hypothesize import (CONTROL_CHANGE_KEYWORDS, NON_PREEMPTIVE_KEYWORDS,
                                      RIGHTS_ISSUE_KEYWORDS, classify)
from app.pipeline.schema import ActionBucket, CandidateEvent
from app.research.documents import DocumentSource

STOP_TICKERS = {'HMET', 'PMTH', 'PMHM', 'RUPS', 'BEI', 'OJK', 'IDX', 'IHSG', 'IPO', 'BUMN', 'UMKM',
               'APBN', 'APBD', 'BANK', 'DATA', 'NEWS', 'READ', 'HTTP', 'HTML', 'SAAT', 'DARI', 'DANA', 'AKAN',
               'YANG', 'TBKS', 'USDT', 'GOTOX', 'FIFA', 'ASEAN', 'ESOP', 'MSOP'}
DISCOVERY_KEYWORDS = ('rights issue', 'right issue', 'hmetd', 'pmthmetd', 'pmhmetd', 'private placement',
            'penambahan modal', 'inbreng', 'perubahan pengendali', 'perubahan kegiatan usaha',
            'perubahan bidang usaha', 'pengambilalihan', 'ambil alih', 'akuisisi', 'divestasi', 'prospektus',
            'capital increase', 'pre-emptive', 'preemptive', 'prospectus', 'acquisition', 'change of control',
            'transaksi afiliasi', 'affiliate', 'conflict of interest', 'benturan kepentingan',
            # IDX menamai formulir keterbukaannya begini, dan di situlah aksi korporasi dilaporkan.
            'fakta material', 'material fact', 'informasi material',
            # Ragam jurnalistik Indonesia untuk pengambilalihan dan penyuntikan modal.
            'caplok', 'mencaplok', 'suntik modal', 'suntikan modal', 'penawaran tender')

# Discovery tidak boleh menyaring habis aksi yang justru diprioritaskan classifier. Daftar ini pernah
# menyimpang dari hypothesize.py dan kehilangan 'takeover', sehingga pengumuman berbahasa Inggris IDX
# untuk pengambilalihan tidak pernah terlihat. Bucket general_action sengaja tidak ikut: isinya luas
# ('dividen', 'ekspansi') dan akan membanjiri antrean dengan hal yang tidak bisa diskor.
PRIORITISED_BUCKET_KEYWORDS = (*CONTROL_CHANGE_KEYWORDS, *NON_PREEMPTIVE_KEYWORDS, *RIGHTS_ISSUE_KEYWORDS)
KEYWORDS = tuple(dict.fromkeys((*DISCOVERY_KEYWORDS, *PRIORITISED_BUCKET_KEYWORDS)))
TITLE_TICKER = re.compile(r'\[\s*([A-Z0-9]{4})\s*\]\s*$')
# Tautan halaman berikutnya hanya berisi penandanya; judul berita yang diawali angka bukan paginasi.
PAGE_LINK = re.compile(r'(?:next|berikutnya|selanjutnya)\W*|\d{1,3}', re.I)
ATTACHMENT_NAME = re.compile(r'^\d{8}_([A-Z0-9]{4})_.+\.(?:pdf|xlsx|xls|docx|doc)$')
IDX_HOSTS = ('idx.id', 'idx.co.id')


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temporary.replace(path)


def matches(text):
    lowered = text.lower()
    return [keyword for keyword in KEYWORDS if keyword in lowered]


def ticker_candidates(text, universe=None, explicit_only=False):
    explicit = re.findall(r'[\[(]\s*([A-Z]{4})\s*[\])]', text)
    explicit += re.findall(r'(?:kode saham|ticker|stock code)\s*[:：]?\s*([A-Z]{4})\b', text)
    # In prose, require a ticker marker or a known company name; uppercase words are not an IDX registry.
    raw = explicit if explicit or explicit_only else re.findall(r'\b[A-Z]{4}\b', text)
    codes = {code for code in raw if code not in STOP_TICKERS}
    if universe:
        lowered = text.lower()
        codes &= set(universe)
        codes |= {ticker for ticker, name in universe.items() if len(name) >= 10 and name.lower() in lowered}
    return sorted(codes)


def announcement_groups(links):
    groups, current = [], None
    for link in links:
        text = ' '.join(link.text.split())
        title = TITLE_TICKER.search(text)
        attachment = ATTACHMENT_NAME.match(text)
        if title:
            current = {'ticker': title.group(1), 'title': text, 'urls': [link.url], 'attachments': []}
            groups.append(current)
        elif attachment:
            if current is None or current['ticker'] != attachment.group(1):
                current = {'ticker': attachment.group(1), 'title': text, 'urls': [], 'attachments': []}
                groups.append(current)
            current['urls'].append(link.url)
            current['attachments'].append(text)
        else:
            current = None
    return groups


def pdf_links(urls):
    return [url for url in urls if '.pdf' in url.lower()]


def load_universe(path):
    if path is None:
        return {}, {'status':'not_supplied', 'ticker_count':0}
    packet = json.loads(Path(path).read_text())
    companies = packet.get('companies', [])
    mapping = {row['ticker'].upper(): row['name'] for row in companies if re.fullmatch(r'[A-Za-z]{4}', row['ticker'])}
    if not mapping or not packet.get('source_url') or not packet.get('as_of'):
        raise ValueError('Universe perlu companies, source_url dan as_of; jangan mengarang daftar seluruh IDX.')
    return mapping, {'status':'supplied', 'ticker_count':len(mapping), 'source_url':packet['source_url'], 'as_of':packet['as_of']}


class Scanner:
    def __init__(self, source, directory, universe=None, max_articles=20, max_listing_pages=3):
        self.source, self.directory = source, Path(directory)
        self.universe = universe or {}
        self.max_articles, self.max_listing_pages = max_articles, max_listing_pages

    def queue(self, report, ticker, headline, body, source_url, documents, keywords, fingerprint, ticker_source):
        bucket, _ = classify(headline, body)
        event = CandidateEvent(ticker=ticker, headline=headline, body=body[:1500], source_url=source_url,
                               published_at='', bucket=bucket or ActionBucket.general_action, matched_keywords=keywords)
        key = hashlib.sha256(f'{ticker}:{source_url}:{fingerprint}'.encode()).hexdigest()[:24]
        path = self.directory / 'queue' / f'{key}.json'
        entry = {'id':key, 'event':event.model_dump(mode='json'), 'article_sha256':fingerprint,
                 'first_seen':report['created_at'], 'ticker_source':ticker_source,
                 'ticker_verified':ticker in self.universe or ticker_source == 'idx_announcement_listing',
                 'pdf_urls':list(dict.fromkeys(documents)),
                 'status':'pending_pdf_review' if documents else 'needs_document'}
        if path.exists():
            report['duplicates'] += 1
            entry = json.loads(path.read_text())
        else:
            write_json(path, entry)
        report['candidates'].append(entry)

    def attach_announcement_documents(self, report, announcements):
        for entry in report['candidates']:
            if entry['status'] != 'needs_document':
                continue
            wanted = set(entry['event']['matched_keywords'])
            documents = [url for group, keywords, _ in announcements
                         if group['ticker'] == entry['event']['ticker'] and wanted & set(keywords)
                         for url in pdf_links(group['urls'])]
            if documents:
                entry.update(pdf_urls=list(dict.fromkeys(documents)), status='pending_pdf_review',
                             documents_from='idx_announcement_same_ticker')
                write_json(self.directory / 'queue' / f"{entry['id']}.json", entry)

    def discover(self, sources):
        report = {'run_id':uuid4().hex[:12], 'created_at':datetime.now(timezone.utc).isoformat(),
                  'coverage':'partial_configured_sources', 'all_idx_covered':False,
                  'listing_pages':[], 'announcements_matched':0, 'articles_checked':0, 'candidates':[],
                  'failures':[], 'duplicates':0, 'budget_exhausted':False, 'keyword_filter':list(KEYWORDS)}
        listings, seen_listings, articles, announcements = list(sources), set(), {}, []
        while listings and len(seen_listings) < self.max_listing_pages:
            url = listings.pop(0)
            if url in seen_listings:
                continue
            seen_listings.add(url)
            try:
                page = self.source.fetch_document(url)
                report['listing_pages'].append({'url':url, 'fetched_at':page.fetched_at, 'sha256':page.sha256,
                                               'links_seen':len(page.links)})
                # Explicit source may itself be an article or a PDF.
                if matches(page.title) or page.format == 'pdf':
                    articles.setdefault(url, page.title)
                groups = announcement_groups(page.links)
                grouped = {link_url for group in groups for link_url in group['urls']}
                host = urlsplit(url).hostname or ''
                for group in groups:
                    keywords = matches(' '.join([group['title'], *group['attachments']]))
                    if keywords:
                        announcements.append((group, keywords, host))
                for link in page.links:
                    if link.url not in grouped and matches(link.text + ' ' + link.url.replace('-', ' ')):
                        articles.setdefault(link.url, link.text)
                    if PAGE_LINK.fullmatch(link.text.strip()) and urlsplit(link.url).netloc == urlsplit(url).netloc:
                        listings.append(link.url)
            except Exception as error:
                report['failures'].append({'url':url, 'error':str(error) if isinstance(error, ValueError) else type(error).__name__})
        report['budget_exhausted'] = bool(listings) or len(articles) > self.max_articles
        report['announcements_matched'] = len(announcements)
        for group, keywords, host in announcements:
            ticker_source = 'idx_announcement_listing' if host.endswith(IDX_HOSTS) else 'listing_title'
            fingerprint = hashlib.sha256('\n'.join(group['urls']).encode()).hexdigest()
            self.queue(report, group['ticker'], group['title'], '\n'.join(group['attachments']), group['urls'][0],
                       pdf_links(group['urls']), keywords, fingerprint, ticker_source)
        for url, title in list(articles.items())[:self.max_articles]:
            try:
                article = self.source.fetch_document(url)
                report['articles_checked'] += 1
                relevant = matches(title + ' ' + article.title + ' ' + article.text)
                if not relevant:
                    continue
                # Prefer headline identity; body can mention peers unrelated to the action.
                tickers = ticker_candidates(title + ' ' + article.title, self.universe)
                if not tickers:
                    tickers = ticker_candidates(article.text[:5000], self.universe, explicit_only=True)
                if not tickers:
                    report['failures'].append({'url':url, 'error':'Kata kunci cocok tetapi ticker belum teridentifikasi.'})
                    continue
                documents = pdf_links(link.url for link in article.links)
                if article.format == 'pdf':
                    documents.insert(0, url)
                # Listing link text may append a relative timestamp ("4 jam yang lalu") on its own line.
                headline = next((line.strip() for line in title.splitlines() if line.strip()), article.title)
                for ticker in tickers:
                    self.queue(report, ticker, headline, article.text, url, documents, relevant,
                               article.sha256, 'article_text')
            except Exception as error:
                report['failures'].append({'url':url, 'error':str(error) if isinstance(error, ValueError) else type(error).__name__})
        self.attach_announcement_documents(report, announcements)
        report['coverage_note'] = 'Tidak ada temuan berarti tidak ditemukan pada sumber yang diperiksa, bukan bukti tidak ada aksi korporasi di IDX.'
        write_json(self.directory / 'runs' / f"{report['run_id']}.json", report)
        write_json(self.directory / 'latest.json', report)
        return report


def document_map(documents):
    mappings = {}
    for value in documents:
        ticker, separator, url = value.partition('=')
        if not separator or not url.startswith(('https://', 'http://')):
            raise ValueError('--document harus TICKER=https://dokumen.pdf')
        mappings.setdefault(ticker.upper(), []).append(url)
    return mappings


def research_queue_items(settings, directory, limit, documents=()):
    """Yield (event, outcome, item) per kandidat antrean. Nol permintaan Sectors.

    Dipakai CLI maupun endpoint /scan/run, supaya keduanya meriset dengan cara yang sama persis.
    Pemanggil wajib menghabiskan generator ini agar engine ditutup tepat waktu.
    """
    mappings = document_map(documents)
    engine = build_provider(settings)
    try:
        processed = 0
        for path in sorted((Path(directory) / 'queue').glob('*.json')):
            item = json.loads(path.read_text())
            if item['status'] == 'completed':
                continue
            if processed >= limit:
                break
            event = CandidateEvent.model_validate(item['event'])
            pdfs = mappings.get(event.ticker, []) + item['pdf_urls']
            if not pdfs:
                continue
            engine.settings = settings.model_copy(update={'research_source_urls':list(dict.fromkeys(pdfs))})
            outcome = engine.research(event, None, require_pdf=True, require_sectors=False)
            item.update(status=outcome.status, case_id=outcome.case_id, label=outcome.verdict.label.value,
                        document_status=outcome.document_status, processed_at=datetime.now(timezone.utc).isoformat())
            write_json(path, item)
            processed += 1
            yield event, outcome, item
    finally:
        engine.close()


def research_queue(settings, directory, limit, documents):
    processed = 0
    for _event, _outcome, item in research_queue_items(settings, directory, limit, documents):
        print(json.dumps({key:item.get(key) for key in ('id','status','case_id','label','document_status')}), flush=True)
        processed += 1
    return processed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['discover','research'])
    parser.add_argument('--source', action='append', help='Halaman daftar berita/pengumuman atau dokumen publik')
    parser.add_argument('--directory', type=Path, default=REPO_ROOT / 'data' / 'scans')
    parser.add_argument('--universe', type=Path, help='JSON daftar ticker dengan sumber dan tanggal snapshot')
    cache_mode = parser.add_mutually_exclusive_group()
    cache_mode.add_argument('--offline', action='store_true')
    cache_mode.add_argument('--refresh', action='store_true')
    parser.add_argument('--limit', type=int, default=3, help='Jumlah kandidat yang dianalisis; discovery memakai SCAN_MAX_ARTICLES')
    parser.add_argument('--document', action='append', default=[], help='TICKER=URL PDF untuk kandidat tanpa tautan dokumen')
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 20:
        parser.error('--limit harus 1..20')
    settings = get_settings()
    mode = 'offline' if args.offline else 'refresh' if args.refresh else settings.source_cache_mode
    settings = settings.model_copy(update={'source_cache_mode':mode})
    if args.command == 'discover':
        universe, metadata = load_universe(args.universe)
        source = DocumentSource(settings.research_library_dir, mode, settings.source_cache_ttl_seconds,
                                settings.research_pdf_max_pages)
        scanner = Scanner(source, args.directory, universe, settings.scan_max_articles, settings.scan_max_listing_pages)
        report = scanner.discover(args.source or settings.scan_sources)
        report['universe'] = metadata
        report['source_cache'] = {'hits':source.hits, 'fetches':source.fetches}
        write_json(args.directory / 'runs' / f"{report['run_id']}.json", report)
        write_json(args.directory / 'latest.json', report)
        print(json.dumps({k:v for k,v in report.items() if k != 'candidates'}, ensure_ascii=False, indent=2))
        print(f"Kandidat: {len(report['candidates'])}; lihat {args.directory / 'latest.json'}")
    else:
        print(f"Kasus diproses: {research_queue(settings, args.directory, args.limit, args.document)}")


if __name__ == '__main__':
    main()

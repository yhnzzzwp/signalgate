"""Scan disclosure/news listings by keyword, queue candidates, then review PDFs.

Development workflow: zero Sectors requests. Coverage is explicitly limited to the
listing pages and ticker universe supplied, never reported as a complete IDX crawl.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
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
from app.research.models import ResearchOutcome
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
ATTACHMENT_NAME = re.compile(r'^(\d{8})_([A-Z0-9]{4})_.+\.(?:pdf|xlsx|xls|docx|doc)$')
# Nomor pengumuman IDX pada nama lampiran: `..._32148872_lamp1.pdf`. Inilah identitas aksinya.
# Tanggal saja tidak cukup: satu emiten bisa menerbitkan beberapa pengumuman berbeda di hari yang
# sama -- IDEA 16/09/2026 punya 32148872 (fakta material) dan 32148902 (pengumuman RUPS).
ANNOUNCEMENT_NUMBER = re.compile(r'_(\d{4,})_lamp\d+\.', re.I)
IDX_HOSTS = ('idx.id', 'idx.co.id')

# Kandidat yang sudah menghasilkan putusan tidak diriset ulang: menekan tombol scan dua kali dulu
# mengulang pekerjaan menit-menitan dan menyimpan baris duplikat ke dashboard. `needs_review` tetap
# terhitung sebagai putusan -- itu justru hasil paling umum.
SETTLED_STATUSES = frozenset({'completed', 'needs_review'})

# Kegagalan lingkungan layak dicoba lagi, tetapi tidak boleh terus memakan jatah `limit` sehingga
# kandidat yang belum pernah dicoba tidak pernah kebagian giliran. Jeda menaik plus batas percobaan.
RETRY_BACKOFF_SECONDS = (300, 1800, 7200)
MAX_ATTEMPTS = len(RETRY_BACKOFF_SECONDS) + 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def queue_path(directory, item_id):
    return Path(directory) / 'queue' / f'{item_id}.json'


def retry_schedule(attempt_count):
    """Kapan boleh dicoba lagi setelah `attempt_count` percobaan; None berarti berhenti mencoba."""
    if attempt_count >= MAX_ATTEMPTS:
        return None
    return (_now() + timedelta(seconds=RETRY_BACKOFF_SECONDS[attempt_count - 1])).isoformat()


def is_due(item, now=None):
    """Kandidat gagal hanya mengambil giliran ketika jeda ulangnya sudah lewat."""
    schedule = item.get('next_retry_at')
    if schedule is None:
        return item.get('attempt_count', 0) < MAX_ATTEMPTS
    try:
        return datetime.fromisoformat(schedule) <= (now or _now())
    except (TypeError, ValueError):
        return True


def needs_publication(item):
    """Sudah berputusan tetapi belum pernah sampai ke database."""
    return item.get('status') in SETTLED_STATUSES and not item.get('published_at')


def mark_published(directory, item_id):
    """Dipanggil pemakai SETELAH commit database, bukan oleh peneliti.

    Menandai selesai sebelum kartunya tersimpan berarti hasil yang hilang saat commit gagal tidak
    akan pernah dipulihkan: run berikutnya melewatinya karena statusnya sudah berputusan.
    """
    path = queue_path(directory, item_id)
    item = json.loads(path.read_text())
    item['published_at'] = _now().isoformat()
    write_json(path, item)
    return item


def load_outcome(settings, case_id):
    """Muat ulang putusan dari artefak kasus, supaya publikasi ulang tidak memanggil model lagi."""
    if not case_id:
        return None
    path = Path(settings.research_cases_dir) / case_id / 'decision.json'
    try:
        return ResearchOutcome.model_validate_json(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temporary.replace(path)


def _iso_date(compact):
    """'20260916' -> '2026-09-16'; nama lampiran IDX adalah satu-satunya tanggal yang kita punya."""
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}" if compact and len(compact) == 8 else ""


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
            current = {'ticker': title.group(1), 'title': text, 'urls': [link.url], 'attachments': [],
                       'dates': set(), 'numbers': set()}
            groups.append(current)
        elif attachment:
            if current is None or current['ticker'] != attachment.group(2):
                current = {'ticker': attachment.group(2), 'title': text, 'urls': [], 'attachments': [],
                           'dates': set(), 'numbers': set()}
                groups.append(current)
            current['urls'].append(link.url)
            current['attachments'].append(text)
            current['dates'].add(attachment.group(1))
            number = ANNOUNCEMENT_NUMBER.search(text)
            if number:
                current['numbers'].add(number.group(1))
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
        """Pasangkan artikel dengan lampiran pengumuman IDX untuk ticker yang sama.

        Kecocokan ticker dan satu kata kunci saja tidak cukup: satu emiten bisa punya dua rights
        issue di tahun berbeda, dan lampiran keduanya akan tergabung ke satu kasus. Kutipannya
        memang ada di dokumen, tetapi menjelaskan aksi yang lain. Tanggal pengumuman pada nama
        lampiran (`YYYYMMDD_TICKER_...`) dipakai sebagai identitas aksi; ketika ambigu, asosiasinya
        tidak dinyatakan selesai melainkan diserahkan ke pemeriksaan manusia.
        """
        for entry in report['candidates']:
            if entry['status'] != 'needs_document':
                continue
            wanted = set(entry['event']['matched_keywords'])
            matching = [group for group, keywords, _ in announcements
                        if group['ticker'] == entry['event']['ticker'] and wanted & set(keywords)]
            if not matching:
                continue
            dates = {date for group in matching for date in group.get('dates') or ()}
            numbers = {number for group in matching for number in group.get('numbers') or ()}
            # Identitas aksi adalah nomor pengumuman, bukan tanggalnya. Satu pengumuman boleh punya
            # beberapa lampiran; beberapa pengumuman hanya boleh digabung bila nomornya sama.
            # Tanggal sengaja TIDAK dipakai sebagai identitas: dua aksi berbeda di hari yang sama
            # akan tergabung, dan kutipan yang benar berakhir menjelaskan aksi yang salah.
            if len(numbers) > 1 or (len(matching) > 1 and len(numbers) != 1):
                entry.update(status='ambiguous_documents', document_dates=sorted(dates),
                             document_numbers=sorted(numbers),
                             documents_from='idx_announcement_ambiguous',
                             document_candidates=len(matching))
                write_json(self.directory / 'queue' / f"{entry['id']}.json", entry)
                continue
            documents = [url for group in matching for url in pdf_links(group['urls'])]
            if documents:
                entry.update(pdf_urls=list(dict.fromkeys(documents)), status='pending_pdf_review',
                             documents_from='idx_announcement_same_number' if numbers
                             else 'idx_announcement_single_group',
                             document_dates=sorted(dates), document_numbers=sorted(numbers))
                if dates and not entry['event'].get('published_at'):
                    entry['event']['published_at'] = _iso_date(next(iter(dates)))
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


def research_queue_items(settings, directory, limit, documents=(), retry=False):
    """Yield (event, outcome, item) per kandidat antrean. Nol permintaan Sectors.

    Dipakai CLI maupun endpoint /scan/run, supaya keduanya meriset dengan cara yang sama persis.
    Pemanggil wajib menghabiskan generator ini agar engine ditutup tepat waktu.
    """
    mappings = document_map(documents)
    engine = None
    try:
        processed = 0
        for path, item in eligible_items(directory, retry):
            if processed >= limit:
                break
            event = CandidateEvent.model_validate(item['event'])

            # Sudah berputusan tetapi belum tersimpan: pulihkan dari artefak, jangan panggil model lagi.
            if needs_publication(item) and not retry:
                recovered = load_outcome(settings, item.get('case_id'))
                if recovered is not None:
                    processed += 1
                    yield event, recovered, item
                    continue

            if engine is None:
                engine = build_provider(settings)
            pdfs = mappings.get(event.ticker, []) + item['pdf_urls']
            # Kandidat tanpa PDF dulu dilewati diam-diam, sehingga tiga kandidat menghasilkan satu kasus.
            # Artikel sumbernya sendiri adalah bukti yang sah: engine selalu mengumpulkan event.source_url.
            # PDF tetap diwajibkan terbaca bila memang ada, supaya lampiran rusak tidak lolos begitu saja.
            engine.settings = settings.model_copy(update={'research_source_urls':list(dict.fromkeys(pdfs))})
            outcome = engine.research(event, None, require_pdf=bool(pdfs), require_sectors=False)
            attempts = item.get('attempt_count', 0) + 1
            item.update(status=outcome.status, case_id=outcome.case_id, label=outcome.verdict.label.value,
                        document_status=outcome.document_status, processed_at=_now().isoformat(),
                        attempt_count=attempts, last_attempt_at=_now().isoformat(),
                        next_retry_at=None if outcome.status in SETTLED_STATUSES else retry_schedule(attempts))
            # `published_at` sengaja TIDAK disetel di sini: pemakailah yang menandainya setelah commit.
            write_json(path, item)
            processed += 1
            yield event, outcome, item
    finally:
        if engine is not None:
            engine.close()


def eligible_items(directory, retry=False):
    """Kandidat yang boleh diambil giliran, yang belum pernah dicoba lebih dulu.

    Dulu berkas hanya dibaca berurutan nama hash, sehingga kandidat yang selalu gagal terus memakan
    jatah `limit` dan kandidat baru tidak pernah kebagian. Urutannya sekarang: percobaan paling
    sedikit dulu, lalu yang paling lama menunggu.
    """
    rows = []
    for path in sorted((Path(directory) / 'queue').glob('*.json')):
        try:
            item = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        settled = item.get('status') in SETTLED_STATUSES
        if settled and item.get('published_at') and not retry:
            continue
        if not settled and not retry and not is_due(item):
            continue
        rows.append((path, item))
    # Publikasi tertunda didahulukan: murah, tanpa model, dan memulihkan hasil yang hilang.
    rows.sort(key=lambda row: (not needs_publication(row[1]), row[1].get('attempt_count', 0),
                               row[1].get('last_attempt_at') or ''))
    return rows


def summarize_scan(directory, report, processed, provider=None):
    """Bedakan penemuan baru, antrean lama, dan kandidat yang sudah selesai.

    Endpoint meriset seluruh antrean tersimpan sementara `candidates_found` berasal dari discovery
    kali ini, sehingga "N kasus dari M kandidat" bisa tidak sebanding dan nol hasil terbaca seperti
    kegagalan. Angka-angka di bawah menjawab pertanyaan yang sebenarnya: apakah ada yang baru,
    apakah semuanya memang sudah pernah diproses, dan apakah ada sumber yang gagal diambil.
    """
    discovered = report.get('candidates') or []
    created_at = report.get('created_at')
    settled = [item for item in discovered if item.get('status') in SETTLED_STATUSES]
    return {
        'discovered': len(discovered),
        'new': sum(1 for item in discovered if item.get('first_seen') == created_at),
        'already_processed': sum(1 for item in settled if item.get('published_at')),
        'awaiting_publication': sum(1 for item in settled if not item.get('published_at')),
        'ambiguous_documents': sum(1 for item in discovered if item.get('status') == 'ambiguous_documents'),
        'without_document': sum(1 for item in discovered if item.get('status') == 'needs_document'),
        'pending': len(eligible_items(directory)),
        'processed': processed,
        'provider': provider,
        'articles_checked': report.get('articles_checked', 0),
        'listing_pages_fetched': len(report.get('listing_pages') or []),
        # Sumber yang gagal tetap dilaporkan walau ada hasil: cakupan parsial harus terlihat.
        'failed_sources': (report.get('failures') or [])[:5],
        'coverage_note': report.get('coverage_note', ''),
    }


def research_queue(settings, directory, limit, documents, retry=False):
    processed = 0
    for _event, _outcome, item in research_queue_items(settings, directory, limit, documents, retry):
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
    parser.add_argument('--retry', action='store_true', help='riset ulang kandidat yang sudah berputusan')
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
        print(f"Kasus diproses: {research_queue(settings, args.directory, args.limit, args.document, args.retry)}")


if __name__ == '__main__':
    main()

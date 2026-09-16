"""Capture current evidence, replay it offline, and score human-reviewed labels.

No broker integration. A replay never fetches live market data or scrapes the web.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.parse import urldefrag

from app.config import get_settings, sectors_block_reason
from app.llm.factory import build_provider
from app.pipeline.schema import CandidateEvent, ActionBucket, VerdictLabel
from app.pipeline.sense import snapshot_from_report
from app.research.models import ResearchOutcome
from app.sectors.client import SectorsClient


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


class FrozenScraper:
    def __init__(self, evidence):
        self.pages = {item['url']: item for item in evidence if item['kind'] == 'scrapling'}
        self.pdfs = {}
        for item in evidence:
            if item['kind'] == 'pdf':
                self.pdfs.setdefault(urldefrag(item['url'])[0], []).append(item)

    def fetch_document(self, url):
        from app.research.documents import Document, Link
        url = urldefrag(url)[0]
        pages = self.pdfs.get(url)
        if pages:
            pages = sorted(pages, key=lambda item: item['page_number'])
            first = pages[0]
            return Document(url=url, title=first['title'], format='pdf',
                sha256=first.get('document_sha256') or first['sha256'], fetched_at=first['retrieved_at'],
                pages=[{'number':p['page_number'], 'text':p['text']} for p in pages],
                warnings=[f"Halaman {p['page_number']} minim teks; periksa kebutuhan OCR."
                          for p in pages if len(p['text'].strip()) < 40])
        item = self.pages.get(url)
        if item is None:
            raise ValueError('URL tidak ada dalam snapshot; replay tidak memakai jaringan.')
        return Document(url=url, title=item['title'], format='html',
                        sha256=item.get('document_sha256') or item['sha256'], fetched_at=item['retrieved_at'],
                        pages=[{'number':None,'text':item['text']}],
                        links=[Link(url=link) for link in item.get('links', [])])

    def fetch(self, url):
        item = self.pages.get(url)
        if item is None:
            raise ValueError('URL tidak ada dalam snapshot; replay tidak memakai jaringan.')
        return item['title'], item['text'], item.get('links', [])


def summarize(records):
    reviewed = [row for row in records if row.get('expected_label') in {v.value for v in VerdictLabel}
                and row.get('reviewer') and row.get('review_notes')]
    eligible = [row for row in reviewed if row['status'] == 'completed']
    decided = [row for row in eligible if row['label'] != 'inconclusive']
    failed = [row for row in records if row['status'] not in {'completed', 'needs_review', 'insufficient_evidence'}]
    return {
        'cases': len(records), 'human_reviewed_cases': len(reviewed),
        'unreviewed_cases': len(records) - len(reviewed), 'operational_failures': len(failed),
        'completed_cases': sum(row['status'] == 'completed' for row in records),
        'inconclusive_rate': sum(row['label'] == 'inconclusive' for row in records) / len(records) if records else None,
        'accuracy_on_completed_reviewed': sum(row['label'] == row['expected_label'] for row in eligible) / len(eligible) if eligible else None,
        'decided_precision': sum(row['label'] == row['expected_label'] for row in decided) / len(decided) if decided else None,
        'reviewed_decision_coverage': len(decided) / len(reviewed) if reviewed else None,
        'false_growth_count': sum(row['label'] == 'growth_catalyst' and row['expected_label'] != 'growth_catalyst' for row in decided),
        'note': 'Label acuan diisi manusia berdasarkan bukti pada waktu snapshot, bukan hasil model atau kenaikan harga.',
    }


def records_for(manifest):
    records = []
    for case in manifest['cases']:
        outcome = ResearchOutcome.model_validate_json((Path(case['directory']) / 'decision.json').read_text())
        records.append({**case, 'label': outcome.verdict.label.value, 'status': outcome.status,
                        'model_versions': outcome.model_versions})
    return records


def capture(args):
    settings = get_settings()
    reason = sectors_block_reason(settings)
    if reason:
        raise SystemExit(reason)
    output = Path(args.output).resolve()
    if output.exists():
        raise SystemExit('Gunakan direktori capture baru; snapshot lama tidak ditimpa.')
    output.mkdir(parents=True)
    settings = settings.model_copy(update={'research_cases_dir': output / 'cases'})
    engine, client = build_provider(settings), SectorsClient(settings.sectors_api_key)
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'mode': 'live_read_only',
                'evaluation_type': 'current_snapshot_not_historical_backtest', 'cases': []}
    try:
        for value in args.case:
            ticker, separator, url = value.partition('=')
            if not separator or not ticker.isalnum() or not url.startswith(('https://', 'http://')):
                raise SystemExit('Format --case TICKER=https://...')
            ticker = ticker.upper()
            # One Sectors report request per case; news discovery can be done separately.
            report = client.company_report(ticker)
            event = CandidateEvent(ticker=ticker, headline=f'{ticker}: evaluasi sumber', source_url=url,
                                   body='', published_at='', bucket=ActionBucket.general_action, matched_keywords=[])
            started = time.monotonic()
            outcome = engine.research(event, snapshot_from_report(report, ticker), report=report,
                                      require_sectors=True, use_cache=False)
            directory = settings.research_cases_dir / outcome.case_id
            write_json(directory / 'evaluation_input.json', {'event': event.model_dump(mode='json'), 'report': report})
            manifest['cases'].append({'ticker': ticker, 'directory': str(directory), 'expected_label': None,
                'reviewer': None, 'review_notes': None, 'split': 'unreviewed',
                'seconds': round(time.monotonic() - started, 2)})
            write_json(output / 'manifest.json', manifest)
            print(f'{ticker}: {outcome.status}, {outcome.verdict.label.value}; {directory}', flush=True)
    finally:
        engine.close()
    report = {'summary': summarize(records_for(manifest)), 'records': records_for(manifest)}
    write_json(output / 'report.json', report)
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))


def replay(args):
    manifest = json.loads(Path(args.manifest).read_text())
    output = Path(args.output).resolve()
    if output.exists():
        raise SystemExit('Gunakan direktori replay baru.')
    settings = get_settings().model_copy(update={'research_cases_dir': output / 'cases'})
    engine = build_provider(settings)
    result = {**manifest, 'mode': 'frozen_evidence_local_models',
              'replayed_at': datetime.now(timezone.utc).isoformat(), 'cases': []}
    try:
        for case in manifest['cases']:
            directory = Path(case['directory'])
            inputs = json.loads((directory / 'evaluation_input.json').read_text())
            evidence = [json.loads(path.read_text()) for path in sorted((directory / 'evidence').glob('E*.json'))]
            engine.scraper = FrozenScraper(evidence)
            engine.settings = settings.model_copy(update={'research_source_urls': list(dict.fromkeys(
                urldefrag(e['url'])[0] for e in evidence if e['kind'] in {'scrapling', 'pdf'}))})
            event = CandidateEvent.model_validate(inputs['event'])
            report = inputs['report']
            started = time.monotonic()
            outcome = engine.research(event, snapshot_from_report(report, event.ticker), report=report,
                                      require_sectors=True, use_cache=False)
            destination = settings.research_cases_dir / outcome.case_id
            write_json(destination / 'evaluation_input.json', inputs)
            write_json(destination / 'frozen_evidence_provenance.json', evidence)
            result['cases'].append({**case, 'directory': str(destination),
                                    'source_capture_directory': str(directory),
                                    'seconds': round(time.monotonic() - started, 2)})
            write_json(output / 'manifest.json', result)
    finally:
        engine.close()
    write_json(output / 'report.json', {'summary': summarize(records_for(result)), 'records': records_for(result)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    cap = sub.add_parser('capture')
    cap.add_argument('--case', action='append', required=True, help='TICKER=https://article; maximum 3 cases')
    cap.add_argument('--output', required=True)
    rep = sub.add_parser('replay')
    rep.add_argument('--manifest', required=True)
    rep.add_argument('--output', required=True)
    score = sub.add_parser('score')
    score.add_argument('--manifest', required=True)
    args = parser.parse_args()
    if args.command == 'capture':
        if len(args.case) > 3:
            parser.error('Capture dibatasi 3 kasus agar biaya API dan waktu lokal terukur.')
        capture(args)
    elif args.command == 'replay':
        replay(args)
    else:
        print(json.dumps(summarize(records_for(json.loads(Path(args.manifest).read_text()))), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

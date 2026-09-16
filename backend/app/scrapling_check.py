from __future__ import annotations

import argparse
import time

from app.config import get_settings
from app.llm.factory import build_provider
from app.pipeline.gate import apply_gate
from app.pipeline.schema import ActionBucket, CandidateEvent

DEFAULT_CASES = [
    ("MGLV", "https://finance.detik.com/bursa-dan-valas/d-8661594/mglv-bidik-dana-rp-2-54-triliun-lewat-rights-issue-siapkan-proyek-data-center"),
    ("HATM", "https://market.bisnis.com/read/20260820/192/1997696/habco-trans-hatm-siapkan-private-placement-868-juta-saham-tambah-armada-kapal"),
    ("APEX", "https://www.idxchannel.com/market-news/konversi-utang-apexindo-apex-rancang-private-placement-rp7088-miliar"),
    ("LAPD", "https://emitennews.com/news/lapd-mau-rights-issue-dan-inbreng-saham-minta-restu-rupslb"),
    ("FORU", "https://www.bloombergtechnoz.com/detail-news/121434/dari-media-ke-tambang-foru-rights-issue-rp27-t"),
]


def parse_case(value: str) -> tuple[str, str]:
    ticker, _, url = value.partition("=")
    if not ticker.isalnum() or not url.startswith(("http://", "https://")):
        raise argparse.ArgumentTypeError("format kasus: TICKER=https://...")
    return ticker.upper(), url


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Uji engine hybrid hanya dengan artikel yang diambil Scrapling, tanpa memanggil Sectors API."
    )
    parser.add_argument("cases", nargs="*", type=parse_case, help="TICKER=URL; kosong berarti kasus bawaan")
    parser.add_argument("--limit", type=int, default=len(DEFAULT_CASES))
    args = parser.parse_args()

    engine = build_provider(get_settings())
    cases = (args.cases or DEFAULT_CASES)[: args.limit]
    print(f"Model: {engine.name} | kasus: {len(cases)} | tanpa Sectors API\n")

    for ticker, url in cases:
        event = CandidateEvent(ticker=ticker, headline=f"{ticker} uji Scrapling", body="", source_url=url,
                               published_at="", bucket=ActionBucket.general_action, matched_keywords=[])
        started = time.monotonic()
        outcome = engine.research(event, None)
        elapsed = time.monotonic() - started
        gate = apply_gate(outcome.verdict)
        cached = " | dari cache" if outcome.cached else ""
        print(f"=== {ticker}: {outcome.verdict.label.value} (confidence {outcome.verdict.confidence:.2f}) | "
              f"status {outcome.status} | gate {gate.status.value} | {elapsed:.0f} detik{cached}")
        for fact in outcome.facts:
            print(f'  fakta  {fact.id} {fact.topic}={fact.value} [{fact.validator_status}]: "{fact.quote[:100]}"')
        for signal in outcome.signals:
            print(f"  sinyal {signal['side']:<6} +{signal['weight']} {signal['reason']}")
        for issue in outcome.issues[:4]:
            print(f"  catatan {issue[:160]}")
        turns = [f"{run['role']} {run['model'].removeprefix('ollama:')} {run['seconds']:.0f}s"
                 for run in outcome.model_runs if "seconds" in run]
        offloaded = sum(1 for run in outcome.model_runs if run.get("offloaded"))
        if turns:
            print(f"  giliran model: {' -> '.join(turns)} | dilepas dari GPU: {offloaded}x")
        print(f"  folder cases/{outcome.case_id}\n")

    print("Hanya untuk pengembangan. Hasil screening, bukan rekomendasi transaksi.")


if __name__ == "__main__":
    main()

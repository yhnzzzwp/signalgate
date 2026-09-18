from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ScreenedEventRecord
from app.pipeline.audit import persist_screened_event, record_audit
from app.pipeline.stream import run_events
from app.pipeline.schema import ActionBucket, CandidateEvent, ScreenedEvent
from app.pipeline.presentation import screen_outcome
from app.pipeline.sense import (FreeFloatLookup, SubsectorValuationLookup, collect_candidate_events,
                                snapshot_company)
from app.research.models import SETTLED_RESEARCH_STATUSES
from app.sectors.client import SectorsClient

BUCKET_PRIORITY = {
    ActionBucket.control_change: 0,
    ActionBucket.non_preemptive_capital: 1,
    ActionBucket.rights_issue: 2,
    ActionBucket.general_action: 3,
}


def pick_events(events: list[CandidateEvent], max_events: int) -> list[CandidateEvent]:
    first_per_ticker: dict[str, CandidateEvent] = {}
    for event in sorted(events, key=lambda item: BUCKET_PRIORITY[item.bucket]):
        first_per_ticker.setdefault(event.ticker, event)
    return list(first_per_ticker.values())[:max_events]


@dataclass
class PipelineRun:
    results: list[ScreenedEvent] = field(default_factory=list)
    # Nol hasil karena semua berita teratas sudah diputus berbeda artinya dari nol karena gagal.
    already_processed: int = 0


def dedupe_key(event: CandidateEvent) -> str:
    return f"sectors:{event.ticker}:{event.source_url}"


def settled_keys(session: Session, events: list[CandidateEvent]) -> set[str]:
    """Berita yang kartunya sudah berisi putusan. Kegagalan lingkungan tetap boleh dicoba lagi."""
    keys = list(dict.fromkeys(dedupe_key(event) for event in events))
    if not keys:
        return set()
    records = session.scalars(select(ScreenedEventRecord).where(ScreenedEventRecord.dedupe_key.in_(keys))).all()
    return {record.dedupe_key for record in records
            if ((record.payload or {}).get("research") or {}).get("status") in SETTLED_RESEARCH_STATUSES}


def run_pipeline(client: SectorsClient, provider, session: Session, max_events: int = 3) -> PipelineRun:
    events = collect_candidate_events(client)
    # Disaring SEBELUM dipilih: berita teratas yang sama dulu diriset ulang setiap run, memakai
    # kredit snapshot Sectors lagi dan menambah kartu kembar, sementara berita berikutnya tidak
    # pernah kebagian giliran.
    done = settled_keys(session, events)
    fresh = [event for event in events if dedupe_key(event) not in done]
    selected = pick_events(fresh, max_events)
    run = PipelineRun(already_processed=len(events) - len(fresh))
    record_audit(session, stage="sense", ticker="*",
                 detail={"event_count": len(events), "already_processed": run.already_processed,
                         "selected": [event.ticker for event in selected]})

    total = len(selected)
    # One call per subsector for each lookup, reused across every event in this run.
    float_lookup, valuation_lookup = FreeFloatLookup(client), SubsectorValuationLookup(client)
    for index, event in enumerate(selected, start=1):
        run_events.publish("case", event.ticker,
                           {"phase": "start", "index": index, "total": total, "bucket": event.bucket.value})
        snapshot = snapshot_company(client, event.ticker, event.sub_sector, float_lookup,
                                    valuation_lookup=valuation_lookup, with_filings=True)
        outcome = provider.research(event, snapshot, report=client.last_report, require_sectors=True)
        record_audit(session, stage="research", ticker=event.ticker,
                     detail=outcome.model_dump(mode="json", exclude={"verdict", "evidence"}))
        screened = screen_outcome(event, snapshot, outcome)
        issues = screened.numeric_issues
        if issues:
            record_audit(session, stage="validate", ticker=event.ticker, detail={"issues": issues})

        gate = screened.gate
        record_audit(session, stage="gate", ticker=event.ticker,
                     detail={"status": gate.status.value, "rejected_terms": gate.rejected_terms})

        persist_screened_event(session, screened, dedupe_key=dedupe_key(event))
        run.results.append(screened)
        # Diterbitkan SETELAH commit: kasus baru dihitung selesai ketika kartunya benar-benar ada.
        run_events.publish("case", event.ticker,
                           {"phase": "end", "index": index, "total": total,
                            "label": screened.verdict.label.value})

    return run

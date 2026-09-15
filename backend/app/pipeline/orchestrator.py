from __future__ import annotations

from sqlalchemy.orm import Session

from app.llm.base import LLMProvider
from app.pipeline.audit import persist_screened_event, record_audit
from app.pipeline.gate import apply_gate, sanitize_for_display
from app.pipeline.reason import reason_about_event
from app.pipeline.schema import ScreenedEvent
from app.pipeline.sense import collect_candidate_events, snapshot_company
from app.pipeline.validate import validate_verdict
from app.sectors.client import SectorsClient


def run_pipeline(client: SectorsClient, provider: LLMProvider, session: Session) -> list[ScreenedEvent]:
    events = collect_candidate_events(client)
    record_audit(session, stage="sense", ticker="*", detail={"event_count": len(events)})

    results: list[ScreenedEvent] = []
    seen_tickers: dict[str, ScreenedEvent] = {}

    for event in events:
        if event.ticker in seen_tickers:
            continue

        snapshot = snapshot_company(client, event.ticker)
        verdict = reason_about_event(provider, event, snapshot)
        issues = validate_verdict(event, snapshot, verdict)
        if issues:
            record_audit(session, stage="validate", ticker=event.ticker, detail={"issues": issues})

        gate = apply_gate(verdict)
        sanitized_verdict = sanitize_for_display(verdict, gate)
        record_audit(
            session,
            stage="gate",
            ticker=event.ticker,
            detail={"status": gate.status.value, "rejected_terms": gate.rejected_terms},
        )

        screened = ScreenedEvent(event=event, snapshot=snapshot, verdict=sanitized_verdict, gate=gate)
        persist_screened_event(session, screened)
        seen_tickers[event.ticker] = screened
        results.append(screened)

    return results

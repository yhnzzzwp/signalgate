from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import AuditLogRecord, ScreenedEventRecord
from app.pipeline.schema import ScreenedEvent


def record_audit(session: Session, stage: str, ticker: str, detail: dict) -> None:
    session.add(AuditLogRecord(stage=stage, ticker=ticker, detail=detail))
    session.commit()


def persist_screened_event(session: Session, screened: ScreenedEvent) -> ScreenedEventRecord:
    record = ScreenedEventRecord(
        ticker=screened.event.ticker,
        headline=screened.event.headline,
        source_url=screened.event.source_url,
        bucket=screened.event.bucket.value,
        label=screened.verdict.label.value,
        confidence=screened.verdict.confidence,
        provider=screened.verdict.provider,
        gate_status=screened.gate.status.value,
        watch_status=screened.watch_status,
        payload=screened.model_dump(mode="json"),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record

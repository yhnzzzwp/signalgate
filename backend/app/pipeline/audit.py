from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLogRecord, ScreenedEventRecord
from app.pipeline.schema import ScreenedEvent
from app.pipeline.stream import run_events


def record_audit(session: Session, stage: str, ticker: str, detail: dict) -> None:
    session.add(AuditLogRecord(stage=stage, ticker=ticker, detail=detail))
    session.commit()
    # Setelah commit, supaya klien tidak pernah melihat tahap yang gagal tersimpan.
    run_events.publish(stage, ticker, detail)


def persist_screened_event(session: Session, screened: ScreenedEvent,
                           dedupe_key: str | None = None) -> ScreenedEventRecord:
    """Simpan kartu; kandidat yang sama memperbarui barisnya alih-alih menambah kartu kedua.

    Publikasi bisa terjadi lebih dari sekali untuk satu kandidat: commit gagal lalu dipulihkan dari
    artefak, atau riset diulang dengan --retry. Tanpa identitas, tiap kali itu menghasilkan kartu
    duplikat yang membuat dashboard menghitung satu aksi korporasi beberapa kali.
    """
    if dedupe_key:
        existing = session.scalars(
            select(ScreenedEventRecord).where(ScreenedEventRecord.dedupe_key == dedupe_key)
        ).first()
        if existing is not None:
            existing.ticker = screened.event.ticker
            existing.headline = screened.event.headline
            existing.source_url = screened.event.source_url
            existing.bucket = screened.event.bucket.value
            existing.label = screened.verdict.label.value
            existing.confidence = screened.verdict.confidence
            existing.provider = screened.verdict.provider
            existing.gate_status = screened.gate.status.value
            existing.watch_status = screened.watch_status
            existing.payload = screened.model_dump(mode="json")
            existing.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(existing)
            return existing

    record = ScreenedEventRecord(
        dedupe_key=dedupe_key,
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

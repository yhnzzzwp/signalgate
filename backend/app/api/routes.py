from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLogRecord, ScreenedEventRecord

router = APIRouter()


def _serialize_event(record: ScreenedEventRecord) -> dict:
    return {
        "id": record.id,
        "ticker": record.ticker,
        "headline": record.headline,
        "source_url": record.source_url,
        "bucket": record.bucket,
        "label": record.label,
        "confidence": record.confidence,
        "provider": record.provider,
        "gate_status": record.gate_status,
        "watch_status": record.watch_status,
        "created_at": record.created_at.isoformat(),
        "detail": record.payload,
    }


def register_routes(app, session_factory) -> None:
    def get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    @router.get("/events")
    def list_events(session: Session = Depends(get_db)) -> list[dict]:
        records = session.scalars(select(ScreenedEventRecord).order_by(ScreenedEventRecord.created_at.desc())).all()
        return [_serialize_event(record) for record in records]

    @router.get("/events/{event_id}")
    def get_event(event_id: int, session: Session = Depends(get_db)) -> dict | None:
        record = session.get(ScreenedEventRecord, event_id)
        return _serialize_event(record) if record else None

    @router.get("/audit")
    def list_audit(session: Session = Depends(get_db)) -> list[dict]:
        records = session.scalars(select(AuditLogRecord).order_by(AuditLogRecord.created_at.desc()).limit(200)).all()
        return [
            {
                "id": record.id,
                "stage": record.stage,
                "ticker": record.ticker,
                "detail": record.detail,
                "created_at": record.created_at.isoformat(),
            }
            for record in records
        ]

    app.include_router(router)

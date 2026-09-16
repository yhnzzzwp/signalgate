from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AuditLogRecord, ScreenedEventRecord

router = APIRouter()

# /audit sudah dibatasi sejak awal; /events tidak, sehingga payload dashboard tumbuh tanpa batas
# seiring bertambahnya run. Batas saja tidak cukup: tanpa offset dan filter server, label yang
# semua hasilnya lebih lama dari satu halaman terlihat kosong padahal masih ada di database.
EVENT_PAGE_LIMIT = 100
AUDIT_PAGE_LIMIT = 200


def _utc_iso(moment: datetime) -> str:
    """Nyatakan zona waktunya secara eksplisit.

    Kolomnya `DateTime` tanpa zona, jadi SQLite mengembalikan datetime naif walau yang ditulis sudah
    UTC. Tanpa offset, `new Date()` di browser menafsirkannya sebagai waktu lokal: di WIB, 07:03 UTC
    tampil sebagai 07:03, bukan 14:03.
    """
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


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
        "created_at": _utc_iso(record.created_at),
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
    def list_events(
        session: Session = Depends(get_db),
        limit: int = Query(EVENT_PAGE_LIMIT, ge=1, le=500),
        offset: int = Query(0, ge=0),
        label: str | None = None,
        ticker: str | None = None,
    ) -> dict:
        """Satu halaman hasil, plus hitungan agar tab label jujur terhadap seluruh database.

        Memfilter di klien hanya menyaring halaman yang kebetulan terambil, sehingga tab red flag
        bisa menunjukkan nol padahal hasilnya ada di halaman berikutnya.
        """
        wanted_ticker = ticker.strip().upper() if ticker else None
        scope = select(ScreenedEventRecord)
        tally = select(ScreenedEventRecord.label, func.count()).group_by(ScreenedEventRecord.label)
        if wanted_ticker:
            scope = scope.where(ScreenedEventRecord.ticker == wanted_ticker)
            tally = tally.where(ScreenedEventRecord.ticker == wanted_ticker)

        # Hitungan tidak ikut disaring label: tab lain harus tetap menunjukkan jumlah sebenarnya.
        counts = dict(session.execute(tally).all())
        selected = scope.where(ScreenedEventRecord.label == label) if label else scope
        total = session.scalar(select(func.count()).select_from(selected.subquery())) or 0
        records = session.scalars(
            selected.order_by(ScreenedEventRecord.created_at.desc(), ScreenedEventRecord.id.desc())
            .limit(limit).offset(offset)
        ).all()
        return {
            "results": [_serialize_event(record) for record in records],
            "total": total,
            "counts": counts,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(records) < total,
        }

    @router.get("/events/{event_id}")
    def get_event(event_id: int, session: Session = Depends(get_db)) -> dict | None:
        record = session.get(ScreenedEventRecord, event_id)
        return _serialize_event(record) if record else None

    @router.get("/audit")
    def list_audit(
        session: Session = Depends(get_db),
        limit: int = Query(AUDIT_PAGE_LIMIT, ge=1, le=500),
        offset: int = Query(0, ge=0),
        ticker: str | None = None,
    ) -> dict:
        scope = select(AuditLogRecord)
        if ticker:
            scope = scope.where(AuditLogRecord.ticker == ticker.strip().upper())
        total = session.scalar(select(func.count()).select_from(scope.subquery())) or 0
        records = session.scalars(
            scope.order_by(AuditLogRecord.created_at.desc(), AuditLogRecord.id.desc())
            .limit(limit).offset(offset)
        ).all()
        return {
            "results": [
                {
                    "id": record.id,
                    "stage": record.stage,
                    "ticker": record.ticker,
                    "detail": record.detail,
                    "created_at": _utc_iso(record.created_at),
                }
                for record in records
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(records) < total,
        }

    app.include_router(router)

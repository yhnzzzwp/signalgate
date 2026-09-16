"""Run yang tersimpan, supaya progres bertahan melewati refresh halaman dan koneksi putus.

Sebelumnya status run hanya hidup di state React: memuat ulang halaman menghapus tampilan progres
meskipun backend masih bekerja, dan menekan tombol lagi berakhir 409 tanpa penjelasan. Run sekarang
punya identitas yang bisa dibaca ulang, jadi dashboard bisa menyambung kembali ke run yang berjalan.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import RunJobRecord

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"


def utc_iso(moment: datetime | None) -> str | None:
    """Zona waktu dinyatakan eksplisit: SQLite mengembalikan datetime naif walau yang ditulis UTC."""
    if moment is None:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


def serialize(record: RunJobRecord) -> dict:
    return {
        "id": record.id,
        "kind": record.kind,
        "status": record.status,
        "started_at": utc_iso(record.started_at),
        "finished_at": utc_iso(record.finished_at),
        "result": record.result,
        "error": record.error,
    }


def create(session: Session, kind: str) -> RunJobRecord:
    record = RunJobRecord(id=uuid4().hex[:16], kind=kind, status=RUNNING)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def finish(session: Session, job_id: str, result: dict) -> None:
    _close(session, job_id, COMPLETED, result=result)


def fail(session: Session, job_id: str, error: str) -> None:
    # Pesan dipotong: yang dibutuhkan pembaca adalah sebabnya, bukan seluruh traceback.
    _close(session, job_id, FAILED, error=error[:2000])


def _close(session: Session, job_id: str, status: str, result: dict | None = None,
           error: str | None = None) -> None:
    record = session.get(RunJobRecord, job_id)
    if record is None:
        return
    record.status = status
    record.finished_at = datetime.now(UTC)
    record.result = result
    record.error = error
    session.commit()


def active(session: Session) -> RunJobRecord | None:
    return session.scalars(
        select(RunJobRecord).where(RunJobRecord.status == RUNNING)
        .order_by(RunJobRecord.started_at.desc())
    ).first()


def latest(session: Session, limit: int = 10) -> list[RunJobRecord]:
    return list(session.scalars(
        select(RunJobRecord).order_by(RunJobRecord.started_at.desc()).limit(limit)
    ).all())


def release_stale(session: Session) -> int:
    """Run yang masih `running` saat proses start ulang tidak akan pernah selesai sendiri.

    Tanpa ini, satu backend yang mati di tengah run membuat dashboard menunggu selamanya dan
    tombolnya terkunci oleh job hantu.
    """
    stranded = list(session.scalars(select(RunJobRecord).where(RunJobRecord.status == RUNNING)).all())
    for record in stranded:
        record.status = FAILED
        record.finished_at = datetime.now(UTC)
        record.error = "Backend berhenti saat run ini berjalan; hasil sebagiannya mungkin tersimpan."
    if stranded:
        session.commit()
    return len(stranded)

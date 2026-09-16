from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ScreenedEventRecord(Base):
    __tablename__ = "screened_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    headline: Mapped[str] = mapped_column(String)
    source_url: Mapped[str] = mapped_column(String)
    bucket: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String, index=True)
    confidence: Mapped[float] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String)
    gate_status: Mapped[str] = mapped_column(String)
    watch_status: Mapped[str] = mapped_column(String, default="active")
    payload: Mapped[dict] = mapped_column(JSON)
    # Identitas kandidat yang menghasilkan kartu ini. Publikasi ulang kandidat yang sama memperbarui
    # barisnya, bukan menambah kartu kedua. NULL untuk jalur yang tidak punya antrean.
    dedupe_key: Mapped[str | None] = mapped_column(String, unique=True, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditLogRecord(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    stage: Mapped[str] = mapped_column(String, index=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    detail: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class RunJobRecord(Base):
    """Satu run pipeline atau scan, dapat dibaca ulang setelah refresh atau koneksi putus."""

    __tablename__ = "run_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)

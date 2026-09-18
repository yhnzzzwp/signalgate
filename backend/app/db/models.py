from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, UniqueConstraint
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


class WorkflowRunRecord(Base):
    """Satu run laporan empat panel. Status run dipisah dari laporannya.

    Run yang gagal tidak boleh membuat laporan terakhir yang berhasil ikut hilang dari dashboard,
    dan sebaliknya laporan lama tidak boleh tampil seolah hasil run yang baru saja gagal.
    """

    __tablename__ = "workflow_runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    horizon: Mapped[str] = mapped_column(String)
    as_of: Mapped[str] = mapped_column(String)
    data_mode: Mapped[str] = mapped_column(String, default="live")
    status: Mapped[str] = mapped_column(String, index=True)
    job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    replay_of: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    report_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WorkflowReportRecord(Base):
    """Laporan tersimpan dengan kunci idempotensi (run_id, report_version)."""

    __tablename__ = "workflow_reports"
    __table_args__ = (UniqueConstraint("run_id", "report_version", name="uq_workflow_report_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    report_version: Mapped[int] = mapped_column(Integer, default=1)
    ticker: Mapped[str] = mapped_column(String, index=True)
    as_of: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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

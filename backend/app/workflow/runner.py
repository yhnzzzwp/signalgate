"""Menjalankan graph laporan: status run tersimpan, checkpoint, batas waktu, dan pembatalan.

Checkpoint menyimpan state, bukan menjamin efek samping hanya terjadi sekali. Karena itu publikasi
memakai kunci `(run_id, report_version)` dan status run disimpan terpisah dari laporannya: run yang
gagal tidak menghapus laporan terakhir yang berhasil, dan laporan lama tidak pernah dipakai sebagai
hasil run baru.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import select

from app.config import Settings
from app.db.models import WorkflowReportRecord, WorkflowRunRecord
from app.workflow.graph import build_graph
from app.workflow.models import ModelPool
from app.workflow.nodes import Context
from app.workflow.snapshot import (LiveGateway, ReplayGateway, SnapshotStore, SourceUnavailable, today_wib)
from app.workflow.state import WorkflowInput

RECURSION_LIMIT = 40


class WorkflowError(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc)


def serialize_run(record: WorkflowRunRecord) -> dict:
    return {"run_id": record.run_id, "ticker": record.ticker, "horizon": record.horizon, "as_of": record.as_of,
            "data_mode": record.data_mode, "status": record.status, "job_id": record.job_id,
            "replay_of": record.replay_of, "error": record.error, "report_version": record.report_version,
            "created_at": record.created_at.replace(tzinfo=record.created_at.tzinfo or timezone.utc).isoformat()
            if record.created_at else None}


class WorkflowRunner:
    def __init__(self, settings: Settings, session_factory, client_factory=None, progress=None,
                 model_pool_factory=ModelPool, gateway_factory=None) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.client_factory = client_factory
        # Dipakai test dan mode fixture: mengembalikan gateway siap pakai tanpa klien Sectors.
        self.gateway_factory = gateway_factory
        self.progress = progress
        self.model_pool_factory = model_pool_factory

    # -- identitas run ----------------------------------------------------------------------------

    def run_dir(self, run_id: str) -> Path:
        return Path(self.settings.workflow_directory) / run_id

    def create(self, ticker: str, horizon: str, as_of: date | None, replay_of: str | None = None) -> dict:
        """Buat identitas run. Replay memakai ticker dan tanggal acuan run aslinya, bukan yang baru."""
        with self.session_factory() as session:
            if replay_of:
                original = session.get(WorkflowRunRecord, replay_of)
                if original is None:
                    raise WorkflowError(f"Run {replay_of} tidak ditemukan untuk diputar ulang.")
                ticker, as_of = original.ticker, date.fromisoformat(original.as_of)
            request = WorkflowInput(run_id="placeholder", ticker=ticker, horizon=horizon,
                                    as_of=as_of or today_wib())
            run_id = f"{request.ticker}-{request.as_of.isoformat()}-{uuid4().hex[:8]}"
            record = WorkflowRunRecord(run_id=run_id, ticker=request.ticker, horizon=request.horizon,
                                       as_of=request.as_of.isoformat(), status="pending",
                                       data_mode="replay" if replay_of else "live", replay_of=replay_of)
            session.add(record)
            session.commit()
            return serialize_run(record)

    def _mark(self, run_id: str, status: str, error: str | None = None, job_id: str | None = None) -> None:
        with self.session_factory() as session:
            record = session.get(WorkflowRunRecord, run_id)
            if record is None:
                return
            record.status = status
            record.updated_at = _now()
            if error is not None:
                record.error = error[:2000]
            if job_id is not None:
                record.job_id = job_id
            session.commit()

    # -- eksekusi ---------------------------------------------------------------------------------

    def execute(self, run_id: str, *, resume: bool = False, job_id: str | None = None, cancel=None) -> dict:
        with self.session_factory() as session:
            record = session.get(WorkflowRunRecord, run_id)
            if record is None:
                raise WorkflowError(f"Run {run_id} tidak ditemukan.")
            record = serialize_run(record)
        self._mark(run_id, "running", job_id=job_id)
        run_directory = self.run_dir(run_id)
        run_directory.mkdir(parents=True, exist_ok=True)
        models = self.model_pool_factory(self.settings)
        try:
            gateway = self._gateway_from(record)
            context = Context(settings=self.settings, store=SnapshotStore(run_directory), gateway=gateway,
                              run_dir=run_directory, session_factory=self.session_factory, models=models)
            deadline = time.monotonic() + self.settings.workflow_deadline_seconds
            checkpoint_path = Path(self.settings.workflow_directory) / "checkpoints.sqlite"
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
                graph = build_graph(context, saver, progress=self._progress(record), cancel=cancel, deadline=deadline)
                config = {"configurable": {"thread_id": run_id}, "recursion_limit": RECURSION_LIMIT}
                snapshot = graph.get_state(config)
                if resume:
                    if not snapshot.values:
                        raise WorkflowError(f"Checkpoint untuk {run_id} tidak ditemukan.")
                    if not snapshot.next:
                        self._mark(run_id, snapshot.values.get("report", {}).get("status", "completed"))
                        return {"run_id": run_id, "status": "already_completed",
                                "report_version": (snapshot.values.get("published") or {}).get("report_version")}
                    initial = None
                elif snapshot.values:
                    raise WorkflowError(f"Run {run_id} sudah pernah dijalankan; pakai resume atau run baru.")
                else:
                    initial = {"request": {"run_id": run_id, "ticker": record["ticker"],
                                           "horizon": record["horizon"], "as_of": record["as_of"]}}
                result = graph.invoke(initial, config=config)
        except BaseException as error:  # noqa: BLE001 - status run harus selalu menutup
            self._mark(run_id, "failed", error=f"{type(error).__name__}: {error}")
            raise
        finally:
            models.close()
        report = result.get("report") or {}
        self._mark(run_id, report.get("status", "failed"))
        return {"run_id": run_id, "status": report.get("status"), "ticker": record["ticker"],
                "as_of": record["as_of"], "data_mode": report.get("data_mode"),
                "report_version": (result.get("published") or {}).get("report_version"),
                "credits_used": report.get("credits_used", 0),
                "panels": {domain: panel["status"] for domain, panel in (report.get("panels") or {}).items()},
                "unresolved_claims": len((report.get("validation") or {}).get("unresolved_claim_ids") or []),
                "model_runs": len(report.get("model_runs") or [])}

    def _gateway_from(self, record: dict):
        if self.gateway_factory is not None and not record.get("replay_of"):
            return self.gateway_factory()
        if record.get("replay_of"):
            try:
                return ReplayGateway(self.run_dir(record["replay_of"]))
            except SourceUnavailable as error:
                raise WorkflowError(str(error)) from error
        if self.client_factory is None:
            raise WorkflowError("Klien Sectors tidak tersedia; pakai mode replay atau aktifkan Sectors.")
        return LiveGateway(self.client_factory())

    def _progress(self, record: dict):
        if self.progress is None:
            return None

        def publish(phase: str, node: str, state: dict) -> None:
            self.progress(phase, node, {"run_id": record["run_id"], "ticker": record["ticker"],
                                        "repair_count": state.get("repair_count", 0)})

        return publish

    # -- pembacaan --------------------------------------------------------------------------------

    def runs(self, ticker: str | None = None, limit: int = 20) -> list[dict]:
        with self.session_factory() as session:
            scope = select(WorkflowRunRecord).order_by(WorkflowRunRecord.created_at.desc()).limit(limit)
            if ticker:
                scope = scope.where(WorkflowRunRecord.ticker == ticker.strip().upper())
            return [serialize_run(record) for record in session.scalars(scope).all()]

    def report(self, run_id: str) -> dict | None:
        with self.session_factory() as session:
            record = session.scalars(
                select(WorkflowReportRecord).where(WorkflowReportRecord.run_id == run_id)
                .order_by(WorkflowReportRecord.report_version.desc())
            ).first()
            return record.payload if record else None

    def latest_report(self, ticker: str) -> dict | None:
        with self.session_factory() as session:
            record = session.scalars(
                select(WorkflowReportRecord).where(WorkflowReportRecord.ticker == ticker.strip().upper())
                .order_by(WorkflowReportRecord.created_at.desc())
            ).first()
            return record.payload if record else None

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
from app.frontier.service import build_frontier
from app.runtime import RunBinding, RuntimeStore, diff_snapshots, load_binding, resolve, save_binding
from app.workflow.graph import build_graph
from app.workflow.models import ModelPool
from app.workflow.nodes import Context
from app.workflow.snapshot import (LiveGateway, ReplayGateway, SnapshotStore, SourceUnavailable, today_wib)
from app.workflow.state import WorkflowInput

RECURSION_LIMIT = 40


class WorkflowError(RuntimeError):
    pass


class WorkflowConfigChanged(WorkflowError):
    """Resume ditolak karena konfigurasi efektif berbeda dari yang dipakai run ini dan belum disetujui."""

    def __init__(self, changes: list[dict]) -> None:
        fields = ", ".join(change["field"] for change in changes)
        super().__init__(f"Konfigurasi berubah sejak run ini dimulai ({fields}); lanjutkan hanya bila perubahan "
                         "itu disetujui eksplisit (accept_config_change=true).")
        self.changes = changes


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
                 model_pool_factory=ModelPool, gateway_factory=None, frontier_factory=build_frontier,
                 runtime_store: RuntimeStore | None = None) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.client_factory = client_factory
        # Dipakai test dan mode fixture: mengembalikan gateway siap pakai tanpa klien Sectors.
        self.gateway_factory = gateway_factory
        self.progress = progress
        self.model_pool_factory = model_pool_factory
        # Mengembalikan FrontierService atau None (FRONTIER_ENABLED=false).
        self.frontier_factory = frontier_factory
        # Pilihan lokasi GPU/mode frontier dari dashboard. None = hanya env (perilaku lama, dipakai test).
        self.runtime_store = runtime_store

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

    def resolved(self):
        """Settings efektif untuk run berikutnya: env + pilihan runtime, salinan baru per panggilan."""
        return resolve(self.settings, self.runtime_store)

    def config_changes(self, run_id: str) -> list[dict]:
        """Beda konfigurasi yang memengaruhi keputusan antara run ini dan konfigurasi efektif sekarang."""
        binding = load_binding(self.run_dir(run_id))
        return diff_snapshots(binding.bound, self.resolved().snapshot) if binding else []

    def _bind(self, run_directory: Path, resolved, *, resume: bool, next_nodes: list[str],
              changes: list[dict]) -> tuple[RunBinding, int]:
        """Catat konfigurasi yang dipakai run ini. Resume tanpa perubahan memakai konfigurasi yang sama;
        resume dengan perubahan yang disetujui dicatat beserta node tempat konfigurasi baru mulai berlaku."""
        binding = load_binding(run_directory)
        at = _now().isoformat()
        if binding is None:
            event = "resume_unbound_legacy" if resume else "start"
            binding = RunBinding(bound=resolved.snapshot, history=[
                {"event": event, "at": at, "from_nodes": next_nodes, "snapshot": resolved.snapshot}])
        elif changes:
            binding.history.append({"event": "resume_config_changed", "at": at, "from_nodes": next_nodes,
                                    "changes": changes, "snapshot": resolved.snapshot})
            binding.bound = resolved.snapshot
        else:
            binding.history.append({"event": "resume" if resume else "start", "at": at, "from_nodes": next_nodes})
        save_binding(run_directory, binding)
        index = max(position for position, entry in enumerate(binding.history) if "snapshot" in entry)
        return binding, index

    def execute(self, run_id: str, *, resume: bool = False, job_id: str | None = None, cancel=None,
                accept_config_change: bool = False) -> dict:
        with self.session_factory() as session:
            record = session.get(WorkflowRunRecord, run_id)
            if record is None:
                raise WorkflowError(f"Run {run_id} tidak ditemukan.")
            record = serialize_run(record)
        run_directory = self.run_dir(run_id)
        # Settings diambil SEKALI di awal: aktivasi dari dashboard saat run berjalan hanya berlaku untuk run
        # berikutnya. Resume dengan konfigurasi berbeda ditolak kecuali disetujui eksplisit.
        resolved = self.resolved()
        binding = load_binding(run_directory)
        changes = diff_snapshots(binding.bound, resolved.snapshot) if (resume and binding) else []
        if changes and not accept_config_change:
            raise WorkflowConfigChanged(changes)
        settings = resolved.settings
        self._mark(run_id, "running", job_id=job_id)
        run_directory.mkdir(parents=True, exist_ok=True)
        models = frontier = None
        try:
            # Replay snapshot Sectors tidak otomatis bebas biaya LLM: bawaannya frontier hanya membaca
            # respons tersimpan pada run replay (FRONTIER_CALLS_ON_REPLAY=true untuk mengizinkan panggilan).
            offline = bool(record.get("replay_of")) and not settings.frontier_calls_on_replay
            context = Context(settings=settings, store=SnapshotStore(run_directory), gateway=None,
                              run_dir=run_directory, session_factory=self.session_factory, frontier_offline=offline)
            deadline = time.monotonic() + settings.workflow_deadline_seconds
            checkpoint_path = Path(settings.workflow_directory) / "checkpoints.sqlite"
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
                bound, index = self._bind(run_directory, resolved, resume=resume,
                                          next_nodes=list(snapshot.next or ()) if resume else [], changes=changes)
                context.gateway = self._gateway_from(record)
                context.models = models = self.model_pool_factory(settings)
                context.frontier = frontier = self.frontier_factory(settings) if self.frontier_factory else None
                context.runtime, context.config_index = bound.model_dump(), index
                result = graph.invoke(initial, config=config)
        except BaseException as error:  # noqa: BLE001 - status run harus selalu menutup
            self._mark(run_id, "failed", error=f"{type(error).__name__}: {error}")
            raise
        finally:
            if models is not None:
                models.close()
            if frontier is not None:
                frontier.close()
        report = result.get("report") or {}
        self._mark(run_id, report.get("status", "failed"))
        frontier_record = report.get("frontier") or {}
        return {"run_id": run_id, "status": report.get("status"), "ticker": record["ticker"],
                "as_of": record["as_of"], "data_mode": report.get("data_mode"),
                "report_version": (result.get("published") or {}).get("report_version"),
                "credits_used": report.get("credits_used", 0),
                "panels": {domain: panel["status"] for domain, panel in (report.get("panels") or {}).items()},
                "unresolved_claims": len((report.get("validation") or {}).get("unresolved_claim_ids") or []),
                "model_runs": len(report.get("model_runs") or []),
                "frontier_status": frontier_record.get("status") if frontier_record else None,
                "frontier_mode": frontier_record.get("mode") if frontier_record else None,
                "frontier_cost_usd_estimate": (frontier_record.get("totals") or {}).get("cost_usd_estimate")
                if frontier_record else None}

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

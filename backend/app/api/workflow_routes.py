"""Endpoint laporan empat panel.

Run berjalan di latar memakai job dan SSE yang sama dengan pipeline lain, jadi refresh halaman tidak
menghilangkan progres. Mode replay memakai snapshot run lain sehingga demo dan pengembangan tidak
memakai kredit Sectors sama sekali.
"""
from __future__ import annotations

from datetime import date
from threading import Event

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.workflow.graph import NODE_LABELS
from app.workflow.runner import WorkflowError

router = APIRouter(prefix="/workflow")


class WorkflowRunRequest(BaseModel):
    ticker: str = Field(default="", pattern=r"^([A-Za-z0-9]{4})?$")
    horizon: str = Field(default="medium", pattern=r"^(short|medium|long)$")
    as_of: date | None = None
    # Memutar ulang snapshot run lain: nol kredit, ticker dan tanggal acuan mengikuti run aslinya.
    replay_of: str | None = Field(default=None, max_length=80)


def register_workflow_routes(app, runner, start_job, require_sectors, cancellations: dict[str, Event]) -> None:
    @router.post("/run", status_code=202)
    def start_workflow(request: WorkflowRunRequest) -> dict:
        if not request.replay_of:
            if not request.ticker:
                raise HTTPException(422, "Sebutkan ticker, atau pakai replay_of untuk memutar ulang run lain.")
            require_sectors()
        try:
            run = runner.create(request.ticker.upper() or "TEST", request.horizon, request.as_of, request.replay_of)
        except WorkflowError as error:
            raise HTTPException(400, str(error)) from error
        return _launch(run["run_id"], resume=False)

    @router.post("/runs/{run_id}/resume", status_code=202)
    def resume_workflow(run_id: str) -> dict:
        if runner.report(run_id) is None and not (runner.run_dir(run_id)).exists():
            raise HTTPException(404, "Run tidak ditemukan; tidak ada checkpoint untuk dilanjutkan.")
        return _launch(run_id, resume=True)

    def _launch(run_id: str, resume: bool) -> dict:
        cancel = Event()

        def work() -> dict:
            try:
                return runner.execute(run_id, resume=resume, cancel=cancel)
            except WorkflowError as error:
                raise RuntimeError(str(error)) from error
            finally:
                cancellations.pop(run_id, None)

        job = start_job("workflow", work)
        cancellations[run_id] = cancel
        return {**job, "run_id": run_id}

    @router.post("/runs/{run_id}/cancel")
    def cancel_workflow(run_id: str) -> dict:
        cancel = cancellations.get(run_id)
        if cancel is None:
            raise HTTPException(409, "Run ini tidak sedang berjalan di proses backend ini.")
        cancel.set()
        return {"run_id": run_id, "cancelling": True}

    @router.get("/runs")
    def list_workflow_runs(ticker: str | None = None, limit: int = 20) -> dict:
        if not 1 <= limit <= 100:
            raise HTTPException(422, "limit harus 1..100.")
        runs = runner.runs(ticker, limit)
        return {"results": runs, "stages": [{"key": key, "label": label} for key, label in NODE_LABELS.items()]}

    @router.get("/runs/{run_id}")
    def get_workflow_run(run_id: str) -> dict:
        runs = [item for item in runner.runs(limit=200) if item["run_id"] == run_id]
        if not runs:
            raise HTTPException(404, "Run tidak ditemukan.")
        return {"run": runs[0], "report": runner.report(run_id)}

    @router.get("/reports/latest")
    def latest_workflow_report(ticker: str) -> dict:
        report = runner.latest_report(ticker)
        if report is None:
            raise HTTPException(404, f"Belum ada laporan tersimpan untuk {ticker.upper()}.")
        return {"report": report}

    app.include_router(router)

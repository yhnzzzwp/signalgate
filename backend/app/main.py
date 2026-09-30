from __future__ import annotations

from contextlib import closing, contextmanager
from threading import Event, Lock, Thread

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.api.routes import register_routes
from app.api.runtime_routes import register_runtime_routes
from app.api.workflow_routes import register_workflow_routes
from app.config import REPO_ROOT, frontier_config_issue, get_settings, sectors_block_reason
from app.db.models import RunJobRecord
from app.db.session import build_session_factory
from app.llm.factory import build_provider
from app.pipeline.audit import persist_screened_event, record_audit
from app.pipeline.presentation import screen_outcome
from app.pipeline.orchestrator import run_pipeline
from app.pipeline.schema import ActionBucket, CandidateEvent
from app.pipeline.sense import FreeFloatLookup, SubsectorValuationLookup, snapshot_company
from app.pipeline import jobs
from app.pipeline.stream import event_source, run_events
from app.research.documents import DocumentSource
from app.runtime import RuntimeStore, resolve
from app.scan import Scanner, mark_published, research_queue_items, summarize_scan
from app.sectors.client import SectorsClient, SectorsAPIError
from app.workflow.runner import WorkflowRunner

app = FastAPI(title="SignalGate", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

settings = get_settings()
session_factory = build_session_factory(settings)
register_routes(app, session_factory)
run_lock = Lock()
workflow_cancellations: dict[str, Event] = {}


def workflow_progress(phase: str, node: str, detail: dict) -> None:
    """Progres disimpan sebelum operasinya mulai, lalu didorong ke SSE; refresh halaman tetap utuh."""
    payload = {"phase": phase, "node": node, **detail}
    if phase == "start":
        with session_factory() as session:
            record_audit(session, stage="workflow", ticker=detail.get("ticker", "*"), detail=payload)
    else:
        run_events.publish("workflow", detail.get("ticker", "*"), payload)


# Pilihan lokasi GPU dan mode frontier dari dashboard; backend/.env tetap satu-satunya sumber kredensial.
runtime_store = RuntimeStore(settings.runtime_directory)


def runtime_settings():
    """Konfigurasi efektif untuk job yang BARU dimulai. Job yang sedang berjalan memegang salinannya sendiri."""
    return resolve(settings, runtime_store)


def record_runtime(resolved, kind: str) -> None:
    with session_factory() as session:
        record_audit(session, stage="runtime", ticker="*", detail={"job": kind, **resolved.snapshot})


workflow_runner = WorkflowRunner(settings, session_factory,
                                 client_factory=lambda: SectorsClient(api_key=settings.sectors_api_keys),
                                 progress=workflow_progress, runtime_store=runtime_store)

with session_factory() as _session:
    _stranded = jobs.release_stale(_session)
if _stranded:
    print(f"[signalgate] {_stranded} run tertinggal dari sesi sebelumnya ditandai gagal.", flush=True)


def require_sectors_key() -> None:
    reason = sectors_block_reason(settings)
    if reason:
        raise HTTPException(400, reason)


def start_job(kind: str, work) -> dict:
    """Jalankan run di latar dan kembalikan identitasnya seketika.

    Run memakan menit. Menahan POST selama itu berarti refresh halaman atau koneksi yang putus
    menghilangkan hasilnya bagi pengguna, padahal backend tetap bekerja. Dengan job tersimpan,
    dashboard bisa menyambung kembali ke run yang sedang berjalan.
    """
    if not run_lock.acquire(blocking=False):
        raise HTTPException(409, "Satu run sedang berjalan; tunggu sampai selesai.")
    try:
        with session_factory() as session:
            job = jobs.create(session, kind)
            payload = jobs.serialize(job)
    except Exception:
        run_lock.release()
        raise
    run_events.set_run(payload["id"])
    run_events.publish("run", "*", {"phase": "start", "kind": kind})

    def runner() -> None:
        try:
            result = work()
            with session_factory() as session:
                jobs.finish(session, payload["id"], result)
            run_events.publish("run", "*", {"phase": "end", "status": jobs.COMPLETED, **result})
        except BaseException as error:
            with session_factory() as session:
                jobs.fail(session, payload["id"], f"{type(error).__name__}: {error}")
            run_events.publish("run", "*", {"phase": "end", "status": jobs.FAILED,
                                            "error": f"{type(error).__name__}: {error}"})
        finally:
            run_events.set_run(None)
            run_lock.release()

    Thread(target=runner, name=f"signalgate-{kind}", daemon=True).start()
    return payload


@app.post("/pipeline/run", status_code=202)
def trigger_pipeline() -> dict:
    require_sectors_key()
    resolved = runtime_settings()

    def work() -> dict:
        provider = None
        record_runtime(resolved, "pipeline")
        try:
            provider = build_provider(resolved.settings)
            client = SectorsClient(api_key=settings.sectors_api_keys)
            with session_factory() as session:
                run = run_pipeline(client, provider, session, settings.pipeline_max_events)
            return {"screened_count": len(run.results), "already_processed": run.already_processed,
                    "provider": provider.name, "runtime": resolved.snapshot}
        except SectorsAPIError as error:
            raise RuntimeError("Sectors tidak dapat dihubungi; periksa key, kuota, atau jaringan.") from error
        finally:
            if provider is not None:
                provider.close()

    return start_job("pipeline", work)


@app.post("/scan/run", status_code=202)
def trigger_scan(limit: int = 3) -> dict:
    """Jalur Scrapling: temukan kandidat dari halaman publik lalu riset PDF-nya. Nol kredit Sectors.

    Sengaja tidak memanggil require_sectors_key(): inilah jalur yang tetap hidup saat mode hemat.
    """
    if not 1 <= limit <= 20:
        raise HTTPException(422, "limit harus 1..20.")
    resolved = runtime_settings()

    def work() -> dict:
        record_runtime(resolved, "scan")
        source = DocumentSource(settings.research_library_dir, settings.source_cache_mode,
                                settings.source_cache_ttl_seconds, settings.research_pdf_max_pages)
        scanner = Scanner(source, settings.scan_directory, {},
                          settings.scan_max_articles, settings.scan_max_listing_pages)
        report = scanner.discover(settings.scan_sources)
        provider_name, screened_count = None, 0
        with session_factory() as session:
            record_audit(session, stage="scan", ticker="*", detail={
                "run_id": report["run_id"], "coverage": report["coverage"],
                "candidates": len(report["candidates"]), "articles_checked": report["articles_checked"],
                "announcements_matched": report["announcements_matched"], "failures": report["failures"][:10],
            })
            position = {"index": 0, "total": 0}

            def announce(event, index, total):
                position.update(index=index, total=total)
                run_events.publish("case", event.ticker,
                                   {"phase": "start", "index": index, "total": total,
                                    "bucket": event.bucket.value})

            with closing(research_queue_items(resolved.settings, settings.scan_directory, limit,
                                              on_start=announce)) as researched:
                for event, outcome, item in researched:
                    provider_name = outcome.verdict.provider
                    screened = screen_outcome(event, None, outcome)
                    record_audit(session, stage="research", ticker=event.ticker,
                                 detail=outcome.model_dump(mode="json", exclude={"verdict", "evidence"}))
                    record_audit(session, stage="gate", ticker=event.ticker,
                                 detail={"status": screened.gate.status.value,
                                         "rejected_terms": screened.gate.rejected_terms})
                    persist_screened_event(session, screened, dedupe_key=f"scan:{item['id']}")
                    mark_published(settings.scan_directory, item["id"], item.get("case_id"))
                    screened_count += 1
                    run_events.publish("case", event.ticker,
                                       {"phase": "end", **position, "label": screened.verdict.label.value})
        return summarize_scan(settings.scan_directory, report, screened_count, provider_name)

    return start_job("scan", work)


@app.post("/scan/retry/{ticker}", status_code=202)
def retry_scan_ticker(ticker: str) -> dict:
    """Paksa proses ulang kandidat tertunda/gagal satu emiten, tanpa menunggu jadwal retry otomatis
    dan tanpa scan sumber baru. Jalur yang sama dengan /scan/run, nol kredit Sectors.
    """
    ticker = ticker.strip().upper()
    if not ticker.isalnum() or len(ticker) > 12:
        raise HTTPException(422, "Kode saham tidak valid.")
    resolved = runtime_settings()

    def work() -> dict:
        record_runtime(resolved, "scan_retry")
        screened_count = 0
        with session_factory() as session:
            position = {"index": 0, "total": 0}

            def announce(event, index, total):
                position.update(index=index, total=total)
                run_events.publish("case", event.ticker,
                                   {"phase": "start", "index": index, "total": total,
                                    "bucket": event.bucket.value})

            with closing(research_queue_items(resolved.settings, settings.scan_directory, settings.scan_max_articles,
                                              retry=True, tickers={ticker}, on_start=announce)) as researched:
                for event, outcome, item in researched:
                    screened = screen_outcome(event, None, outcome)
                    record_audit(session, stage="research", ticker=event.ticker,
                                 detail=outcome.model_dump(mode="json", exclude={"verdict", "evidence"}))
                    record_audit(session, stage="gate", ticker=event.ticker,
                                 detail={"status": screened.gate.status.value,
                                         "rejected_terms": screened.gate.rejected_terms})
                    persist_screened_event(session, screened, dedupe_key=f"scan:{item['id']}")
                    mark_published(settings.scan_directory, item["id"], item.get("case_id"))
                    screened_count += 1
                    run_events.publish("case", event.ticker,
                                       {"phase": "end", **position, "label": screened.verdict.label.value})
        return {"ticker": ticker, "screened_count": screened_count}

    return start_job("scan_retry", work)


class ResearchRequest(BaseModel):
    ticker: str = Field(pattern=r"^[A-Za-z0-9]{1,12}$")
    headline: str = Field(min_length=1, max_length=1000)
    source_url: str = Field(pattern=r"^https?://", max_length=2000)
    body: str = Field(default="", max_length=10000)
    published_at: str = ""
    bucket: ActionBucket = ActionBucket.general_action


@app.post("/research/run")
def research_single_case(request: ResearchRequest) -> dict:
    require_sectors_key()
    if not run_lock.acquire(blocking=False):
        raise HTTPException(409, "Riset masih berjalan.")
    provider = None
    try:
        resolved = runtime_settings()
        record_runtime(resolved, "research")
        provider = build_provider(resolved.settings)
        client = SectorsClient(api_key=settings.sectors_api_keys)
        event = CandidateEvent(**request.model_dump(), matched_keywords=[])
        snapshot = snapshot_company(client, event.ticker, event.sub_sector, FreeFloatLookup(client),
                                    valuation_lookup=SubsectorValuationLookup(client),
                                    with_filings=True)
        outcome = provider.research(event, snapshot, report=client.last_report, require_sectors=True)
        screened = screen_outcome(event, snapshot, outcome)
        with session_factory() as session:
            persist_screened_event(session, screened)
            record_audit(session, "research", event.ticker,
                         outcome.model_dump(mode="json", exclude={"verdict", "evidence"}))
        return {
            **outcome.model_dump(mode="json", exclude={"verdict"}),
            "verdict": screened.verdict.model_dump(mode="json"),
            "gate": screened.gate.model_dump(mode="json"),
            "numeric_issues": screened.numeric_issues,
        }
    finally:
        if provider is not None:
            provider.close()
        run_lock.release()


@app.get("/runs/active")
def active_run() -> dict | None:
    """Run yang sedang berjalan, supaya dashboard bisa menyambung lagi setelah halaman dimuat ulang."""
    with session_factory() as session:
        record = jobs.active(session)
        return jobs.serialize(record) if record else None


@app.get("/runs")
def list_runs(limit: int = 10) -> list[dict]:
    if not 1 <= limit <= 50:
        raise HTTPException(422, "limit harus 1..50.")
    with session_factory() as session:
        return [jobs.serialize(record) for record in jobs.latest(session, limit)]


@app.get("/runs/{job_id}")
def get_run(job_id: str) -> dict:
    with session_factory() as session:
        record = session.get(RunJobRecord, job_id)
        if record is None:
            raise HTTPException(404, "Run tidak ditemukan.")
        return jobs.serialize(record)


@app.get("/run/stream")
async def run_stream(request: Request) -> StreamingResponse:
    """Tahap pipeline langsung lewat SSE, menggantikan polling /audit oleh dashboard.

    Klien yang baru tersambung (refresh halaman) menerima riwayat run yang sedang berjalan lebih
    dulu. EventSource yang menyambung ulang mengirim `Last-Event-ID`, jadi hanya sisanya yang dikirim.
    """
    header = request.headers.get("last-event-id", "")
    after = int(header) if header.isdigit() else None
    return StreamingResponse(
        event_source(last_event_id=after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/frontier/status")
def frontier_status() -> dict:
    """Status reviewer frontier untuk dashboard. Hanya `key_configured` (bool), tidak pernah nilai key."""
    effective = runtime_settings().settings
    status = {**effective.public_frontier(), "issue": frontier_config_issue(effective)}
    if effective.frontier_enabled:
        from app.frontier.service import FrontierService

        try:
            status["usage_today"] = FrontierService(effective).ledger.usage()
        except Exception as error:  # noqa: BLE001 - status tidak boleh menjatuhkan dashboard
            status["usage_today"] = {"error": type(error).__name__}
    return status


_frontier_issue = frontier_config_issue(settings)
if settings.frontier_enabled:
    print(f"[signalgate] Frontier {settings.frontier_model} "
          f"({settings.public_frontier()['model_version'] or 'versi tidak dikenal'}) aktif, mode {settings.frontier_mode}"
          + (f" -- {_frontier_issue}" if _frontier_issue else "."), flush=True)


register_workflow_routes(app, workflow_runner, start_job, lambda: require_sectors_key(), workflow_cancellations)


def _active_run() -> dict | None:
    with session_factory() as session:
        record = jobs.active(session)
        return jobs.serialize(record) if record else None


@contextmanager
def _machine_free():
    """Pegang run_lock selama pembuktian GPU: probe tidak berjalan bersamaan dengan analisis, dan analisis
    baru tidak bisa mulai sampai probe selesai (start_job menolak dengan 409)."""
    if not run_lock.acquire(blocking=False):
        yield False
        return
    try:
        yield True
    finally:
        run_lock.release()


register_runtime_routes(app, settings, runtime_store, _active_run, exclusive=_machine_free)


# Register after API routes so missing API resources never shadow existing endpoints.
_dashboard = REPO_ROOT / "frontend" / "dist"
if (_dashboard / "index.html").is_file():
    app.mount("/", StaticFiles(directory=_dashboard, html=True), name="dashboard")

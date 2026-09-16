from __future__ import annotations

from threading import Lock

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.api.routes import register_routes
from app.config import get_settings, sectors_block_reason
from app.db.session import build_session_factory
from app.llm.factory import build_provider
from app.pipeline.audit import persist_screened_event, record_audit
from app.pipeline.presentation import screen_outcome
from app.pipeline.orchestrator import run_pipeline
from app.pipeline.schema import ActionBucket, CandidateEvent
from app.pipeline.sense import FreeFloatLookup, snapshot_company
from app.sectors.client import SectorsClient, SectorsAPIError

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


def require_sectors_key() -> None:
    reason = sectors_block_reason(settings)
    if reason:
        raise HTTPException(400, reason)


@app.post("/pipeline/run")
def trigger_pipeline() -> dict:
    require_sectors_key()
    if not run_lock.acquire(blocking=False):
        raise HTTPException(409, "Riset masih berjalan.")
    provider = None
    try:
        provider = build_provider(settings)
        client = SectorsClient(api_key=settings.sectors_api_key)
        with session_factory() as session:
            results = run_pipeline(client, provider, session, settings.pipeline_max_events)
    except SectorsAPIError:
        raise HTTPException(502, "Sectors tidak dapat dihubungi; periksa key, kuota, atau jaringan.") from None
    finally:
        if provider is not None:
            provider.close()
        run_lock.release()
    return {"screened_count": len(results), "provider": provider.name}


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
        provider = build_provider(settings)
        client = SectorsClient(api_key=settings.sectors_api_key)
        event = CandidateEvent(**request.model_dump(), matched_keywords=[])
        snapshot = snapshot_company(client, event.ticker, event.sub_sector, FreeFloatLookup(client))
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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}

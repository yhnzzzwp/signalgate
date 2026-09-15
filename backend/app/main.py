from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import register_routes
from app.config import get_settings
from app.db.session import build_session_factory
from app.llm.factory import build_provider
from app.pipeline.orchestrator import run_pipeline
from app.sectors.client import SectorsClient

app = FastAPI(title="SignalGate", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

settings = get_settings()
session_factory = build_session_factory(settings)
register_routes(app, session_factory)


@app.post("/pipeline/run")
def trigger_pipeline() -> dict:
    client = SectorsClient(api_key=settings.sectors_api_key)
    provider = build_provider(settings)
    session = session_factory()
    try:
        results = run_pipeline(client, provider, session)
    finally:
        session.close()
    return {"screened_count": len(results), "provider": provider.name}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}

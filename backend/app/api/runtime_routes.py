"""Readiness and settings for local Ollama on Windows, Linux and macOS."""
from contextlib import contextmanager
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from app.config import Settings
from app.runtime import RuntimeStore, check, resolve

LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost"}
DASHBOARD_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173",
                     "http://localhost:8000", "http://127.0.0.1:8000"}

class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["local"] = "local"
    probe_gpu: bool = False
    check_frontier_key: bool = False

class ActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["local"] = "local"
    frontier_mode: Literal["off", "shadow", "escalation"] = "off"
    allow_not_ready: bool = False

@contextmanager
def _always_free():
    yield True

def require_operator(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in LOCAL_CLIENTS:
        raise HTTPException(403, "Pengaturan runtime hanya bisa diubah dari mesin tempat backend berjalan.")
    origin = request.headers.get("origin")
    if origin and origin not in DASHBOARD_ORIGINS:
        raise HTTPException(403, "Origin tidak diizinkan mengubah pengaturan runtime.")


def public_config(settings, store, active_run):
    state = store.state()
    effective = resolve(settings, store)
    return {"local_only": settings.local_only, "state": state.model_dump(),
            "effective": effective.snapshot, "active_run": active_run(),
            "note": "Ollama berjalan di perangkat ini. Perubahan berlaku untuk run berikutnya."}


def register_runtime_routes(app, settings: Settings, store: RuntimeStore, active_run, *, exclusive=None,
                            transport=None, frontier_client=None):
    router = APIRouter(prefix="/runtime")
    exclusive = exclusive or _always_free

    def run_check(mode=None, probe=False, check_key=False):
        resolved = resolve(settings, store)
        effective = resolved.settings
        if mode is not None:
            effective = effective.model_copy(update={"frontier_enabled": mode != "off",
                                                     "frontier_mode": mode if mode != "off" else effective.frontier_mode})
        required = list(dict.fromkeys(filter(None, [resolved.snapshot["workflow"]["analyst"],
                        *resolved.snapshot["workflow"]["reviewers"], resolved.snapshot["screening"]["analyst"],
                        *resolved.snapshot["screening"]["reviewers"]])))
        return check(effective, target="local", url=None, token="", required_models=required,
                     probe_gpu=probe, check_frontier_key=check_key, transport=transport,
                     frontier_client=frontier_client)

    @router.get("/config")
    def get_config():
        return public_config(settings, store, active_run)

    @router.post("/check")
    def check_runtime(body: CheckRequest, request: Request):
        require_operator(request)
        if settings.local_only and body.check_frontier_key:
            raise HTTPException(422, "Frontier dinonaktifkan dalam mode lokal.")
        if not body.probe_gpu:
            return run_check(check_key=body.check_frontier_key)
        if active_run():
            raise HTTPException(409, "Tunggu analisis selesai sebelum memuat model untuk cek GPU.")
        with exclusive() as free:
            if not free:
                raise HTTPException(409, "Perangkat sedang dipakai run lain.")
            return run_check(probe=True, check_key=body.check_frontier_key)

    @router.post("/activate")
    def activate_runtime(body: ActivateRequest, request: Request):
        require_operator(request)
        if body.frontier_mode != "off":
            if settings.local_only:
                raise HTTPException(422, "Frontier dinonaktifkan dalam mode lokal.")
            if not settings.deepseek_api_key.get_secret_value().strip():
                raise HTTPException(422, "DEEPSEEK_API_KEY belum diisi di backend/.env.")
        result = run_check(body.frontier_mode)
        if not result["ollama"]["ok"] or (not result["ready_for_next_run"] and not body.allow_not_ready):
            raise HTTPException(409, {"message": "Ollama atau model belum siap. Periksa hasil cek kesiapan.",
                                     "check": result, "can_force": result["ollama"]["ok"]})
        store.save(frontier_mode=body.frontier_mode, ready=result["ready_for_next_run"])
        return {**public_config(settings, store, active_run), "check": result,
                "saved_not_ready": not result["ready_for_next_run"]}

    app.include_router(router)

"""Endpoint pengaturan runtime: lokasi GPU (MacBook/Colab), token gateway, mode frontier, cek kesiapan.

Mengubah endpoint adalah operasi operator lokal: hanya dari mesin ini (loopback) dan dari origin dashboard.
Token tidak pernah dikembalikan dalam respons apa pun; yang terlihat hanya `token_configured`, sidik jari
pendek, dan endpoint tempat token itu terikat. Token tersimpan hanya dikirim ke endpoint asalnya: host
baru butuh token baru atau persetujuan eksplisit (`reuse_saved_token`). Key DeepSeek tetap di backend/.env.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr

from app.config import Settings
from app.runtime import EndpointError, RuntimeStore, check, display_url, fingerprint, normalize_endpoint, resolve

LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost"}
DASHBOARD_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}


class CheckRequest(BaseModel):
    target: Literal["local", "colab"]
    colab_url: str | None = Field(default=None, max_length=300)
    # Kosong = pakai token tersimpan, TETAPI hanya bila URL sama dengan endpoint tempat token itu diberikan.
    token: SecretStr | None = Field(default=None, max_length=512)
    # Persetujuan eksplisit memakai token tersimpan untuk host yang berbeda dari asalnya.
    reuse_saved_token: bool = False
    probe_gpu: bool = False
    check_frontier_key: bool = False


class ActivateRequest(BaseModel):
    target: Literal["local", "colab"]
    colab_url: str | None = Field(default=None, max_length=300)
    token: SecretStr | None = Field(default=None, max_length=512)
    reuse_saved_token: bool = False
    frontier_mode: Literal["off", "shadow", "escalation"]
    # Simpan walau model belum lengkap/terbaca (mis. masih diunduh). Hasilnya ditandai "tersimpan, belum siap".
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


def public_config(settings: Settings, store: RuntimeStore, active_run) -> dict:
    state = store.state()
    resolved = resolve(settings, store)
    stored_token = store.token()
    return {"state": {"revision": state.revision, "target": state.target or "env",
                      "colab_url": state.colab_url, "frontier_mode": state.frontier_mode or "env",
                      "updated_at": state.updated_at, "ready_at_activation": state.ready_at_activation,
                      "token_configured": bool(stored_token), "token_fingerprint": fingerprint(stored_token),
                      "token_endpoint": display_url(store.token_endpoint()) if stored_token else None},
            "effective": resolved.snapshot, "active_run": active_run(),
            "note": "Perubahan berlaku untuk run berikutnya; run yang sedang berjalan tidak berubah."}


def register_runtime_routes(app, settings: Settings, store: RuntimeStore, active_run, *, exclusive=None,
                            transport=None, resolver=None, frontier_client=None) -> None:
    """`exclusive()` -> context manager yang menghasilkan True bila mesin bebas dipakai (tidak ada run);
    dipegang selama pembuktian GPU supaya probe tidak berebut memori dengan analisis."""
    router = APIRouter(prefix="/runtime")
    exclusive = exclusive or _always_free
    extra = {key: value for key, value in (("transport", transport), ("resolver", resolver),
                                           ("frontier_client", frontier_client)) if value is not None}
    resolver_kw = {"resolver": resolver} if resolver else {}

    def _normalized(target: str, url: str | None) -> str | None:
        if target != "colab":
            return None
        try:
            return normalize_endpoint("colab", url, default_local=settings.ollama_local_url, **resolver_kw)
        except EndpointError:
            return None

    def _token_plan(target: str, endpoint: str | None, supplied: SecretStr | None, reuse: bool):
        """(token yang dikirim, token yang disimpan atau None = tetap, alasan token ditahan)."""
        if target != "colab":
            return "", None, None
        new = supplied.get_secret_value().strip() if supplied is not None else ""
        if new:
            return new, new, None
        saved, bound = store.token(), store.token_endpoint()
        if not saved:
            return "", None, None
        if endpoint and bound == endpoint:
            return saved, None, None
        if endpoint and reuse:
            return saved, saved, None  # diikat ulang ke host baru atas persetujuan eksplisit
        return "", None, (f"Token tersimpan terikat ke {display_url(bound) or 'endpoint lama'} dan tidak dikirim ke "
                          "host ini. Tempel token baru dari notebook, atau setujui eksplisit pemakaian ulang token "
                          "tersimpan untuk host ini.")

    def _check(target, url, token, frontier_mode=None, probe_gpu=False, check_frontier_key=False) -> dict:
        resolved = resolve(settings, store)
        effective = resolved.settings
        if frontier_mode == "off":
            effective = effective.model_copy(update={"frontier_enabled": False})
        elif frontier_mode in {"shadow", "escalation"}:
            effective = effective.model_copy(update={"frontier_enabled": True, "frontier_mode": frontier_mode})
        required = list(dict.fromkeys([*filter(None, [resolved.snapshot["workflow"]["analyst"]]),
                                       *resolved.snapshot["workflow"]["reviewers"],
                                       *filter(None, [resolved.snapshot["screening"]["analyst"]]),
                                       *resolved.snapshot["screening"]["reviewers"]]))
        return check(effective, target=target, url=url if target == "colab" else None, token=token,
                     required_models=required, probe_gpu=probe_gpu, check_frontier_key=check_frontier_key, **extra)

    @router.get("/config")
    def get_config() -> dict:
        return public_config(settings, store, active_run)

    @router.post("/check")
    def check_runtime(body: CheckRequest, request: Request) -> dict:
        require_operator(request)
        endpoint = _normalized(body.target, body.colab_url)
        token, _keep, withheld = _token_plan(body.target, endpoint, body.token, body.reuse_saved_token)
        if not body.probe_gpu:
            result = _check(body.target, body.colab_url, token, None, False, body.check_frontier_key)
        else:
            # Probe memuat model; jangan berebut memori/urutan offload dengan analisis yang sedang berjalan.
            if active_run():
                raise HTTPException(409, "Pembuktian GPU ditolak: ada run analisis yang sedang berjalan. "
                                         "Ulangi setelah run selesai; cek tanpa probe tetap bisa dipakai.")
            with exclusive() as free:
                if not free:
                    raise HTTPException(409, "Pembuktian GPU ditolak: mesin sedang dipakai run lain.")
                result = _check(body.target, body.colab_url, token, None, True, body.check_frontier_key)
        if withheld:
            result["warnings"].insert(0, withheld)
            result["token_withheld"] = True
        return result

    @router.post("/activate")
    def activate_runtime(body: ActivateRequest, request: Request) -> dict:
        require_operator(request)
        try:
            url = (normalize_endpoint("colab", body.colab_url, default_local=settings.ollama_local_url, **resolver_kw)
                   if body.target == "colab" else None)
        except EndpointError as error:
            raise HTTPException(422, str(error)) from error
        if body.frontier_mode != "off" and not settings.deepseek_api_key.get_secret_value().strip():
            raise HTTPException(422, "Mode frontier shadow/escalation butuh DEEPSEEK_API_KEY di backend/.env "
                                     "(isi sendiri, lalu restart backend). Key tidak bisa diisi dari dashboard.")
        token, keep, withheld = _token_plan(body.target, url, body.token, body.reuse_saved_token)
        if withheld:
            raise HTTPException(422, withheld)
        result = _check(body.target, url, token, body.frontier_mode)
        if not result["ollama"]["ok"]:
            raise HTTPException(409, {"message": "Aktivasi ditolak: Ollama di lokasi itu belum tersambung. "
                                                 "Tidak ada yang diubah; run berikutnya tetap memakai "
                                                 "konfigurasi sebelumnya.", "check": result, "can_force": False})
        ready = result["ready_for_next_run"]
        if not ready and not body.allow_not_ready:
            raise HTTPException(409, {"message": "Aktivasi ditolak: lokasi itu belum siap (model belum lengkap atau "
                                                 "daftar model tidak terbaca). Tidak ada yang diubah. Simpan tetap "
                                                 "hanya bila memang disengaja (mis. model masih diunduh).",
                                      "check": result, "can_force": True})
        store.save(target=body.target, colab_url=url, frontier_mode=body.frontier_mode,
                   token=keep if body.target == "colab" else None, ready=ready)
        return {**public_config(settings, store, active_run), "check": result, "saved_not_ready": not ready}

    app.include_router(router)

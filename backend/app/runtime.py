"""Local Ollama readiness, persisted settings and per-run configuration history."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, SecretStr

from app.config import Settings

Target = Literal["local"]
FrontierChoice = Literal["off", "shadow", "escalation"]

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
# Field yang mengubah siapa/apa yang menghasilkan keputusan. Beda di sini saat resume wajib disetujui.
DECISION_FIELDS = ("target", "ollama_url", "token_fingerprint", "workflow.analyst", "workflow.reviewers",
                   "screening.analyst", "screening.reviewers", "frontier.enabled", "frontier.mode",
                   "frontier.model", "frontier.reasoning_effort", "frontier.max_tokens")


class EndpointError(ValueError):
    """URL endpoint ditolak sebelum ada koneksi apa pun."""


class RuntimeState(BaseModel):
    revision: int = 0
    target: Target | None = None
    frontier_mode: FrontierChoice | None = None
    updated_at: str | None = None
    # Hasil cek saat aktivasi: False berarti konfigurasi tersimpan atas persetujuan eksplisit walau belum siap.
    ready_at_activation: bool | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(secret: str) -> str | None:
    """Sidik jari pendek untuk membandingkan token tanpa menyimpan atau menampilkan nilainya."""
    return hashlib.sha256(secret.encode()).hexdigest()[:10] if secret else None


class RuntimeStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self._lock = threading.Lock()

    @property
    def config_path(self) -> Path:
        return self.directory / "config.json"

    def state(self) -> RuntimeState:
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            # Migrate an old remote selection without reading its saved credentials.
            data["target"] = "local"
            return RuntimeState.model_validate(data)
        except (OSError, ValueError, TypeError):
            return RuntimeState()

    def _write(self, path: Path, text: str, mode: int) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary, mode)
        temporary.replace(path)

    def save(self, *, target: Target = "local", frontier_mode: FrontierChoice | None = None,
             ready: bool | None = None) -> RuntimeState:
        if target != "local":
            raise ValueError("Hanya runtime lokal yang didukung.")
        with self._lock:
            state = RuntimeState(revision=self.state().revision + 1, target="local",
                                 frontier_mode=frontier_mode, updated_at=_now(), ready_at_activation=ready)
            self._write(self.config_path, state.model_dump_json(indent=1), 0o644)
            return state


# -- validasi endpoint ------------------------------------------------------------------------------

def _is_loopback(host: str) -> bool:
    if host in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def display_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    host = parts.hostname or ""
    host = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{host}" + (f":{parts.port}" if parts.port else "")


def normalize_endpoint(target: Target, url: str | None, *, default_local: str) -> str:
    if target != "local":
        raise EndpointError("Hanya Ollama lokal yang didukung.")
    raw = (url or default_local).strip()
    try:
        parts = urlsplit(raw)
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        raise EndpointError("URL Ollama tidak valid.") from None
    if parts.scheme not in {"http", "https"} or not _is_loopback(host):
        raise EndpointError("Ollama harus berada di localhost, 127.0.0.1, atau ::1.")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in {"", "/"}:
        raise EndpointError("Gunakan URL dasar Ollama tanpa kredensial, path, query, atau fragmen.")
    netloc = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{netloc}" + (f":{port}" if port else "")


# -- settings efektif dan snapshot per run ----------------------------------------------------------

@dataclass(frozen=True)
class Resolved:
    settings: Settings
    snapshot: dict


def _screening_models(settings: Settings) -> tuple[str | None, list[str]]:
    if settings.llm_backend != "ollama":
        return None, []
    if settings.ollama_reviewer_models:
        return settings.ollama_model, list(dict.fromkeys(settings.ollama_reviewer_models))
    return settings.ollama_model, [settings.ollama_validator_model or settings.ollama_model]


def snapshot(settings: Settings, *, target: str, revision: int, token: str) -> dict:
    """Konfigurasi efektif NONRAHASIA: cukup untuk mengaudit dan membandingkan saat resume."""
    from app.frontier.pricing import MODEL_VERSIONS
    from app.workflow.models import resolve_models, resolve_reviewers

    analyst, reviewers = _screening_models(settings)
    return {
        "revision": revision, "target": target, "ollama_url": display_url(settings.ollama_base_url),
        "auth": "token" if token else "none", "token_fingerprint": fingerprint(token),
        "workflow": {"analyst": resolve_models(settings)[0], "reviewers": resolve_reviewers(settings)},
        "screening": {"analyst": analyst, "reviewers": reviewers},
        "frontier": {"enabled": settings.frontier_enabled, "mode": settings.frontier_mode,
                     "model": settings.frontier_model, "model_version": MODEL_VERSIONS.get(settings.frontier_model),
                     "reasoning_effort": settings.frontier_reasoning_effort,
                     "max_tokens": settings.frontier_max_tokens, "cache_mode": settings.frontier_cache_mode,
                     "calls_on_replay": settings.frontier_calls_on_replay,
                     "key_configured": bool(settings.deepseek_api_key.get_secret_value().strip())},
    }


def resolve(base: Settings, store: RuntimeStore | None) -> Resolved:
    """Settings per run dari env + pilihan runtime. Salinan baru setiap kali: run lain tidak ikut berubah."""
    state = store.state() if store is not None else RuntimeState()
    endpoint = normalize_endpoint("local", None, default_local=base.ollama_local_url)
    updates = {"ollama_base_url": endpoint, "ollama_auth_token": SecretStr("")}
    if base.local_only or state.frontier_mode == "off":
        updates["frontier_enabled"] = False
    elif state.frontier_mode in {"shadow", "escalation"}:
        updates.update(frontier_enabled=True, frontier_mode=state.frontier_mode)
    settings = base.model_copy(update=updates)
    return Resolved(settings, snapshot(settings, target="local", revision=state.revision, token=""))


def _field(snapshot_value: dict, dotted: str):
    value = snapshot_value
    for key in dotted.split("."):
        value = (value or {}).get(key) if isinstance(value, dict) else None
    return value


def diff_snapshots(before: dict, after: dict) -> list[dict]:
    return [{"field": field, "before": _field(before, field), "after": _field(after, field)}
            for field in DECISION_FIELDS if _field(before, field) != _field(after, field)]


# -- pemeriksaan kesiapan ---------------------------------------------------------------------------

def _installed(tags: dict) -> set[str]:
    names = set()
    for item in tags.get("models") or []:
        if isinstance(item, dict) and item.get("name"):
            names.add(item["name"])
            names.add(item["name"].removesuffix(":latest"))
    return names


def _gpu_from_ps(ps: dict) -> dict:
    loaded = [item for item in ps.get("models") or [] if isinstance(item, dict)]
    if not loaded:
        return {"status": "unknown", "detail": "Belum ada model dimuat; jalankan cek GPU (memuat model sebentar) "
                                               "atau lihat `ollama ps` saat run berlangsung."}
    rows = [{"model": item.get("name"), "size": item.get("size"), "size_vram": item.get("size_vram")}
            for item in loaded]
    on_gpu = [row for row in rows if (row["size_vram"] or 0) > 0]
    if not on_gpu:
        return {"status": "not_proven", "detail": "Model berjalan di CPU. Tetap dapat digunakan, tetapi lebih lambat.",
                "loaded": rows}
    full = all((row["size_vram"] or 0) >= (row["size"] or 0) > 0 for row in rows)
    return {"status": "proven", "detail": ("Model dimuat penuh di memori GPU." if full else
                                           "Sebagian model di GPU, sebagian di CPU (lebih lambat)."),
            "loaded": rows}


def check(settings: Settings, *, target: Target, url: str | None, token: str, required_models: list[str],
          probe_gpu: bool = False, check_frontier_key: bool = False, transport: httpx.BaseTransport | None = None,
          frontier_client=None) -> dict:
    """Status terpisah: backend hidup, endpoint valid, Ollama terhubung, model tersedia, GPU terbukti,
    frontier. `probe_gpu` memuat satu model lokal sebentar (gratis, di GPU sendiri); cek key DeepSeek
    hanya memanggil endpoint metadata (saldo, daftar model), tidak pernah generasi berbayar."""
    result = {"backend": {"ok": True}, "target": target, "endpoint": {"ok": False, "url": None, "error": None},
              "ollama": {"ok": False, "status": "not_checked", "version": None, "error": None},
              "models": {"ok": False, "status": "not_checked", "required": list(dict.fromkeys(required_models)),
                         "missing": [], "installed": [], "error": None},
              "gpu": {"status": "unknown", "detail": "Belum diperiksa."}, "frontier": {}, "warnings": [],
              "ready_for_next_run": False, "checked_at": _now()}
    try:
        endpoint = normalize_endpoint(target, url, default_local=settings.ollama_local_url)
        result["endpoint"] = {"ok": True, "url": display_url(endpoint), "error": None}
    except EndpointError as error:
        result["endpoint"]["error"] = str(error)
        result["ollama"].update(status="invalid_endpoint", error=str(error))
        result["frontier"] = _frontier_status(settings, check_frontier_key, frontier_client)
        return result
    with httpx.Client(timeout=10.0, follow_redirects=False, transport=transport) as client:
        def get(path: str):
            response = client.get(f"{endpoint}{path}")
            if 300 <= response.status_code < 400:
                raise EndpointError(f"Endpoint mengalihkan (HTTP {response.status_code}); redirect ditolak.")
            return response

        try:
            response = get("/api/version")
            if response.status_code in (401, 403):
                result["ollama"].update(status="unauthorized", error="Ollama lokal menolak akses.")
            elif response.status_code >= 500 or response.status_code == 404:
                result["ollama"].update(status="disconnected", error="Ollama lokal tidak tersedia; jalankan ollama serve.")
            else:
                response.raise_for_status()
                result["ollama"].update(ok=True, status="connected", version=response.json().get("version"))
        except EndpointError as error:
            result["ollama"].update(status="redirect_refused", error=str(error))
        except (httpx.HTTPError, ValueError, AttributeError) as error:
            result["ollama"].update(status="disconnected", error=(
                f"Tidak tersambung ({type(error).__name__}). Pastikan Ollama berjalan; backend tidak "
                "otomatis pindah ke mesin lain."))
        if result["ollama"]["ok"]:
            # Daftar model punya status berhasil sendiri: gagal dibaca TIDAK sama dengan "semua tersedia".
            try:
                response = get("/api/tags")
                response.raise_for_status()
                tags = response.json()
                if not isinstance(tags, dict) or not isinstance(tags.get("models"), list):
                    raise ValueError("respons /api/tags bukan daftar model")
                installed = _installed(tags)
                result["models"].update(ok=True, status="read", installed=sorted(installed))
                result["models"]["missing"] = [name for name in result["models"]["required"]
                                               if name not in installed and f"{name}:latest" not in installed]
            except (httpx.HTTPError, ValueError, AttributeError, EndpointError) as error:
                detail = (f"HTTP {error.response.status_code}" if isinstance(error, httpx.HTTPStatusError)
                          else type(error).__name__)
                result["models"].update(status="unreadable", error=f"Daftar model tidak terbaca ({detail}).")
                result["warnings"].append(result["models"]["error"] + " Kesiapan tidak bisa dipastikan.")
            if result["models"]["ok"]:
                try:
                    result["gpu"] = _gpu_status(get, client, endpoint, target, probe_gpu,
                                                [name for name in result["models"]["required"]
                                                 if name not in result["models"]["missing"]])
                except (httpx.HTTPError, ValueError, AttributeError, EndpointError) as error:
                    result["warnings"].append(f"Status GPU tidak terbaca: {type(error).__name__}.")
    if result["models"]["missing"]:
        result["warnings"].append("Model belum diunduh: " + ", ".join(result["models"]["missing"]))
    if result["gpu"]["status"] == "not_proven":
        result["warnings"].append(result["gpu"]["detail"])
    result["frontier"] = _frontier_status(settings, check_frontier_key, frontier_client)
    result["ready_for_next_run"] = bool(result["ollama"]["ok"] and result["models"]["ok"]
                                        and not result["models"]["missing"])
    return result


def _gpu_status(get, client: httpx.Client, endpoint: str, target: str, probe: bool, available: list[str]) -> dict:
    if probe and available:
        model = available[-1]
        try:
            response = client.post(f"{endpoint}/api/generate", timeout=600.0,
                                   json={"model": model, "prompt": "ok", "stream": False, "keep_alive": "30s",
                                         "options": {"num_ctx": 2048, "num_predict": 1}})
            response.raise_for_status()
            loaded = get("/api/ps").json()
            loaded["models"] = [row for row in loaded.get("models", [])
                                if row.get("name", "").removesuffix(":latest") == model.removesuffix(":latest")]
            return {**_gpu_from_ps(loaded), "probe_model": model}
        finally:
            # Release probe memory before a real analysis starts, including on failed probes.
            try:
                client.post(f"{endpoint}/api/generate", timeout=30.0,
                            json={"model": model, "keep_alive": 0}).raise_for_status()
            except httpx.HTTPError:
                pass  # keep_alive=30s bounds retention if the server cannot unload immediately.
    return _gpu_from_ps(get("/api/ps").json())


def _frontier_status(settings: Settings, check_key: bool, frontier_client=None) -> dict:
    from app.config import frontier_config_issue
    from app.frontier.pricing import MODEL_VERSIONS

    status = {"enabled": settings.frontier_enabled, "mode": settings.frontier_mode, "model": settings.frontier_model,
              "model_version": MODEL_VERSIONS.get(settings.frontier_model),
              "key_configured": bool(settings.deepseek_api_key.get_secret_value().strip()),
              "issue": frontier_config_issue(settings), "account": None}
    if check_key and status["key_configured"]:
        client = frontier_client
        if client is None:
            from app.frontier.client import DeepSeekClient
            client = DeepSeekClient(settings.deepseek_api_key.get_secret_value(),
                                    base_url=settings.frontier_base_url, model=settings.frontier_model)
        try:
            status["account"] = client.account()
        finally:
            if frontier_client is None:
                client.close()
    return status


# -- pengikatan konfigurasi ke run -----------------------------------------------------------------

class RunBinding(BaseModel):
    bound: dict
    history: list[dict]


def load_binding(run_dir: Path) -> RunBinding | None:
    try:
        return RunBinding.model_validate_json((Path(run_dir) / "runtime.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_binding(run_dir: Path, binding: RunBinding) -> None:
    path = Path(run_dir) / "runtime.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(binding.model_dump_json(indent=1), encoding="utf-8")
    temporary.replace(path)

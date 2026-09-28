"""Konfigurasi runtime: lokasi inferensi (MacBook/Colab) dan mode frontier untuk run BERIKUTNYA.

Aturan yang dijaga modul ini:
- `backend/.env` tetap satu-satunya sumber kredensial backend (Sectors, DeepSeek). Tidak ada skrip yang
  menyalin file env lagi; memilih lokasi GPU tidak pernah mengubah key, mode frontier, atau batas biaya.
- Pilihan dari dashboard disimpan di `data/runtime/config.json` (tanpa rahasia). Token gateway Colab
  disimpan terpisah di `data/runtime/secrets.json` (izin 0600) dan tidak pernah dikirim balik ke browser,
  log, status, atau audit -- yang keluar hanya `token_configured` dan sidik jari pendek.
- Precedence: pilihan runtime yang pernah diaktifkan > nilai env. Target "env" = belum pernah diaktifkan
  (perilaku lama, `OLLAMA_BASE_URL` dari env).
- Setiap run memegang salinan settings-nya sendiri. Aktivasi hanya berlaku untuk run berikutnya; run yang
  sedang berjalan tidak berubah. Resume dengan konfigurasi berbeda harus disetujui eksplisit dan dicatat.
- Pemeriksaan kesiapan tidak pernah memanggil generasi DeepSeek berbayar.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, SecretStr

from app.config import Settings

Target = Literal["local", "colab"]
FrontierChoice = Literal["off", "shadow", "escalation"]

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
# Link halaman (bukan URL layanan) yang sering tertempel dari browser.
NOTEBOOK_HOSTS = ("colab.research.google.com", "colab.google.com", "colab.google", "drive.google.com",
                  "accounts.google.com", "docs.google.com")
# Penanda di setiap jawaban gateway Colab (colab/ollama_gateway.py). Header `Server` tidak bisa dipakai karena
# Cloudflare menggantinya; tanpa penanda ini, layanan lain di URL tunnel bisa dikira gateway.
GATEWAY_MARKER = "x-signalgate-gateway"
# Field yang mengubah siapa/apa yang menghasilkan keputusan. Beda di sini saat resume wajib disetujui.
DECISION_FIELDS = ("target", "ollama_url", "token_fingerprint", "workflow.analyst", "workflow.reviewers",
                   "screening.analyst", "screening.reviewers", "frontier.enabled", "frontier.mode",
                   "frontier.model", "frontier.reasoning_effort", "frontier.max_tokens")


class EndpointError(ValueError):
    """URL endpoint ditolak sebelum ada koneksi apa pun."""


class RuntimeState(BaseModel):
    revision: int = 0
    target: Target | None = None
    colab_url: str | None = None
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

    @property
    def secrets_path(self) -> Path:
        return self.directory / "secrets.json"

    def state(self) -> RuntimeState:
        try:
            return RuntimeState.model_validate_json(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return RuntimeState()

    def _secret(self) -> dict:
        try:
            data = json.loads(self.secrets_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def token(self) -> str:
        """Token tersimpan apa adanya (untuk status/sidik jari). Untuk DIKIRIM, pakai `token_for`."""
        return str(self._secret().get("colab_token") or "")

    def token_endpoint(self) -> str | None:
        """Endpoint tempat token tersimpan diberikan. Token lama tanpa endpoint dianggap tidak terikat."""
        return self._secret().get("endpoint") or None

    def token_for(self, endpoint: str | None) -> str:
        """Token hanya untuk endpoint asalnya: host baru tidak pernah menerima token lama secara otomatis."""
        return self.token() if endpoint and self.token_endpoint() == endpoint else ""

    def _write(self, path: Path, text: str, mode: int) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary, mode)
        temporary.replace(path)

    def save(self, *, target: Target, colab_url: str | None, frontier_mode: FrontierChoice | None,
             token: str | None, ready: bool | None = None) -> RuntimeState:
        """`token=None` mempertahankan token lama (tetap terikat ke endpoint lamanya); string kosong
        menghapusnya. Token baru selalu diikat ke `colab_url` yang disimpan bersamanya."""
        with self._lock:
            state = RuntimeState(revision=self.state().revision + 1, target=target, colab_url=colab_url,
                                 frontier_mode=frontier_mode, updated_at=_now(), ready_at_activation=ready)
            self._write(self.config_path, state.model_dump_json(indent=1), 0o644)
            if token is not None:
                self._write(self.secrets_path, json.dumps({"colab_token": token, "endpoint": colab_url if token else None}),
                            0o600)
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
    return f"{parts.scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")


def normalize_endpoint(target: Target, url: str | None, *, default_local: str,
                       resolver=socket.getaddrinfo) -> str:
    """URL layanan yang aman dipakai, atau EndpointError dengan petunjuk.

    Lokal: hanya loopback (http boleh). Colab: wajib https, tanpa kredensial/query/path, bukan link halaman
    notebook, dan setiap alamat hasil resolusi DNS harus publik -- endpoint tidak boleh jadi proxy ke
    alamat internal, loopback, link-local (termasuk metadata cloud 169.254.169.254), atau CGNAT.
    Keterbatasan: DNS diresolusi saat validasi; klien HTTP meresolusi ulang saat request (rebinding tidak
    ditutup sepenuhnya), sehingga redirect juga selalu ditolak.
    """
    raw = (url or "").strip() or (default_local if target == "local" else "")
    if not raw:
        raise EndpointError("URL layanan Colab wajib diisi: salin URL https://…trycloudflare.com yang dicetak notebook.")
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if any(host == item or host.endswith("." + item) for item in NOTEBOOK_HOSTS):
        raise EndpointError("Ini link halaman notebook/Google, bukan URL layanan. Salin URL "
                            "https://…trycloudflare.com yang dicetak sel terakhir notebook.")
    if parts.scheme not in {"http", "https"} or not host:
        raise EndpointError("URL harus diawali http:// atau https:// dan memuat nama host.")
    if parts.username or parts.password:
        raise EndpointError("URL tidak boleh memuat nama pengguna/kata sandi; token diisi di field token.")
    if parts.query or parts.fragment or parts.path not in {"", "/"}:
        raise EndpointError("Pakai URL dasar layanan saja (tanpa path, ?query, atau #fragmen).")
    netloc = host if ":" not in host else f"[{host}]"
    if parts.port:
        netloc += f":{parts.port}"
    normalized = f"{parts.scheme}://{netloc}"
    if target == "local":
        if not _is_loopback(host):
            raise EndpointError("Mode lokal hanya untuk Ollama di mesin ini (localhost/127.0.0.1). "
                                "Untuk mesin lain pilih mode Colab/remote.")
        return normalized
    if _is_loopback(host):
        raise EndpointError("Mode Colab tidak menerima alamat loopback; pilih mode lokal untuk Ollama di mesin ini.")
    if parts.scheme != "https":
        raise EndpointError("Endpoint remote wajib HTTPS supaya token tidak terkirim tanpa enkripsi.")
    try:
        addresses = {info[4][0] for info in resolver(host, parts.port or 443, proto=socket.IPPROTO_TCP)}
    except (socket.gaierror, UnicodeError, OSError):
        raise EndpointError(f"Nama host {host} tidak bisa diresolusi; URL tunnel mungkin sudah kedaluwarsa.") from None
    if not addresses:
        raise EndpointError(f"Nama host {host} tidak punya alamat.")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global or ip.is_multicast:
            raise EndpointError(f"{host} mengarah ke alamat non-publik ({ip}); ditolak agar backend tidak menjadi "
                                "proxy ke jaringan internal atau metadata.")
    return normalized


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
    updates: dict = {}
    target = state.target or "env"
    token = base.ollama_auth_token.get_secret_value()
    if state.target == "local":
        updates["ollama_base_url"] = base.ollama_local_url
        token = ""
    elif state.target == "colab" and state.colab_url:
        updates["ollama_base_url"] = state.colab_url
        # Hanya token yang diikat ke endpoint ini; token env/lama tidak pernah dikirim ke host Colab lain.
        token = store.token_for(state.colab_url) if store is not None else ""
    updates["ollama_auth_token"] = SecretStr(token)
    if state.frontier_mode == "off":
        updates["frontier_enabled"] = False
    elif state.frontier_mode in {"shadow", "escalation"}:
        updates["frontier_enabled"] = True
        updates["frontier_mode"] = state.frontier_mode
    settings = base.model_copy(update=updates)
    return Resolved(settings, snapshot(settings, target=target, revision=state.revision, token=token))


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
        return {"status": "not_proven", "detail": "Model dimuat tanpa memori GPU (size_vram=0): jatuh ke CPU.",
                "loaded": rows}
    full = all((row["size_vram"] or 0) >= (row["size"] or 0) for row in on_gpu)
    return {"status": "proven", "detail": ("Model dimuat penuh di memori GPU." if full else
                                           "Sebagian model di GPU, sebagian di CPU (lebih lambat)."),
            "loaded": rows}


def check(settings: Settings, *, target: Target, url: str | None, token: str, required_models: list[str],
          probe_gpu: bool = False, check_frontier_key: bool = False, transport: httpx.BaseTransport | None = None,
          resolver=socket.getaddrinfo, frontier_client=None) -> dict:
    """Status terpisah: backend hidup, endpoint valid, Ollama terhubung, model tersedia, GPU terbukti,
    frontier. `probe_gpu` memuat satu model lokal/Colab sebentar (gratis, di GPU sendiri); cek key DeepSeek
    hanya memanggil endpoint metadata (saldo, daftar model), tidak pernah generasi berbayar."""
    result = {"backend": {"ok": True}, "target": target, "endpoint": {"ok": False, "url": None, "error": None},
              "ollama": {"ok": False, "status": "not_checked", "version": None, "error": None},
              "models": {"ok": False, "status": "not_checked", "required": list(dict.fromkeys(required_models)),
                         "missing": [], "installed": [], "error": None},
              "gpu": {"status": "unknown", "detail": "Belum diperiksa."}, "frontier": {}, "warnings": [],
              "ready_for_next_run": False, "checked_at": _now()}
    try:
        endpoint = normalize_endpoint(target, url, default_local=settings.ollama_local_url, resolver=resolver)
        result["endpoint"] = {"ok": True, "url": display_url(endpoint), "error": None}
    except EndpointError as error:
        result["endpoint"]["error"] = str(error)
        result["ollama"].update(status="invalid_endpoint", error=str(error))
        result["frontier"] = _frontier_status(settings, check_frontier_key, frontier_client)
        return result
    if target == "colab" and not token:
        result["warnings"].append("Token gateway Colab kosong; gateway SignalGate akan menolak tanpa token.")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(timeout=10.0, headers=headers, follow_redirects=False, transport=transport) as client:
        def get(path: str):
            response = client.get(f"{endpoint}{path}")
            if 300 <= response.status_code < 400:
                raise EndpointError(f"Endpoint mengalihkan (HTTP {response.status_code}); redirect ditolak.")
            return response

        try:
            response = get("/api/version")
            is_gateway = response.headers.get(GATEWAY_MARKER) == "1"
            if target == "colab" and response.status_code < 500 and not is_gateway:
                result["ollama"].update(status="not_gateway", error=(
                    f"HTTP {response.status_code} dari layanan LAIN, bukan gateway SignalGate. Biasanya: notebook versi "
                    "lama (gateway di port 8080 bentrok dengan Jupyter Server milik Colab, sehingga tunnel "
                    "meneruskan ke Jupyter), atau URL yang ditempel salah. Matikan tunnel itu, jalankan notebook "
                    "terbaru, lalu tempel URL + token baru."))
            elif response.status_code in (401, 403):
                result["ollama"].update(status="unauthorized",
                                        error=f"Gateway menolak token (HTTP {response.status_code}).")
            elif response.status_code >= 500 or response.status_code == 404:
                result["ollama"].update(status="disconnected", error=(
                    f"HTTP {response.status_code}: tunnel/Ollama tidak tersambung. URL Colab mungkin kedaluwarsa; "
                    "jalankan ulang notebook dan tempel URL baru. Backend tidak otomatis pindah mesin."))
            else:
                response.raise_for_status()
                result["ollama"].update(ok=True, status="connected", version=response.json().get("version"))
        except EndpointError as error:
            result["ollama"].update(status="redirect_refused", error=str(error))
        except (httpx.HTTPError, ValueError, AttributeError) as error:
            result["ollama"].update(status="disconnected", error=(
                f"Tidak tersambung ({type(error).__name__}). Pastikan Ollama/notebook berjalan; backend tidak "
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
    if target == "colab":
        response = get("/gateway/info")
        if response.status_code == 200:
            gpus = response.json().get("gpus") or []
            if gpus:
                names = ", ".join(f"{gpu.get('name')} ({gpu.get('memory_total_mb')} MB)" for gpu in gpus)
                gateway = {"status": "proven", "detail": f"nvidia-smi di runtime Colab: {names}.", "gpus": gpus}
            else:
                gateway = {"status": "not_proven", "detail": "Runtime Colab tanpa GPU (nvidia-smi tidak menemukan "
                                                             "GPU). Ubah Runtime type ke GPU.", "gpus": []}
            if not probe:
                return gateway
        elif not probe:
            return {"status": "unknown", "detail": "Gateway tidak menyediakan info GPU (notebook versi lama?)."}
    if probe and available:
        model = available[-1]
        response = client.post(f"{endpoint}/api/generate", timeout=600.0,
                               json={"model": model, "prompt": "ok", "stream": False, "keep_alive": "30s",
                                     "options": {"num_predict": 1}})
        if response.status_code != 200:
            return {"status": "unknown", "detail": f"Probe GPU gagal memuat {model} (HTTP {response.status_code})."}
        status = _gpu_from_ps(get("/api/ps").json())
        return {**status, "probe_model": model}
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

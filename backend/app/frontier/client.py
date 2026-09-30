"""Klien HTTP DeepSeek (format OpenAI chat completions), terpisah dari klien Ollama.

Kontrak yang diverifikasi dari dokumen resmi (28 Sep 2026):
- https://api-docs.deepseek.com/guides/thinking_mode/ : `thinking: {"type": "enabled"}` + `reasoning_effort`
  (low/high/max). temperature/presence_penalty/frequency_penalty diabaikan di thinking mode, jadi tidak dikirim.
- https://api-docs.deepseek.com/guides/json_mode/ : `response_format: {"type": "json_object"}`, prompt wajib
  memuat kata "json" dan contoh bentuknya; API sesekali mengembalikan content kosong.
- https://api-docs.deepseek.com/api/create-chat-completion : finish_reason stop/length/content_filter/
  tool_calls/insufficient_system_resource/aborted; usage prompt_cache_hit_tokens/prompt_cache_miss_tokens,
  completion_tokens_details.reasoning_tokens.
- https://api-docs.deepseek.com/quick_start/error_codes : 400/401/402/422/429/500/503.

Hanya `message.content` (jawaban akhir) yang dikembalikan. `reasoning_content` tidak pernah disimpan,
digabung ke JSON, atau ditampilkan.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import httpx

SECRET_PATTERNS = (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"), re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"))


def redact(text: object, secrets: tuple[str, ...] = ()) -> str:
    """Buang key dari teks apa pun sebelum dicatat, disimpan, atau dikirim ke UI."""
    value = str(text)
    for secret in secrets:
        if secret and len(secret) >= 4:
            value = value.replace(secret, "[disensor]")
    for pattern in SECRET_PATTERNS:
        value = pattern.sub("[disensor]", value)
    return value


@dataclass
class FrontierCallError(Exception):
    """Kegagalan satu percobaan. `billed`: "no" (ditolak sebelum diproses), "yes" (usage diketahui),
    atau "unknown" (provider mungkin sudah memproses, mis. timeout setelah request terkirim)."""

    kind: str
    message: str
    transient: bool
    billed: str = "unknown"
    status_code: int | None = None
    usage: dict | None = None
    finish_reason: str | None = None
    request_id: str | None = None
    # connect | pool | write | read: fase tempat request gagal. connect/pool = request belum terkirim.
    phase: str | None = None

    @property
    def sent(self) -> bool:
        return self.phase not in {"connect", "pool"}

    def __str__(self) -> str:
        return f"{self.kind}: {self.message}"


@dataclass
class RawCompletion:
    content: str
    finish_reason: str | None
    usage: dict | None
    request_id: str | None
    model: str | None
    seconds: float
    extra: dict = field(default_factory=dict)


def _usage(body: dict) -> dict | None:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    details = usage.get("completion_tokens_details") or {}
    return {"prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens"),
            "prompt_cache_miss_tokens": usage.get("prompt_cache_miss_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens") if isinstance(details, dict) else None}


HTTP_ERRORS = {
    400: ("bad_request", False, "no"),
    401: ("auth_failed", False, "no"),
    402: ("insufficient_balance", False, "no"),
    422: ("invalid_parameters", False, "no"),
    429: ("rate_limited", True, "no"),
    500: ("server_error", True, "unknown"),
    503: ("server_overloaded", True, "unknown"),
}


class DeepSeekClient:
    provider = "deepseek"

    def __init__(self, api_key: str, *, base_url: str = "https://api.deepseek.com", model: str = "deepseek-flash",
                 reasoning_effort: str = "high", max_tokens: int = 8192, timeout: float = 180.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.connect_timeout = min(15.0, timeout)
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=self.connect_timeout), transport=transport)

    @property
    def host(self) -> str:
        return httpx.URL(self.base_url).host or self.base_url

    def __repr__(self) -> str:  # jangan pernah memuat key
        return f"DeepSeekClient(model={self.model!r}, base_url={self.base_url!r})"

    @property
    def secrets(self) -> tuple[str, ...]:
        return (self._api_key,)

    def request_body(self, system: str, user: str) -> dict:
        return {"model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "thinking": {"type": "enabled"}, "reasoning_effort": self.reasoning_effort,
                "response_format": {"type": "json_object"}, "max_tokens": self.max_tokens, "stream": False}

    def close(self) -> None:
        self._client.close()

    def account(self) -> dict:
        """Cek key TANPA generasi berbayar: `GET /user/balance` dan `GET /models` (endpoint metadata,
        https://api-docs.deepseek.com/api/get-user-balance dan /api/list-models). Tidak memanggil chat."""
        result = {"key_valid": None, "balance": None, "models": [], "model_available": None, "model_name": None,
                  "error": None}
        if not self._api_key:
            return {**result, "key_valid": False, "error": "DEEPSEEK_API_KEY kosong."}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            response = self._client.get(f"{self.base_url}/user/balance", headers=headers, timeout=15.0)
            if response.status_code == 401:
                return {**result, "key_valid": False, "error": "Key ditolak DeepSeek (HTTP 401)."}
            response.raise_for_status()
            data = response.json()
            result["key_valid"] = True
            result["balance"] = {
                "is_available": data.get("is_available"),
                "balances": [{key: info.get(key) for key in ("currency", "total_balance", "granted_balance",
                                                             "topped_up_balance")}
                             for info in data.get("balance_infos") or [] if isinstance(info, dict)]}
            response = self._client.get(f"{self.base_url}/models", headers=headers, timeout=15.0)
            response.raise_for_status()
            models = [item for item in (response.json().get("data") or []) if isinstance(item, dict)]
            result["models"] = [str(item.get("id")) for item in models]
            result["model_available"] = self.model in result["models"]
            result["model_name"] = next((item.get("name") for item in models if item.get("id") == self.model), None)
        except httpx.ConnectTimeout:
            result["error"] = f"Cek akun gagal: tidak tersambung ke {self.host} dalam 15 detik (fase koneksi)."
        except (httpx.HTTPError, ValueError, AttributeError, TypeError) as error:
            result["error"] = redact(f"Cek akun gagal: {type(error).__name__}", self.secrets)
        return result

    def complete(self, system: str, user: str) -> RawCompletion:
        if not self._api_key:
            raise FrontierCallError("config", "DEEPSEEK_API_KEY kosong.", transient=False, billed="no")
        started = time.monotonic()
        try:
            response = self._client.post(f"{self.base_url}/chat/completions", json=self.request_body(system, user),
                                         headers={"Authorization": f"Bearer {self._api_key}",
                                                  "Content-Type": "application/json"})
        except httpx.ConnectTimeout:
            # Batas koneksi (TCP+TLS) 15 detik, bukan batas jawaban 180 detik: request belum terkirim.
            raise FrontierCallError("connect_timeout", f"Tidak tersambung ke {self.host} dalam "
                                    f"{self.connect_timeout:.0f} detik (fase koneksi); request belum terkirim. "
                                    "Periksa jaringan, VPN, atau firewall.", transient=True, billed="no",
                                    phase="connect") from None
        except httpx.ConnectError:
            # Koneksi tidak terbentuk: request belum sampai ke provider.
            raise FrontierCallError("network", f"Tidak bisa terhubung ke {self.host} (fase koneksi); request belum "
                                    "terkirim.", transient=True, billed="no", phase="connect") from None
        except httpx.PoolTimeout:
            raise FrontierCallError("pool_timeout", "Antrean koneksi lokal penuh; request belum terkirim.",
                                    transient=True, billed="no", phase="pool") from None
        except httpx.WriteTimeout:
            raise FrontierCallError("write_timeout", f"Pengiriman request ke {self.host} terhenti (fase kirim); "
                                    "provider mungkin sudah menerima sebagian.", transient=True, billed="unknown",
                                    phase="write") from None
        except httpx.TimeoutException:
            # Request sudah terkirim dan mungkin sedang diproses; usage tidak diketahui, jadi biaya tidak dianggap nol.
            raise FrontierCallError("timeout", f"Request terkirim tetapi tidak ada jawaban dalam {self.timeout:.0f} "
                                    "detik (fase baca).", transient=True, billed="unknown", phase="read") from None
        except httpx.HTTPError as error:
            raise FrontierCallError("network", redact(f"Transport gagal: {type(error).__name__}", self.secrets),
                                    transient=True, billed="unknown") from None
        seconds = round(time.monotonic() - started, 2)
        if response.status_code != 200:
            kind, transient, billed = HTTP_ERRORS.get(
                response.status_code, ("server_error" if response.status_code >= 500 else "http_error",
                                       response.status_code >= 500, "unknown"))
            detail = ""
            try:
                payload = response.json()
                detail = str(((payload or {}).get("error") or {}).get("message") or "")[:200]
            except (ValueError, AttributeError):
                detail = ""
            raise FrontierCallError(kind, redact(f"HTTP {response.status_code} {detail}".strip(), self.secrets),
                                    transient=transient, billed=billed, status_code=response.status_code)
        try:
            body = response.json()
            choice = body["choices"][0]
            message = choice.get("message") or {}
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise FrontierCallError("invalid_response", "Badan respons bukan chat completion yang sah.",
                                    transient=True, billed="unknown") from None
        usage = _usage(body)
        finish = choice.get("finish_reason")
        request_id = body.get("id") if isinstance(body.get("id"), str) else None
        content = message.get("content")
        billed = "yes" if usage else "unknown"
        if finish == "length":
            raise FrontierCallError("truncated", "Jawaban terpotong batas max_tokens (termasuk token penalaran).",
                                    transient=False, billed=billed, usage=usage, finish_reason=finish,
                                    request_id=request_id)
        if finish in {"insufficient_system_resource", "aborted"}:
            raise FrontierCallError("server_error", f"Generasi berhenti: {finish}.", transient=True, billed=billed,
                                    usage=usage, finish_reason=finish, request_id=request_id)
        if finish == "content_filter":
            raise FrontierCallError("content_filter", "Jawaban diblokir filter konten provider.", transient=False,
                                    billed=billed, usage=usage, finish_reason=finish, request_id=request_id)
        if not isinstance(content, str) or not content.strip():
            # Dokumen JSON mode: API kadang mengembalikan content kosong.
            raise FrontierCallError("empty", "Jawaban akhir kosong.", transient=True, billed=billed, usage=usage,
                                    finish_reason=finish, request_id=request_id)
        return RawCompletion(content=content, finish_reason=finish, usage=usage, request_id=request_id,
                             model=body.get("model") if isinstance(body.get("model"), str) else None,
                             seconds=seconds)


def parse_json_object(content: str) -> dict:
    """JSON dari jawaban akhir saja. Pagar kode ```json dibuang; selain itu harus objek JSON utuh."""
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("JSON bukan objek")
    return value

from __future__ import annotations

import json

import httpx
from pydantic import BaseModel

# Ruang keluaran default. Graph laporan memakai nilai lebih kecil lewat `num_predict` supaya konteks
# kecil tetap menyisakan ruang input; mengubah Modelfile saja tidak mengalahkan parameter request ini.
OUTPUT_TOKEN_BUDGET = 2048
CHARS_PER_TOKEN = 3


class AgentError(RuntimeError):
    pass


def inline_schema(schema: dict) -> dict:
    definitions = schema.get("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(definitions[node["$ref"].rsplit("/", 1)[-1]])
            return {key: resolve(value) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


class OllamaAgent:
    def __init__(
        self,
        base_url: str,
        model: str,
        num_ctx: int = 16384,
        timeout: float = 600.0,
        think: bool | None = None,
        client: httpx.Client | None = None,
        keep_alive: str | int = "5m",
        num_predict: int = OUTPUT_TOKEN_BUDGET,
        auth_token: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.think = think
        self.digest = "unknown"
        self.keep_alive = keep_alive
        # Otentikasi opsional untuk klien Ollama. Redirect tidak diikuti: endpoint yang mengalihkan ke alamat
        # lain (mis. halaman login atau alamat internal) diperlakukan sebagai gagal, bukan diikuti diam-diam.
        headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else None
        self.client = client or httpx.Client(timeout=timeout, headers=headers, follow_redirects=False)

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    @property
    def max_prompt_chars(self) -> int:
        return (self.num_ctx - self.num_predict) * CHARS_PER_TOKEN

    def check_ready(self) -> None:
        try:
            response = self.client.get(f"{self.base_url}/api/tags", timeout=5.0)
            if response.status_code in (401, 403):
                raise AgentError(f"Gateway Ollama di {self.base_url} menolak token (HTTP {response.status_code}); "
                                 "periksa konfigurasi layanan Ollama lokal.")
            response.raise_for_status()
            models = response.json().get("models", [])
            installed = {model["name"] for model in models}
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            raise AgentError(f"Ollama tidak bisa dihubungi di {self.base_url}; buka aplikasi Ollama.") from None
        if self.model not in installed and f"{self.model}:latest" not in installed:
            raise AgentError(f"Model {self.model} belum diunduh; jalankan `ollama pull {self.model}`.")
        self.digest = next((item.get("digest", "unknown") for item in models
                            if item["name"] in {self.model, f"{self.model}:latest"}), "unknown")

    def close(self) -> None:
        self.client.close()

    def unload(self) -> bool:
        try:
            response = self.client.post(f"{self.base_url}/api/generate",
                                        json={"model": self.model, "keep_alive": 0}, timeout=30.0)
            response.raise_for_status()
        except httpx.HTTPError:
            return False
        return True

    def run(self, prompt: str, schema: type[BaseModel]) -> BaseModel:
        if len(prompt) > self.max_prompt_chars:
            raise AgentError(
                f"{self.name}: prompt {len(prompt)} karakter melebihi batas konteks {self.max_prompt_chars}."
            )
        payload = {
            "model": self.model,
            "stream": True,
            "keep_alive": self.keep_alive,
            "format": inline_schema(schema.model_json_schema()),
            "options": {"temperature": 0, "num_ctx": self.num_ctx, "num_predict": self.num_predict},
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.think is not None:
            payload["think"] = self.think
        try:
            # Streaming, bukan cuma dirakit ulang di sini: tunnel/proxy (mis. Cloudflare quick tunnel)
            # memutus koneksi yang diam tanpa byte mengalir selama puluhan detik menunggu model 14B
            # selesai, walau permintaannya sendiri belum benar-benar timeout.
            content, done_reason, completed = [], None, False
            with self.client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if not isinstance(chunk, dict):
                        raise ValueError("baris stream Ollama bukan objek JSON")
                    if chunk.get("error"):
                        raise ValueError("Ollama mengembalikan error di tengah stream")
                    piece = (chunk.get("message") or {}).get("content") or ""
                    if not isinstance(piece, str):
                        raise ValueError("isi stream Ollama bukan teks")
                    content.append(piece)
                    if chunk.get("done"):
                        done_reason = chunk.get("done_reason")
                        completed = True
            if not completed:
                raise ValueError("stream Ollama berakhir tanpa penanda selesai")
            body = {"done_reason": done_reason, "message": {"content": "".join(content)}}
        except httpx.TimeoutException:
            raise AgentError(f"{self.name}: timeout menunggu model.") from None
        except (httpx.HTTPError, ValueError):
            raise AgentError(f"{self.name}: permintaan ke Ollama gagal.") from None
        if body.get("done_reason") == "length":
            raise AgentError(f"{self.name}: output terpotong batas token.")
        try:
            return schema.model_validate_json(body["message"]["content"])
        except (ValueError, KeyError, TypeError):
            raise AgentError(f"{self.name}: output tidak sesuai schema.") from None

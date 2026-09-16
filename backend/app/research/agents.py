from __future__ import annotations

import httpx
from pydantic import BaseModel

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
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.think = think
        self.digest = "unknown"
        self.keep_alive = keep_alive
        self.client = client or httpx.Client(timeout=timeout)

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    @property
    def max_prompt_chars(self) -> int:
        return (self.num_ctx - OUTPUT_TOKEN_BUDGET) * CHARS_PER_TOKEN

    def check_ready(self) -> None:
        try:
            response = self.client.get(f"{self.base_url}/api/tags", timeout=5.0)
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
            "stream": False,
            "keep_alive": self.keep_alive,
            "format": inline_schema(schema.model_json_schema()),
            "options": {"temperature": 0, "num_ctx": self.num_ctx, "num_predict": OUTPUT_TOKEN_BUDGET},
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.think is not None:
            payload["think"] = self.think
        try:
            response = self.client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException:
            raise AgentError(f"{self.name}: timeout menunggu model.") from None
        except (httpx.HTTPError, ValueError):
            raise AgentError(f"{self.name}: permintaan ke Ollama gagal.") from None
        if not isinstance(body, dict):
            raise AgentError(f"{self.name}: respons Ollama bukan objek JSON.")
        if body.get("done_reason") == "length":
            raise AgentError(f"{self.name}: output terpotong batas token.")
        try:
            return schema.model_validate_json(body["message"]["content"])
        except (ValueError, KeyError, TypeError):
            raise AgentError(f"{self.name}: output tidak sesuai schema.") from None

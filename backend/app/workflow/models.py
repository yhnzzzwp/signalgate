"""Pemanggilan model lokal untuk graph laporan.

Satu model aktif pada satu waktu: model sebelumnya dilepas sebelum peran berikutnya memuat model
lain, sesuai batas unified memory Mac. Kegagalan model tidak pernah menghentikan laporan; ia menjadi
keterbatasan yang tercatat, dan klaim yang bergantung padanya tidak pernah naik menjadi terverifikasi.
"""
from __future__ import annotations

import time

from app.config import Settings
from app.llm.factory import THINKING_MODEL_PREFIXES
from app.research.agents import AgentError, OllamaAgent

ANALYST, REVIEWER = "analyst", "reviewer"
MAX_ATTEMPTS = 2


def workflow_agent(settings: Settings, model_name: str) -> OllamaAgent:
    return OllamaAgent(settings.ollama_base_url, model_name, settings.workflow_num_ctx,
                       settings.ollama_timeout_seconds,
                       think=False if model_name.startswith(THINKING_MODEL_PREFIXES) else None,
                       keep_alive=settings.ollama_keep_alive,
                       num_predict=settings.workflow_num_predict,
                       auth_token=settings.ollama_auth_token.get_secret_value())


def resolve_reviewers(settings: Settings) -> list[str]:
    """Pembanding lokal berurutan untuk graph laporan.

    Urutan sumber: WORKFLOW_REVIEWER_MODELS (daftar) -> WORKFLOW_REVIEWER_MODEL (format lama, tepat satu)
    -> OLLAMA_REVIEWER_MODELS (profil workstation: glm4:9b lalu gemma3:12b) -> OLLAMA_VALIDATOR_MODEL.
    Model yang sama dengan analis dibuang: pembanding ada untuk membaca secara independen.
    """
    if settings.llm_backend != "ollama":
        return []
    analyst = settings.workflow_analyst_model or settings.ollama_model
    if settings.workflow_reviewer_models:
        names = list(settings.workflow_reviewer_models)
    elif settings.workflow_reviewer_model:
        names = [settings.workflow_reviewer_model]
    elif settings.ollama_reviewer_models:
        names = list(settings.ollama_reviewer_models)
    else:
        names = [settings.ollama_validator_model] if settings.ollama_validator_model else []
    return [name for name in dict.fromkeys(names) if name and name != analyst][:3]


def resolve_models(settings: Settings) -> tuple[str | None, str | None]:
    """Analis dan pembanding PERTAMA (kompatibilitas pemanggil lama). `None` berarti peran tidak tersedia."""
    if settings.llm_backend != "ollama":
        return None, None
    reviewers = resolve_reviewers(settings)
    return settings.workflow_analyst_model or settings.ollama_model, (reviewers[0] if reviewers else None)


def reviewer_roles_for(count: int) -> list[str]:
    """Satu pembanding tetap berperan "reviewer" (sama dengan data run lama); lebih dari satu diberi nomor."""
    return [REVIEWER] if count <= 1 else [f"{REVIEWER}_{index}" for index in range(1, count + 1)]


class ModelPool:
    def __init__(self, settings: Settings, factory=workflow_agent) -> None:
        self.settings = settings
        self.factory = factory
        analyst = resolve_models(settings)[0]
        reviewers = resolve_reviewers(settings)
        self._reviewer_roles = reviewer_roles_for(len(reviewers)) if reviewers else [REVIEWER]
        self.names: dict[str, str | None] = {ANALYST: analyst}
        for role, name in zip(self._reviewer_roles, reviewers or [None]):
            self.names[role] = name
        self.agents: dict[str, OllamaAgent] = {}
        self.unavailable: dict[str, str] = {}
        self.loaded: str | None = None
        self.runs: list[dict] = []

    def reviewer_roles(self) -> list[str]:
        return list(self._reviewer_roles)

    def name(self, role: str) -> str | None:
        if role == REVIEWER and role not in self.names:
            return self.names.get(self._reviewer_roles[0])  # pembanding pertama, untuk pemanggil lama
        return self.names.get(role)

    def reason(self, role: str) -> str:
        if self.settings.llm_backend != "ollama":
            return f"LLM_BACKEND={self.settings.llm_backend}; tidak ada model yang dipanggil."
        if not self.name(role):
            return f"Model untuk peran {role} tidak dikonfigurasi."
        return self.unavailable.get(role, "")

    def _agent(self, role: str) -> OllamaAgent | None:
        name = self.name(role)
        if not name or role in self.unavailable:
            return None
        if role not in self.agents:
            shared = next((agent for other, agent in self.agents.items() if agent.model == name), None)
            if shared is not None:
                self.agents[role] = shared
                return shared
            agent = self.factory(self.settings, name)
            try:
                agent.check_ready()
            except AgentError as error:
                self.unavailable[role] = str(error)
                agent.close()
                return None
            self.agents[role] = agent
        return self.agents[role]

    def _switch(self, role: str, agent: OllamaAgent) -> None:
        if self.loaded in (None, role):
            self.loaded = role
            return
        previous = self.agents.get(self.loaded)
        if previous is not None and self.settings.ollama_offload_between_models and previous.model != agent.model:
            self.runs.append({"role": self.loaded, "model": previous.name, "seconds": 0.0, "prompt_chars": 0,
                              "ok": previous.unload(), "error": None, "attempt": 1, "offload": True})
        self.loaded = role

    def budget(self, role: str) -> int:
        agent = self._agent(role)
        return agent.max_prompt_chars if agent else 0

    def call(self, role: str, text: str, schema):
        """(hasil, error). Retry sekali: gangguan transport dan JSON tidak valid sama-sama layak dicoba ulang."""
        agent = self._agent(role)
        if agent is None:
            return None, self.reason(role) or f"Peran {role} tidak tersedia."
        self._switch(role, agent)
        last = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = time.monotonic()
            try:
                result = agent.run(text, schema)
            except AgentError as error:
                last = str(error)
                self.runs.append({"role": role, "model": agent.name, "digest": agent.digest,
                                  "seconds": round(time.monotonic() - started, 2), "prompt_chars": len(text),
                                  "ok": False, "error": last, "attempt": attempt})
                continue
            self.runs.append({"role": role, "model": agent.name, "digest": agent.digest,
                              "seconds": round(time.monotonic() - started, 2), "prompt_chars": len(text),
                              "ok": True, "error": None, "attempt": attempt})
            return result, None
        return None, last

    def close(self) -> None:
        loaded = self.agents.get(self.loaded) if self.loaded else None
        if loaded is not None and self.settings.ollama_offload_between_models:
            loaded.unload()
        for agent in {id(agent): agent for agent in self.agents.values()}.values():
            agent.close()
        self.agents.clear()

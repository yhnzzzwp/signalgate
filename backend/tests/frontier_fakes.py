"""Klien frontier palsu dan helper untuk test. Tidak ada jaringan; semua jawaban ditentukan test."""
from __future__ import annotations

import json
import threading

from app.config import Settings
from app.frontier.client import FrontierCallError, RawCompletion
from app.frontier.service import FrontierService

FAKE_KEY = "sk-testrahasia1234567890abcdef"
USAGE = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, "prompt_cache_hit_tokens": 0,
         "prompt_cache_miss_tokens": 100, "reasoning_tokens": 20}


def completion(payload, usage=USAGE, finish="stop") -> RawCompletion:
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return RawCompletion(content=content, finish_reason=finish, usage=dict(usage) if usage else None,
                         request_id="req-test", model="deepseek-flash", seconds=0.01)


def verdict(claim_id="news:model:1", status="supported", evidence=("news:a",), reason="Kutipan di news:a mendukung."):
    return {"claim_id": claim_id, "status": status, "evidence_ids": list(evidence), "reason": reason}


def review_json(*verdicts, timeline=()):
    return {"evidence_reading": ["Sumber news:a menyebut angka yang sama."], "verdicts": list(verdicts),
            "timeline": list(timeline), "limitations": []}


class ScriptedClient:
    """`outcomes`: RawCompletion, FrontierCallError, atau fungsi(user_prompt) -> salah satunya."""

    model = "deepseek-flash"

    def __init__(self, outcomes=(), gate: threading.Event | None = None, entered: threading.Event | None = None):
        self.outcomes = list(outcomes)
        self.prompts: list[str] = []
        self.gate, self.entered = gate, entered
        self.closed = False

    def complete(self, system, user):
        self.prompts.append(user)
        if self.entered is not None:
            self.entered.set()
        if self.gate is not None:
            self.gate.wait(10)
        if not self.outcomes:
            raise AssertionError("ScriptedClient dipanggil lebih banyak dari yang dijadwalkan")
        outcome = self.outcomes.pop(0)
        if callable(outcome) and not isinstance(outcome, FrontierCallError):
            outcome = outcome(user)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def account(self):
        raise AssertionError("account() tidak dijadwalkan")

    def close(self):
        self.closed = True


class NoCallClient(ScriptedClient):
    def complete(self, system, user):
        raise AssertionError("frontier tidak boleh memanggil API di skenario ini")


def frontier_settings(tmp_path, **overrides) -> Settings:
    values = dict(frontier_enabled=True, deepseek_api_key=FAKE_KEY, frontier_directory=tmp_path / "frontier",
                  frontier_max_retries=1, frontier_mode="shadow", research_cases_dir=tmp_path / "cases",
                  runtime_directory=tmp_path / "runtime")
    values.update(overrides)
    return Settings(_env_file=None, **values)


def service(settings: Settings, client) -> FrontierService:
    return FrontierService(settings, client=client, sleep=lambda _seconds: None)


def timeout_error():
    return FrontierCallError("timeout", "Tidak ada jawaban.", transient=True, billed="unknown")


CLAIMS = [{"claim_id": "news:model:1", "pernyataan": "TEST menerbitkan 868 juta saham baru."}]
PACKET = {"bukti": {"artikel": [{"id": "news:a", "isi": "TEST menerbitkan 868 juta saham baru."}]}}
VALID = {"news:a"}
TRIGGERS = [{"claim_id": "news:model:1", "reason": "reviewer_conflict"}]
AGREE = {"news:model:1": [{"reviewer": "reviewer_1", "status": "supported"}]}
SPLIT = {"news:model:1": [{"reviewer": "reviewer_1", "status": "supported"},
                          {"reviewer": "reviewer_2", "status": "contradicted"}]}


def run_review(frontier: FrontierService, *, opinions=AGREE, offline=False, run_key="run-1", packet=PACKET,
               claims=CLAIMS, triggers=TRIGGERS, valid=VALID) -> dict:
    return frontier.review(run_key=run_key, scope="workflow", packet=packet, claims=claims, valid_ids=valid,
                           local_opinions=opinions, triggers=triggers, offline=offline)

from __future__ import annotations

from app.llm.base import LLMProvider
from app.pipeline.schema import CandidateEvent, CompanySnapshot, Verdict


def reason_about_event(provider: LLMProvider, event: CandidateEvent, snapshot: CompanySnapshot | None) -> Verdict:
    return provider.reason(event, snapshot)

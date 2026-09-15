from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ActionBucket(StrEnum):
    control_change = "control_change"
    non_preemptive_capital = "non_preemptive_capital"
    rights_issue = "rights_issue"
    general_action = "general_action"


class CandidateEvent(BaseModel):
    ticker: str
    headline: str
    body: str
    source_url: str
    published_at: str
    bucket: ActionBucket
    matched_keywords: list[str]
    sector: str | None = None
    sub_sector: list[str] = Field(default_factory=list)


class CompanySnapshot(BaseModel):
    ticker: str
    company_name: str
    business_description: str | None = None
    market_cap: int | None = None
    major_shareholders: list[dict] = Field(default_factory=list)
    pb_ratio: float | None = None
    pe_ratio: float | None = None
    intrinsic_value: float | None = None
    last_close_price: float | None = None


class VerdictLabel(StrEnum):
    growth_catalyst = "growth_catalyst"
    structural_red_flag = "structural_red_flag"
    inconclusive = "inconclusive"


class Verdict(BaseModel):
    label: VerdictLabel
    confidence: float = Field(ge=0.0, le=1.0)
    red_flag_signals: list[str] = Field(default_factory=list)
    growth_signals: list[str] = Field(default_factory=list)
    rationale_bullets: list[str] = Field(default_factory=list)
    provider: str


class GateStatus(StrEnum):
    passed = "passed"
    needs_review = "needs_review"


class GateResult(BaseModel):
    status: GateStatus
    rejected_terms: list[str] = Field(default_factory=list)


class ScreenedEvent(BaseModel):
    event: CandidateEvent
    snapshot: CompanySnapshot | None
    verdict: Verdict
    gate: GateResult
    watch_status: str = "active"

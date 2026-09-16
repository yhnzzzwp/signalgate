from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.pipeline.schema import Verdict

Relation = Literal["existing_shareholder", "affiliate", "new_party", "creditor", "public", "unknown"]
FundsCategory = Literal["core_expansion", "new_business", "debt_repayment", "acquisition", "working_capital", "unknown"]
ActionType = Literal[
    "rights_issue", "private_placement", "acquisition", "divestment", "control_change", "debt_conversion", "other"
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Quoted(StrictModel):
    evidence_id: str
    quote: str = Field(max_length=800)


class Counterparty(Quoted):
    name: str = Field(max_length=200)
    relation: Relation


class FundsUse(Quoted):
    category: FundsCategory


class Flag(Quoted):
    present: bool


class Extraction(StrictModel):
    action_type: ActionType
    counterparties: list[Counterparty] = Field(max_length=4)
    use_of_funds: list[FundsUse] = Field(max_length=4)
    business_change: Flag
    old_business_divested: Flag
    asset_injection: Flag


class FactCheck(StrictModel):
    fact_id: str
    status: Literal["supported", "not_supported", "contradicted"]
    reason: str = Field(max_length=500)


class Validation(StrictModel):
    checks: list[FactCheck] = Field(max_length=12)
    agrees_with_label: bool
    issues: list[str] = Field(max_length=6)


class Fact(StrictModel):
    id: str
    topic: str
    value: str
    claim: str
    quote: str
    evidence_id: str
    validator_status: Literal["unchecked", "supported", "not_supported"] = "unchecked"


class Evidence(StrictModel):
    id: str
    kind: Literal["sectors_api", "scrapling", "pdf", "input"]
    url: str
    retrieved_at: str
    title: str
    text: str
    sha256: str
    links: list[str]
    page_number: int | None = None
    document_sha256: str | None = None


class ResearchOutcome(BaseModel):
    verdict: Verdict
    status: str
    case_id: str | None = None
    cached: bool = False
    cached_from_case_id: str | None = None
    generated_at: str = ""
    evidence_checked_at: str = ""
    confidence_kind: str = "heuristic_not_probability"
    model_versions: dict[str, str] = Field(default_factory=dict)
    document_status: str = "not_required"
    review_rounds: int = 0
    model_runs: list[dict] = Field(default_factory=list)
    extraction_attempts: int = 0
    facts: list[Fact] = Field(default_factory=list)
    signals: list[dict] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)

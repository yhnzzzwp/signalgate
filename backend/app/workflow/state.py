"""Kontrak state laporan empat panel.

State graph disimpan sebagai dict JSON biasa agar checkpoint SQLite bisa dibaca ulang lintas versi
kode; model Pydantic di bawah dipakai untuk memvalidasi di batas setiap node, bukan disimpan apa
adanya. Percakapan model tidak pernah menjadi state: yang disimpan adalah sumber, metrik, dan klaim.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "workflow-2026-09-18.1"
DOMAINS: tuple[str, ...] = ("fundamental", "valuation", "technical", "news")
MAX_REPAIRS = 1

Domain = Literal["fundamental", "valuation", "technical", "news"]
PanelStatus = Literal["completed", "insufficient_data", "needs_review", "failed"]
ClaimKind = Literal["observation", "calculation", "interpretation"]
ValidationStatus = Literal["pending", "supported", "unsupported", "contradicted"]
# Siapa yang menyatakan: fakta dokumen berbeda dari pernyataan manajemen, analis, atau rumor.
Attribution = Literal["data", "document", "company_statement", "analyst", "unattributed"]
Horizon = Literal["short", "medium", "long"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowInput(Strict):
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    ticker: str = Field(pattern=r"^[A-Z0-9]{4}$")
    horizon: Horizon = "medium"
    as_of: date

    @field_validator("ticker", mode="before")
    @classmethod
    def upper(cls, value: str) -> str:
        return str(value).strip().upper().removesuffix(".JK")


class Source(Strict):
    """Satu snapshot mentah yang benar-benar diambil. Klaim hanya boleh merujuk ID yang ada di sini."""

    source_id: str
    kind: Literal["sectors_company_report", "sectors_quarterly", "sectors_daily", "sectors_corporate_actions",
                  "sectors_news", "signalgate_screening"]
    endpoint: str
    params: dict = Field(default_factory=dict)
    ticker: str
    fetched_at: str
    # Kapan informasi tersedia untuk publik, bila diketahui. None = tidak diketahui, bukan "selalu".
    available_at: str | None = None
    period: str | None = None
    sha256: str | None = None
    path: str | None = None
    status: Literal["ok", "empty", "error", "excluded"] = "ok"
    error: str | None = None
    credits: int = 0
    # live: diambil untuk run ini; replay: dipakai ulang dari run lain, tanggal ambil aslinya dipertahankan.
    mode: Literal["live", "replay", "fixture"] = "live"


class Metric(Strict):
    metric_id: str
    domain: Domain
    name: str
    value: float | None
    unit: str
    formula: str
    period: str
    source_ids: list[str] = Field(default_factory=list)
    input_metric_ids: list[str] = Field(default_factory=list)
    price_basis: str | None = None
    status: Literal["ok", "insufficient_data", "not_meaningful"] = "ok"
    note: str | None = None


class Claim(Strict):
    claim_id: str
    domain: Domain
    kind: ClaimKind
    statement: str = Field(min_length=1, max_length=600)
    attribution: Attribution = "data"
    metric_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    quote: str | None = None
    period: str | None = None
    supports_claim_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    validation_status: ValidationStatus = "pending"
    # Gabungan dua daftar di bawah; dipisah supaya pemeriksaan ulang kode tidak menghapus catatan pembanding.
    validation_notes: list[str] = Field(default_factory=list)
    mechanical_issues: list[str] = Field(default_factory=list)
    review_notes: list[str] = Field(default_factory=list)
    version: int = 1
    # Dibuat oleh kode (template/metrik) atau model. Klaim kode tetap melewati pemeriksaan mekanis.
    author: str = "code"


class Panel(Strict):
    domain: Domain
    status: PanelStatus
    headline: str = ""
    metrics: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)


class ModelRun(Strict):
    role: str
    model: str
    digest: str = "unknown"
    seconds: float
    prompt_chars: int
    ok: bool
    error: str | None = None
    attempt: int = 1


class SynthesisSection(Strict):
    text: str
    claim_ids: list[str]


class GraphState(TypedDict, total=False):
    """State LangGraph. Semua nilai JSON; divalidasi ulang dengan model di atas ketika dibaca.

    Setiap kunci yang dipakai lintas node WAJIB terdaftar di sini: LangGraph hanya meneruskan channel
    yang dikenal, jadi kunci yang lupa didaftarkan hilang diam-diam di perbatasan node.
    """

    request: dict
    mode: str
    plan: dict
    run_dir: str
    sources: dict[str, dict]
    # Kartu screening aksi korporasi terakhir (dibaca dari database), dipakai panel berita.
    screening: dict | None
    metrics: dict[str, dict]
    panels: dict[str, dict]
    events: list[dict]
    # Aksi korporasi sampai tanggal acuan, dan kualitas/pemotongan seri harga.
    corporate_actions: list[dict]
    series: dict
    validation: dict
    reviewer_notes: dict
    repair_count: int
    repaired_claim_ids: list[str]
    synthesis: dict
    gate: dict
    report: dict
    published: dict
    model_runs: list[dict]
    credits_used: int
    node_timings: list[dict]

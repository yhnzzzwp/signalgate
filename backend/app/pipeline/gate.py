from __future__ import annotations

import re

from app.pipeline.schema import GateResult, GateStatus, Verdict

DENYLIST_TERMS = [
    "buy", "sell", "beli", "jual",
    "recommend", "rekomendasi",
    "target price", "harga target",
    "strong buy", "strong sell",
    "accumulate", "akumulasi",
    "take profit", "cut loss",
    "entry price", "harga masuk",
]

_TERM_PATTERNS = [re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE) for term in DENYLIST_TERMS]


def _scan_text(text: str) -> list[str]:
    hits = []
    for term, pattern in zip(DENYLIST_TERMS, _TERM_PATTERNS):
        if pattern.search(text):
            hits.append(term)
    return hits


def apply_gate(verdict: Verdict) -> GateResult:
    combined_text = " ".join(verdict.rationale_bullets + verdict.red_flag_signals + verdict.growth_signals)
    rejected_terms = _scan_text(combined_text)
    if rejected_terms:
        return GateResult(status=GateStatus.needs_review, rejected_terms=rejected_terms)
    return GateResult(status=GateStatus.passed, rejected_terms=[])


def sanitize_for_display(verdict: Verdict, gate: GateResult) -> Verdict:
    if gate.status == GateStatus.passed:
        return verdict
    return verdict.model_copy(
        update={
            "rationale_bullets": ["Hasil memerlukan pemeriksaan manual; bukti, validasi, atau bahasa analisis belum lolos pemeriksaan."],
            "red_flag_signals": [],
            "growth_signals": [],
        }
    )

from __future__ import annotations

from app.pipeline.schema import ActionBucket, CandidateEvent, CompanySnapshot, Verdict

EXTREME_PBV_THRESHOLD = 20.0
PBV_CLAIM_MARKERS = ["pbv", "p/b", "price to book", "price-to-book"]
CONTROL_CLAIM_MARKERS = ["pengendali", "control", "kendali"]


class ValidationIssue(str):
    pass


def validate_verdict(event: CandidateEvent, snapshot: CompanySnapshot | None, verdict: Verdict) -> list[str]:
    issues: list[str] = []
    combined_text = " ".join(verdict.rationale_bullets + verdict.red_flag_signals).lower()

    mentions_pbv = any(marker in combined_text for marker in PBV_CLAIM_MARKERS)
    if mentions_pbv:
        if snapshot is None or snapshot.pb_ratio is None:
            issues.append("Verdict menyebut PBV tapi snapshot tidak punya data PBV.")
        elif snapshot.pb_ratio < EXTREME_PBV_THRESHOLD and "ekstrem" in combined_text:
            issues.append(
                f"Verdict menyebut PBV ekstrem, tapi angka aktual ({snapshot.pb_ratio:.1f}x) di bawah ambang "
                f"{EXTREME_PBV_THRESHOLD:.0f}x."
            )

    mentions_control_change = any(marker in combined_text for marker in CONTROL_CLAIM_MARKERS)
    if mentions_control_change and event.bucket not in (
        ActionBucket.control_change,
        ActionBucket.non_preemptive_capital,
    ):
        issues.append(
            f"Verdict menyebut isu pengendali/kendali, tapi event ini terklasifikasi bucket {event.bucket.value}."
        )

    return issues

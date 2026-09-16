from __future__ import annotations

from app.pipeline.schema import VerdictLabel

STALE_AFTER_DAYS = 30


def next_watch_status(
    current_status: str,
    label: VerdictLabel,
    days_since_screened: int,
    latest_price_change_pct: float | None,
) -> str:
    if current_status != "active":
        return current_status

    if days_since_screened >= STALE_AFTER_DAYS:
        return "stale"

    # Price movement does not verify a corporate-action thesis. Resolution needs
    # separately reviewed follow-up disclosures; until then only age changes state.
    return "active"

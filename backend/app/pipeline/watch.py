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

    if label == VerdictLabel.structural_red_flag and latest_price_change_pct is not None:
        if abs(latest_price_change_pct) < 2.0:
            return "resolved_redflag"

    if label == VerdictLabel.growth_catalyst and latest_price_change_pct is not None:
        if latest_price_change_pct > 0:
            return "resolved_growth"

    return "active"

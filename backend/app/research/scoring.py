from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.pipeline.schema import ActionBucket, VerdictLabel
from app.research.models import Fact

EXTREME_PBV = 20.0
RED_FLAG_THRESHOLD = 4
GROWTH_THRESHOLD = 3

# A thin float means new shares concentrate control instead of spreading it, so the two market rules
# below only fire on the buckets that actually issue shares or raise cash.
FREE_FLOAT_THIN = 0.15
FUNDRAISING_BUCKETS = frozenset({ActionBucket.rights_issue, ActionBucket.non_preemptive_capital})
CASH_BURN_WINDOW = 4
CASH_BURN_NEGATIVE_QUARTERS = 3

Side = Literal["red", "growth"]

FACT_RULES: dict[tuple[str, str], tuple[str, Side, int, str]] = {
    ("business_change", "present"): ("business_change", "red", 3, "Ada pergantian atau penambahan bidang usaha baru"),
    ("old_business_divested", "present"): ("old_business", "red", 2, "Bisnis atau anak usaha lama dilepas"),
    ("asset_injection", "present"): (
        "asset_injection", "red", 2, "Ada penyuntikan aset, inbreng, atau pengambilalihan aset dari pihak terkait"
    ),
    ("counterparty", "new_party"): ("counterparty_new", "red", 2, "Penerima saham atau dana adalah pihak baru"),
    ("counterparty", "existing_shareholder"): (
        "counterparty_known", "growth", 2, "Penerima saham atau dana adalah pemegang saham lama atau afiliasinya"
    ),
    ("counterparty", "affiliate"): (
        "counterparty_known", "growth", 2, "Penerima saham atau dana adalah pemegang saham lama atau afiliasinya"
    ),
    ("counterparty", "public"): ("counterparty_public", "growth", 1, "Saham baru ditawarkan ke seluruh pemegang saham"),
    ("use_of_funds", "core_expansion"): ("funds_core", "growth", 2, "Dana untuk ekspansi bisnis inti"),
    ("use_of_funds", "working_capital"): ("funds_working", "growth", 1, "Dana untuk modal kerja"),
    ("use_of_funds", "new_business"): ("funds_new", "red", 1, "Dana untuk bisnis baru di luar bisnis inti"),
}


@dataclass(frozen=True)
class Signal:
    side: Side
    weight: int
    reason: str
    source: str
    code: str = ""


def fact_signals(facts: list[Fact]) -> list[Signal]:
    signals: list[Signal] = []
    seen_groups: set[str] = set()
    for fact in facts:
        rule = FACT_RULES.get((fact.topic, fact.value))
        if rule is None or rule[0] in seen_groups:
            continue
        group, side, weight, reason = rule
        seen_groups.add(group)
        detail = f"{reason}: {fact.claim}" if fact.claim else reason
        signals.append(Signal(side, weight, f"{detail} [{fact.evidence_id}]", fact.id, group))
    return signals


def sectors_signals(pb_ratio: float | None) -> list[Signal]:
    if pb_ratio is not None and pb_ratio > EXTREME_PBV:
        return [Signal("red", 1, f"PBV {pb_ratio:.1f}x di atas {EXTREME_PBV:.0f}x, tidak cukup sendirian", "sectors")]
    return []


@dataclass(frozen=True)
class MarketContext:
    """Deterministic Sectors data attached to one event. Never produced by a model."""

    pb_ratio: float | None = None
    bucket: ActionBucket | None = None
    free_float: float | None = None
    free_float_rank: int | None = None
    free_float_universe: int | None = None
    quarters: tuple[dict, ...] = field(default_factory=tuple)

    @classmethod
    def from_snapshot(cls, snapshot, pb_ratio: float | None, bucket: ActionBucket | None) -> "MarketContext":
        if snapshot is None:
            return cls(pb_ratio=pb_ratio, bucket=bucket)
        return cls(
            pb_ratio=pb_ratio,
            bucket=bucket,
            free_float=snapshot.free_float,
            free_float_rank=snapshot.free_float_rank,
            free_float_universe=snapshot.free_float_universe,
            quarters=tuple(snapshot.quarterly_financials or ()),
        )

    def raises_capital(self) -> bool:
        return self.bucket in FUNDRAISING_BUCKETS


def _series(quarters: tuple[dict, ...], key: str, window: int = CASH_BURN_WINDOW) -> list[float]:
    """Newest-first values for `key`, only while every quarter in the window reports a number."""
    values = [row.get(key) for row in quarters[:window]]
    if len(values) < window or not all(isinstance(value, (int, float)) for value in values):
        return []
    return [float(value) for value in values]


def float_risk_signals(context: MarketContext) -> list[Signal]:
    """Thin float plus a capital raise: dilution that concentrates control rather than spreading it."""
    free_float = context.free_float
    if free_float is None or free_float >= FREE_FLOAT_THIN or not context.raises_capital():
        return []
    reason = f"Free float {free_float:.1%} di bawah {FREE_FLOAT_THIN:.0%}"
    if context.free_float_rank and context.free_float_universe:
        reason += f" (tertipis ke-{context.free_float_rank} dari {context.free_float_universe} emiten subsektor)"
    reason += "; penambahan modal memperbesar konsentrasi kendali"
    return [Signal("red", 1, reason, "sectors:free-float", "float_risk")]


def cash_burn_signals(context: MarketContext) -> list[Signal]:
    """Raising money while operations keep consuming it is the pattern worth flagging, not a weak quarter."""
    if not context.raises_capital():
        return []
    values = _series(context.quarters, "operating_cash_flow")
    negatives = [value for value in values if value < 0]
    if len(negatives) < CASH_BURN_NEGATIVE_QUARTERS:
        return []
    return [Signal("red", 1,
                   f"Arus kas operasi negatif pada {len(negatives)} dari {len(values)} kuartal terakhir "
                   f"sementara emiten menggalang dana", "sectors:quarterly", "cash_burn")]


def contradiction_signals(signals: list[Signal], context: MarketContext) -> list[Signal]:
    """The stated use of funds has to survive contact with the reported numbers."""
    if not any(signal.code == "funds_core" for signal in signals):
        return []
    # Quarterly revenue is seasonal, so only an unbroken decline across the whole window counts.
    values = _series(context.quarters, "revenue")
    if not values or any(newer >= older for newer, older in zip(values, values[1:])):
        return []
    return [Signal("red", 1,
                   f"Klaim ekspansi bisnis inti tidak tercermin pada pendapatan: turun beruntun "
                   f"{len(values)} kuartal terakhir", "sectors:quarterly", "expansion_contradiction")]


def market_signals(context: MarketContext) -> list[Signal]:
    return sectors_signals(context.pb_ratio) + float_risk_signals(context) + cash_burn_signals(context)


def score_signals(context: MarketContext, facts: list[Fact]) -> list[Signal]:
    """The one place signals are assembled, so drafts, reviews and the published score always agree."""
    signals = market_signals(context) + fact_signals(facts)
    return signals + contradiction_signals(signals, context)


def decide(signals: list[Signal]) -> tuple[VerdictLabel, float]:
    red = sum(signal.weight for signal in signals if signal.side == "red")
    growth = sum(signal.weight for signal in signals if signal.side == "growth")
    if red >= RED_FLAG_THRESHOLD and red >= growth + 2:
        return VerdictLabel.structural_red_flag, round(min(0.9, 0.4 + 0.1 * (red - growth)), 2)
    has_core_expansion = any(signal.code == "funds_core" for signal in signals)
    if growth >= GROWTH_THRESHOLD and red <= 1 and has_core_expansion:
        return VerdictLabel.growth_catalyst, round(min(0.9, 0.4 + 0.1 * (growth - red)), 2)
    return VerdictLabel.inconclusive, 0.0

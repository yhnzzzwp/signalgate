from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.pipeline.schema import ActionBucket, VerdictLabel
from app.research.models import Fact

# Ambang mutlak untuk seluruh pasar; hanya dipakai ketika pembanding subsektor tidak tersedia.
# PBV wajar sangat berbeda antar subsektor: bank 2026 ada di 0,8x, jadi 20x tidak pernah menyala
# untuk emiten perbankan semahal apa pun relatif terhadap sebayanya.
EXTREME_PBV = 20.0
PB_PEER_MULTIPLE = 3.0
RED_FLAG_THRESHOLD = 4
GROWTH_THRESHOLD = 3

# A thin float means new shares concentrate control instead of spreading it, so the two market rules
# below only fire on the buckets that actually issue shares or raise cash.
FREE_FLOAT_THIN = 0.15
FUNDRAISING_BUCKETS = frozenset({ActionBucket.rights_issue, ActionBucket.non_preemptive_capital})
CASH_BURN_WINDOW = 4
CASH_BURN_NEGATIVE_QUARTERS = 3

# Penjualan kumulatif oleh pengendali/insider yang dianggap berarti, dalam persen modal disetor.
# Angka awal, belum dikalibrasi terhadap data berlabel manusia -- itu pekerjaan Tahap 8.
CONTROLLER_EXIT_MIN_PERCENT = 1.0
# Kategori penggunaan dana yang membangun bisnisnya. Di luar ini, dana tidak menambah kemampuan
# emiten menghasilkan kas.
BUILDING_FUND_USES = frozenset({"core_expansion", "working_capital", "acquisition"})

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


def sectors_signals(pb_ratio: float | None, subsector_pb: float | None = None,
                   subsector_slug: str | None = None) -> list[Signal]:
    """Valuasi dibandingkan terhadap subsektornya sendiri; ambang mutlak hanya cadangan."""
    if pb_ratio is None:
        return []
    if subsector_pb:
        multiple = pb_ratio / subsector_pb
        if multiple > PB_PEER_MULTIPLE:
            peer = f" ({subsector_slug})" if subsector_slug else ""
            return [Signal("red", 1,
                           f"PBV {pb_ratio:.2f}x setara {multiple:.1f}x median subsektor{peer} "
                           f"{subsector_pb:.2f}x, tidak cukup sendirian", "sectors:subsector", "valuation_gap")]
        return []
    if pb_ratio > EXTREME_PBV:
        return [Signal("red", 1, f"PBV {pb_ratio:.1f}x di atas {EXTREME_PBV:.0f}x, tidak cukup sendirian",
                       "sectors", "valuation_gap")]
    return []


@dataclass(frozen=True)
class MarketContext:
    """Deterministic Sectors data attached to one event. Never produced by a model."""

    pb_ratio: float | None = None
    bucket: ActionBucket | None = None
    subsector_pb: float | None = None
    subsector_slug: str | None = None
    free_float: float | None = None
    free_float_rank: int | None = None
    free_float_universe: int | None = None
    quarters: tuple[dict, ...] = field(default_factory=tuple)
    insider_sales: tuple[dict, ...] = field(default_factory=tuple)

    @classmethod
    def from_snapshot(cls, snapshot, pb_ratio: float | None, bucket: ActionBucket | None) -> "MarketContext":
        if snapshot is None:
            return cls(pb_ratio=pb_ratio, bucket=bucket)
        return cls(
            pb_ratio=pb_ratio,
            bucket=bucket,
            subsector_pb=snapshot.subsector_pb,
            subsector_slug=snapshot.subsector_slug,
            free_float=snapshot.free_float,
            free_float_rank=snapshot.free_float_rank,
            free_float_universe=snapshot.free_float_universe,
            quarters=tuple(snapshot.quarterly_financials or ()),
            insider_sales=tuple(snapshot.insider_sales or ()),
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


def debt_only_signals(facts: list[Fact]) -> list[Signal]:
    """Rights issue yang dananya hanya menutup utang, tanpa satu pun penggunaan yang membangun bisnis.

    `debt_repayment` sudah lama diekstrak model tetapi tidak punya aturan skor, jadi pola ini lolos
    tanpa sinyal. Melunasi utang dengan ekuitas baru memindahkan risiko dari kreditur ke pemegang
    saham publik tanpa menambah kemampuan emiten menghasilkan kas. Yang dinilai eksklusivitasnya:
    melunasi utang sambil berekspansi adalah cerita yang berbeda.
    """
    uses = {fact.value for fact in facts if fact.topic == "use_of_funds"}
    if "debt_repayment" not in uses or uses & BUILDING_FUND_USES:
        return []
    return [Signal("red", 2, "Seluruh dana yang terverifikasi hanya untuk melunasi utang, tanpa "
                             "penggunaan yang menambah kemampuan menghasilkan kas",
                   "fact:use_of_funds", "funds_debt_only")]


def controller_exit_signals(context: MarketContext) -> list[Signal]:
    """Pengendali menjual saat publik diminta menyerap saham baru.

    Pola exit liquidity: pihak yang paling tahu kondisi emiten mengurangi posisinya justru ketika
    dana publik masuk. Dihitung dari laporan keterbukaan kepemilikan, bukan dari teks berita.
    """
    if not context.raises_capital():
        return []
    sold = sum(
        row.get("share_percentage_transaction") or 0
        for row in context.insider_sales
        if row.get("transaction_type") == "sell"
        and row.get("holder_type") in {"insider", "corporate-investor"}
    )
    if sold < CONTROLLER_EXIT_MIN_PERCENT:
        return []
    return [Signal("red", 2, f"Pengendali atau insider melepas {sold:.2f}% saham menjelang "
                             f"penggalangan dana dari publik", "sectors:filings", "controller_exit")]


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
    return (sectors_signals(context.pb_ratio, context.subsector_pb, context.subsector_slug)
            + float_risk_signals(context) + cash_burn_signals(context)
            + controller_exit_signals(context))


BUCKET_TEXT = {
    ActionBucket.control_change: "perubahan pengendali",
    ActionBucket.non_preemptive_capital: "penambahan modal tanpa HMETD",
    ActionBucket.rights_issue: "rights issue",
    ActionBucket.general_action: "aksi korporasi",
}

LABEL_TEXT = {
    VerdictLabel.growth_catalyst: "polanya sejalan dengan katalis pertumbuhan",
    VerdictLabel.structural_red_flag: "polanya cocok dengan red flag struktural",
    VerdictLabel.inconclusive: "belum cukup untuk disimpulkan dan perlu diperiksa manusia",
}


def _clause(signals: list[Signal], side: Side) -> str:
    """Alasan tanpa penanda bukti, digabung jadi satu frasa yang enak dibaca."""
    reasons = [signal.reason.split(" [")[0].rstrip(".").lower() for signal in signals if signal.side == side]
    unique = list(dict.fromkeys(reasons))
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    return f"{', '.join(unique[:-1])}, dan {unique[-1]}"


def compose_summary(ticker: str, bucket: ActionBucket | None, signals: list[Signal],
                    label: VerdictLabel) -> str:
    """Satu paragraf yang menyatakan apa yang ditemukan dan apa artinya, tanpa bahasa transaksi.

    Dirangkai Python dari sinyal yang sudah terverifikasi, bukan diminta ke model: model tidak
    pernah menentukan label, jadi ia juga tidak boleh menulis kalimat yang menyimpulkannya.
    """
    action = BUCKET_TEXT.get(bucket, "aksi korporasi")
    parts = [f"{ticker} mengumumkan {action}."]

    reds, growths = _clause(signals, "red"), _clause(signals, "growth")
    if reds and growths:
        parts.append(f"Dari sumbernya terverifikasi {growths}; di sisi lain {reds}.")
    elif reds:
        parts.append(f"Dari sumbernya terverifikasi {reds}.")
    elif growths:
        parts.append(f"Dari sumbernya terverifikasi {growths}.")
    else:
        parts.append("Belum ada fakta yang bisa diverifikasi terhadap dokumen sumbernya.")

    parts.append(f"Hasil penyaringan: {LABEL_TEXT[label]}.")
    parts.append("Ini hasil screening atas aksi korporasinya, bukan penilaian atas sahamnya.")
    return " ".join(parts)


def score_signals(context: MarketContext, facts: list[Fact]) -> list[Signal]:
    """The one place signals are assembled, so drafts, reviews and the published score always agree."""
    signals = market_signals(context) + fact_signals(facts) + debt_only_signals(facts)
    return signals + contradiction_signals(signals, context)


def is_fact_derived(signal: Signal) -> bool:
    """Sinyal yang berasal dari dokumen sumber, bukan dari data pasar."""
    return not signal.source.startswith("sectors")


def decide(signals: list[Signal]) -> tuple[VerdictLabel, float]:
    # Tanpa satu pun fakta terverifikasi dari dokumen sumber, tidak ada label yang boleh terbit.
    # Dulu invarian ini hanya bertahan karena kebetulan: seluruh sinyal data pasar berbobot 1 dan
    # jumlahnya kurang dari ambang. Begitu satu aturan berbobot 2 ditambahkan, data pasar saja
    # sudah cukup mencapai ambang merah. Sekarang syaratnya struktural, bukan aritmetika.
    if not any(is_fact_derived(signal) for signal in signals):
        return VerdictLabel.inconclusive, 0.0
    red = sum(signal.weight for signal in signals if signal.side == "red")
    growth = sum(signal.weight for signal in signals if signal.side == "growth")
    if red >= RED_FLAG_THRESHOLD and red >= growth + 2:
        return VerdictLabel.structural_red_flag, round(min(0.9, 0.4 + 0.1 * (red - growth)), 2)
    has_core_expansion = any(signal.code == "funds_core" for signal in signals)
    if growth >= GROWTH_THRESHOLD and red <= 1 and has_core_expansion:
        return VerdictLabel.growth_catalyst, round(min(0.9, 0.4 + 0.1 * (growth - red)), 2)
    return VerdictLabel.inconclusive, 0.0

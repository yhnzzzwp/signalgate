from __future__ import annotations

import re

from app.pipeline.schema import GateResult, GateStatus, Verdict

# Bahasa transaksi: menyuruh orang bertindak di pasar.
TRANSACTION_TERMS = [
    "buy", "sell", "beli", "jual",
    "recommend", "rekomendasi",
    "target price", "harga target",
    "strong buy", "strong sell",
    "accumulate", "akumulasi",
    "take profit", "cut loss",
    "entry price", "harga masuk",
]

# Penilaian nilai: tidak menyuruh membeli, tetapi tetap menyatakan sahamnya bagus atau buruk.
# Screening menilai aksi korporasinya, bukan sahamnya, jadi kalimat seperti ini tidak boleh terbit.
# Kata bersayap seperti "murah" atau "mahal" sengaja tidak masuk: keduanya muncul wajar dalam
# kutipan fakta dan akan menahan hasil yang sah.
VALUE_JUDGEMENT_TERMS = [
    "layak beli", "layak dikoleksi", "layak investasi", "layak dibeli",
    "saham bagus", "saham baik", "saham menarik",
    "prospek cerah", "prospek bagus", "prospek menarik",
    "undervalued", "overvalued",
    "potensi cuan", "cuan", "peluang emas", "jangan lewatkan",
    "worth buying", "should buy", "good buy", "must buy",
    "berpotensi naik", "berpotensi turun", "diprediksi naik", "diprediksi turun",
]

DENYLIST_TERMS = [*TRANSACTION_TERMS, *VALUE_JUDGEMENT_TERMS]

# Dihasilkan mesin, bukan prosa: nama model atau nilai enum tidak pernah menjadi saran.
MACHINE_FIELDS = frozenset({"label", "confidence", "provider"})

_TERM_PATTERNS = [re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE) for term in DENYLIST_TERMS]


def _scan_text(text: str) -> list[str]:
    hits = []
    for term, pattern in zip(DENYLIST_TERMS, _TERM_PATTERNS):
        if pattern.search(text):
            hits.append(term)
    return hits


def prose_fields(verdict: Verdict) -> list[str]:
    """Setiap field prosa pada Verdict, ditemukan lewat refleksi.

    Sebelumnya gate hanya memindai tiga field yang ditulis eksplisit, sehingga field baru apa pun
    akan lolos tanpa pernah diperiksa. Menemukannya sendiri membuat field baru ikut terjaga
    secara bawaan, bukan karena seseorang ingat memperbarui daftarnya.
    """
    return [name for name in type(verdict).model_fields if name not in MACHINE_FIELDS]


def _texts(verdict: Verdict) -> list[str]:
    texts: list[str] = []
    for name in prose_fields(verdict):
        value = getattr(verdict, name, None)
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, (list, tuple)):
            texts.extend(item for item in value if isinstance(item, str))
    return texts


def apply_gate(verdict: Verdict) -> GateResult:
    rejected_terms = _scan_text(" ".join(_texts(verdict)))
    if rejected_terms:
        return GateResult(status=GateStatus.needs_review, rejected_terms=sorted(set(rejected_terms)))
    return GateResult(status=GateStatus.passed, rejected_terms=[])


def sanitize_for_display(verdict: Verdict, gate: GateResult) -> Verdict:
    if gate.status == GateStatus.passed:
        return verdict
    # Kosongkan setiap field prosa, bukan hanya tiga yang diketahui, agar field baru tidak
    # menjadi jalan keluar bagi teks yang gagal pemeriksaan.
    updates: dict[str, object] = {}
    for name in prose_fields(verdict):
        value = getattr(verdict, name, None)
        if isinstance(value, str):
            updates[name] = ""
        elif isinstance(value, list):
            updates[name] = []
    updates["rationale_bullets"] = [
        "Hasil memerlukan pemeriksaan manual; bukti, validasi, atau bahasa analisis belum lolos pemeriksaan."
    ]
    return verdict.model_copy(update=updates)

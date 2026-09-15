from __future__ import annotations

from typing import Protocol

from app.pipeline.schema import CandidateEvent, CompanySnapshot, Verdict

REASONING_RUBRIC = """
Kamu menilai satu aksi korporat emiten IDX. Klasifikasikan sebagai salah satu dari:
- growth_catalyst: pendanaan/aksi korporat yang mendukung bisnis inti yang sudah ada, oleh
  pemegang saham/pihak yang punya rekam jejak jelas, dengan tren keuangan yang mendukung.
- structural_red_flag: pola konsisten dengan asset injection/backdoor listing — pergantian
  bidang usaha, pengendali baru yang tidak jelas rekam jejaknya, basis ekuitas pasca-transaksi
  yang kecil dibanding lonjakan valuasi pasar, atau penerima dana/aset yang tidak transparan.
- inconclusive: bukti tidak cukup untuk menyimpulkan salah satu di atas.

Pertimbangkan: apakah ada perubahan bidang usaha? Siapa pengendali/pihak yang menerima dana —
kredibel dan transparan, atau baru/anonim? Bagaimana basis ekuitas dibanding lonjakan valuasi
pasar (PBV ekstrem = tanda peringatan)? Bagaimana tren keuangan historisnya?

WAJIB: jangan pernah menyebut kata "beli", "jual", "buy", "sell", "target price", atau bentuk
rekomendasi transaksi apa pun. Ini murni analisis pola, bukan rekomendasi investasi.
"""


class LLMProvider(Protocol):
    name: str

    def reason(self, event: CandidateEvent, snapshot: CompanySnapshot | None) -> Verdict: ...


VERDICT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": ["growth_catalyst", "structural_red_flag", "inconclusive"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "red_flag_signals": {"type": "array", "items": {"type": "string"}},
        "growth_signals": {"type": "array", "items": {"type": "string"}},
        "rationale_bullets": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["label", "confidence", "red_flag_signals", "growth_signals", "rationale_bullets"],
}


def build_prompt(event: CandidateEvent, snapshot: CompanySnapshot | None) -> str:
    snapshot_block = "Tidak ada data snapshot perusahaan tersedia."
    if snapshot is not None:
        snapshot_block = (
            f"Nama perusahaan: {snapshot.company_name}\n"
            f"Sektor/bidang usaha (menurut data terkini): {snapshot.business_description}\n"
            f"Market cap: {snapshot.market_cap}\n"
            f"PBV: {snapshot.pb_ratio}\n"
            f"PE: {snapshot.pe_ratio}\n"
            f"Pemegang saham utama: {snapshot.major_shareholders}\n"
        )

    return (
        f"{REASONING_RUBRIC}\n\n"
        f"=== Aksi Korporat ===\n"
        f"Ticker: {event.ticker}\n"
        f"Judul: {event.headline}\n"
        f"Isi: {event.body}\n"
        f"Kategori aksi (bucket kasar): {event.bucket.value}\n\n"
        f"=== Snapshot Perusahaan ===\n"
        f"{snapshot_block}\n\n"
        f"Jawab HANYA dalam JSON sesuai schema yang diberikan, tanpa teks lain."
    )

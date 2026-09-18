"""Prompt, schema keluaran, dan paket bukti per peran.

Paket dibentuk agar muat di konteks model: bila tidak cukup, jumlah item dikurangi dan pengurangannya
dilaporkan sebagai keterbatasan. Bukti tidak pernah dipotong diam-diam. Model tidak pernah diberi
kesimpulan peran lain kecuali memang dirancang begitu (reviewer membaca bukti dulu, baru klaim).
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.workflow.evidence import display

PROMPT_VERSION = "workflow-prompt-2026-09-18.1"

RULES = (
    "Kamu bagian dari alat riset saham Indonesia. Aturan yang tidak bisa dilanggar:\n"
    "1. Jawab HANYA JSON sesuai schema, tanpa penjelasan di luar JSON.\n"
    "2. Jangan menghitung dan jangan mengarang angka. Angka hanya boleh muncul bila sama persis "
    "dengan nilai metrik yang kamu rujuk di `metric_ids`.\n"
    "3. Setiap pernyataan wajib merujuk metrik atau sumber yang diberikan. ID yang tidak ada di data "
    "dianggap salah dan klaimnya dibuang.\n"
    "4. Jangan menulis rekomendasi beli/jual, target harga, nilai wajar, atau penilaian bahwa saham "
    "bagus/buruk. Tugasmu menjelaskan data, bukan menyuruh bertindak.\n"
    "5. Bila data tidak cukup, katakan kekurangannya di `limitations` dan jangan menebak.\n"
)

RESEARCH_INSTRUCTION = (
    "Jelaskan kondisi fundamental dan valuasi emiten dari metrik yang sudah dihitung kode.\n"
    "- `fundamental`: pertumbuhan, profitabilitas, arus kas, dan risiko neraca.\n"
    "- `valuation`: bagaimana penilaian pasar dibanding data bisnis dan pembanding.\n"
    "Tiap klaim: satu kalimat, sebutkan periodenya, dan rujuk metrik yang mendasarinya. Bedakan fakta "
    "dari tafsiran, dan sebut keterbatasan basis data (misalnya periode berbeda atau peer sedikit)."
)

NEWS_INSTRUCTION = (
    "Hubungkan berita ke emiten dan aksi korporasi yang tepat.\n"
    "- Satu entri per peristiwa, bukan per artikel. Artikel yang menceritakan peristiwa sama digabung.\n"
    "- `quote` wajib potongan kata demi kata dari judul atau isi artikel yang kamu rujuk, minimal 12 karakter.\n"
    "- `attribution`: `document` bila mengutip dokumen resmi, `company_statement` bila pernyataan pihak "
    "perusahaan, `analyst` bila pendapat analis, `unattributed` bila tidak jelas sumbernya.\n"
    "- `event_date` format YYYY-MM-DD bila tertulis; kosongkan bila tidak disebut. Jangan menebak tanggal."
)

REVIEW_NOTES_INSTRUCTION = (
    "Kamu pembaca independen. Baca bukti berikut dan tulis pengamatanmu sendiri SEBELUM melihat "
    "kesimpulan analis. Sebutkan hal yang menurutmu penting dan hal yang meragukan atau kurang bukti."
)

REVIEW_VERDICT_INSTRUCTION = (
    "Bandingkan setiap klaim analis dengan bukti dan catatan independenmu.\n"
    "- `supported`: bukti yang dirujuk memang mendukung isi klaim.\n"
    "- `unsupported`: bukti tidak cukup untuk menyimpulkan itu.\n"
    "- `contradicted`: bukti menunjukkan yang sebaliknya.\n"
    "Beda periode atau beda dimensi bukan kontradiksi: fundamental membaik dan harga melemah bisa "
    "sama-sama benar. Nilai isi klaimnya, bukan gaya bahasanya."
)

REPAIR_INSTRUCTION = (
    "Perbaiki hanya klaim yang gagal di bawah. Untuk setiap klaim: tulis ulang agar sesuai bukti, atau "
    "set `drop` true bila memang tidak bisa didukung bukti yang ada. Jangan menambah klaim baru."
)

SYNTHESIS_INSTRUCTION = (
    "Susun ringkasan lintas dimensi HANYA dari klaim yang sudah terverifikasi di bawah.\n"
    "- Tiap bagian merujuk `claim_ids` yang dipakai.\n"
    "- Pertahankan konflik dan keterbatasan apa adanya; jangan mendamaikannya dengan menebak.\n"
    "- Jangan menambah angka atau fakta baru, dan jangan menyimpulkan tindakan bagi pembaca."
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaimDraft(Strict):
    statement: str = Field(min_length=10, max_length=300)
    metric_ids: list[str] = Field(default_factory=list, max_length=4)
    limitations: list[str] = Field(default_factory=list, max_length=2)


class ResearchOutput(Strict):
    fundamental: list[ClaimDraft] = Field(default_factory=list, max_length=4)
    valuation: list[ClaimDraft] = Field(default_factory=list, max_length=4)


class NewsEvent(Strict):
    event_type: Literal["rights_issue", "private_placement", "acquisition", "divestment", "control_change",
                        "dividend", "operational", "legal", "other"]
    event_date: str = Field(default="", max_length=10)
    statement: str = Field(min_length=10, max_length=300)
    quote: str = Field(default="", max_length=300)
    attribution: Literal["document", "company_statement", "analyst", "unattributed"]
    source_ids: list[str] = Field(default_factory=list, max_length=3)


class NewsOutput(Strict):
    events: list[NewsEvent] = Field(default_factory=list, max_length=6)


class ReviewNote(Strict):
    statement: str = Field(min_length=5, max_length=300)
    metric_ids: list[str] = Field(default_factory=list, max_length=4)
    source_ids: list[str] = Field(default_factory=list, max_length=3)


class ReviewNotes(Strict):
    observations: list[ReviewNote] = Field(default_factory=list, max_length=6)
    concerns: list[str] = Field(default_factory=list, max_length=4)


class ClaimVerdict(Strict):
    claim_id: str = Field(max_length=120)
    status: Literal["supported", "unsupported", "contradicted"]
    reason: str = Field(default="", max_length=300)


class ReviewVerdicts(Strict):
    verdicts: list[ClaimVerdict] = Field(default_factory=list, max_length=12)


class RepairedClaim(Strict):
    claim_id: str = Field(max_length=120)
    statement: str = Field(default="", max_length=300)
    metric_ids: list[str] = Field(default_factory=list, max_length=4)
    source_ids: list[str] = Field(default_factory=list, max_length=3)
    quote: str = Field(default="", max_length=300)
    drop: bool = False


class RepairOutput(Strict):
    claims: list[RepairedClaim] = Field(default_factory=list, max_length=8)


class SynthesisSection(Strict):
    text: str = Field(min_length=20, max_length=400)
    claim_ids: list[str] = Field(default_factory=list, max_length=6)


class SynthesisOutput(Strict):
    sections: list[SynthesisSection] = Field(default_factory=list, max_length=4)


def prompt(instruction: str, packet: dict) -> str:
    return f"{RULES}\n{instruction}\n\nDATA JSON:\n{json.dumps(packet, ensure_ascii=False)}"


def fit(items: list, render, max_chars: int) -> tuple[list, int]:
    """Ambil item selama masih muat. Yang tidak terpakai dikembalikan sebagai jumlah, bukan dibuang diam-diam."""
    kept, used = [], 0
    for item in items:
        rendered = render(item)
        size = len(json.dumps(rendered, ensure_ascii=False)) + 1
        if used + size > max_chars:
            break
        kept.append(rendered)
        used += size
    return kept, len(items) - len(kept)


def metric_view(metric: dict) -> dict:
    view = {"id": metric["metric_id"], "nama": metric["name"], "periode": metric["period"],
            "nilai": display(metric) if metric["status"] == "ok" else None, "status": metric["status"]}
    if metric.get("note"):
        view["catatan"] = metric["note"]
    return view


def article_view(source: dict, text: str, body_chars: int = 600) -> dict:
    title, _, body = text.partition("\n")
    return {"id": source["source_id"], "tanggal": source.get("available_at"), "judul": title.strip(),
            "isi": body.strip()[:body_chars]}


def claim_view(claim: dict) -> dict:
    view = {"claim_id": claim["claim_id"], "dimensi": claim["domain"], "pernyataan": claim["statement"],
            "metric_ids": claim.get("metric_ids") or [], "source_ids": claim.get("source_ids") or []}
    if claim.get("quote"):
        view["kutipan"] = claim["quote"]
    return view


def research_packet(company: dict, metrics: list[dict], limitations: list[str], budget: int) -> tuple[dict, int]:
    views, dropped = fit(metrics, metric_view, budget)
    return {"emiten": company, "metrik": views, "keterbatasan_data": limitations[:6],
            "pertanyaan": ["Apa yang berubah pada pertumbuhan, profitabilitas, dan arus kas?",
                           "Bagaimana penilaian pasar dibanding data bisnis dan pembanding?"]}, dropped


def news_packet(company: dict, articles: list[tuple[dict, str]], actions: list[dict], budget: int) -> tuple[dict, int]:
    views, dropped = fit(articles, lambda item: article_view(item[0], item[1]), budget)
    return {"emiten": company, "artikel": views,
            "aksi_korporasi_tercatat": [{"jenis": action["type"], "tanggal": action["date"]} for action in actions[:8]],
            "pertanyaan": ["Peristiwa apa yang benar-benar terjadi, kapan, dan siapa pihaknya?"]}, dropped

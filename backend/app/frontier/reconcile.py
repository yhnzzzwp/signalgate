"""Aturan rekonsiliasi antara pembanding lokal dan frontier. Semua keputusan di sini kode, bukan model.

Status dinormalisasi ke "supported" / "unsupported" / "contradicted" (jalur screening memakai
"not_supported" untuk "unsupported"; lihat `to_screening`).

Aturan untuk satu klaim/fakta yang dieskalasi (dicatat sebagai `rule` di audit):
- R0 `mechanical`: klaim gagal pemeriksaan mekanis (kutipan palsu, identitas salah, angka/perhitungan
  tidak valid, Compliance Gate). Frontier tidak pernah dibaca; status lokal tetap.
- R1 `frontier_missing`: frontier tidak memberi putusan sah (gagal, budget habis, cache miss offline,
  ID bukti tidak dikenal). Status lokal tetap -- kegagalan frontier tidak pernah menaikkan status.
- R2 `frontier_unstable`: putusan berubah antara pembacaan independen dan langkah kedua, atau langkah
  kedua diperlukan tapi tidak berjalan. Tidak dipakai; status lokal tetap.
- R3 `tie_break`: pembanding lokal berbeda pendapat dan putusan frontier (stabil) sama dengan salah satu
  pendapat lokal -> putusan itu dipakai. Frontier yang berbeda dari SEMUA pendapat lokal tidak dipakai.
- R4 `downgrade`: pembanding lokal sepakat tetapi frontier (stabil) lebih pesimistis -> turun ke
  "unsupported" (belum terverifikasi). Frontier tidak pernah menaikkan status yang disepakati lokal.
- R5 `agree`: frontier sepakat dengan status lokal; tidak ada perubahan.
"""
from __future__ import annotations

PESSIMISM = {"supported": 0, "pending": 1, "unsupported": 2, "contradicted": 3}


def normalize(status: str | None) -> str | None:
    if status is None:
        return None
    return "unsupported" if status == "not_supported" else status


def to_screening(status: str) -> str:
    return "not_supported" if status == "unsupported" else status


def merge_local(statuses: list[str | None], expected: int) -> str:
    """Penggabungan pesimistis pembanding lokal: satu bantahan menang, persetujuan harus lengkap."""
    present = [normalize(status) for status in statuses if status]
    if "contradicted" in present:
        return "contradicted"
    if "unsupported" in present:
        return "unsupported"
    if present and len(present) >= expected and all(status == "supported" for status in present):
        return "supported"
    return "pending"


def reviewer_conflict(statuses: list[str | None]) -> bool:
    present = {normalize(status) for status in statuses if status}
    return len(present) > 1


def frontier_view(verdict: dict | None) -> tuple[str | None, bool, str]:
    """(status frontier yang boleh dipakai, stabil?, alasan bila tidak)."""
    if not verdict or not verdict.get("independent"):
        return None, False, "frontier tidak memberi putusan sah"
    independent = verdict["independent"]["status"]
    if verdict.get("second_look_needed"):
        second = (verdict.get("second_look") or {}).get("status")
        if second is None:
            return independent, False, "langkah kedua diperlukan tetapi tidak berjalan"
        if second != independent:
            return independent, False, f"berubah dari {independent} menjadi {second} setelah melihat pendapat lokal"
    return independent, True, ""


def reconcile(*, local_statuses: list[str | None], expected_reviewers: int, verdict: dict | None,
              mechanical_ok: bool = True) -> dict:
    local = [normalize(status) for status in local_statuses]
    merged = merge_local(local, expected_reviewers)
    result = {"local_statuses": local, "local_merged": merged, "frontier_status": None, "final_status": merged,
              "rule": "", "changed": False, "note": ""}
    if not mechanical_ok:
        return {**result, "rule": "R0_mechanical",
                "note": "Gagal pemeriksaan mekanis; frontier tidak bisa menganulir."}
    status, stable, why = frontier_view(verdict)
    result["frontier_status"] = status
    if status is None:
        return {**result, "rule": "R1_frontier_missing", "note": why}
    if not stable:
        return {**result, "rule": "R2_frontier_unstable", "note": why}
    present = [item for item in local if item]
    if reviewer_conflict(present):
        if status in present:
            return {**result, "final_status": status, "rule": "R3_tie_break", "changed": status != merged,
                    "note": f"Pembanding lokal berbeda; frontier stabil sejalan dengan salah satunya ({status})."}
        return {**result, "rule": "R3_no_match",
                "note": "Frontier berbeda dari semua pembanding lokal; konflik tetap belum terselesaikan."}
    if PESSIMISM.get(status, 0) > PESSIMISM.get(merged, 0) and merged == "supported":
        return {**result, "final_status": "unsupported", "rule": "R4_downgrade", "changed": True,
                "note": f"Frontier stabil menilai {status}; klaim diturunkan ke belum terverifikasi."}
    if status == merged:
        return {**result, "rule": "R5_agree"}
    return {**result, "rule": "R5_keep_local",
            "note": "Frontier tidak pernah menaikkan status lokal; status lokal dipertahankan."}

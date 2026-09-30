"""One publication boundary shared by batch scans and single-case research."""
from app.pipeline.gate import apply_gate, sanitize_for_display
from app.pipeline.schema import GateResult, GateStatus, ScreenedEvent
from app.pipeline.validate import validate_verdict

# Kenapa hasil ditahan, per status riset. Kalimat ini membedakan kegagalan infrastruktur dari
# ketidaksepakatan analisis, supaya "inconclusive" tidak terbaca sebagai penilaian risiko.
HOLD_REASONS = {
    "needs_review": "Ditahan untuk pemeriksaan: pembacaan independen belum sepakat. Fakta yang lolos validasi "
                    "tetap ditampilkan; kesimpulannya belum.",
    "needs_document": "Ditahan: dokumen sumber belum dapat dibaca, jadi belum ada yang dinilai.",
    "insufficient_evidence": "Ditahan: belum ada fakta yang lolos verifikasi terhadap sumber.",
    "model_unavailable": "Ditahan: model tidak tersedia saat analisis. Ini kegagalan infrastruktur, bukan penilaian.",
    "missing_sectors_data": "Ditahan: data emiten dari Sectors tidak tersedia.",
    "deterministic_only": "Ditahan: model lokal tidak aktif; hanya sinyal data pasar yang dihitung.",
}


def screen_outcome(event, snapshot, outcome) -> ScreenedEvent:
    issues = validate_verdict(event, snapshot, outcome.verdict)
    gate = apply_gate(outcome.verdict)
    # Prosa dikosongkan hanya bila isinya sendiri yang gagal: bahasa transaksi atau angka yang tidak
    # cocok dengan data. Status riset yang belum selesai menahan kesimpulan, bukan fakta yang sudah lolos.
    verdict = sanitize_for_display(outcome.verdict, gate)
    if issues:
        verdict = sanitize_for_display(verdict, GateResult(status=GateStatus.needs_review))
    if issues or outcome.status != "completed":
        if not issues and gate.status == GateStatus.passed:
            reason = HOLD_REASONS.get(outcome.status, "Ditahan untuk pemeriksaan manual.")
            verdict = verdict.model_copy(update={"rationale_bullets": [reason, *verdict.rationale_bullets]})
        gate = gate.model_copy(update={"status": GateStatus.needs_review})
    return ScreenedEvent(event=event, snapshot=snapshot, verdict=verdict, gate=gate,
                         numeric_issues=issues,
                         research=outcome.model_dump(mode="json", exclude={"verdict"}))

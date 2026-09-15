from __future__ import annotations

from app.pipeline.schema import ActionBucket, CandidateEvent, CompanySnapshot, Verdict, VerdictLabel

EXTREME_PBV_THRESHOLD = 20.0


class MockProvider:
    name = "mock"

    def reason(self, event: CandidateEvent, snapshot: CompanySnapshot | None) -> Verdict:
        red_flags: list[str] = []
        growth: list[str] = []

        if event.bucket in (ActionBucket.control_change, ActionBucket.non_preemptive_capital):
            red_flags.append(
                f"Aksi korporat masuk kategori {event.bucket.value} — pola umum dipakai untuk perubahan kendali "
                "atau penambahan modal tanpa hak proporsional ke pemegang saham lama."
            )
        else:
            growth.append(f"Aksi korporat masuk kategori {event.bucket.value} — pola umum aksi korporat rutin.")

        if snapshot and snapshot.pb_ratio is not None and snapshot.pb_ratio > EXTREME_PBV_THRESHOLD:
            red_flags.append(
                f"PBV {snapshot.pb_ratio:.1f}x jauh di atas ambang wajar ({EXTREME_PBV_THRESHOLD:.0f}x) — "
                "indikasi basis ekuitas kecil dibanding valuasi pasar."
            )
        elif snapshot and snapshot.pb_ratio is not None:
            growth.append(f"PBV {snapshot.pb_ratio:.1f}x masih dalam kisaran wajar.")

        if snapshot and snapshot.major_shareholders:
            top_holder = snapshot.major_shareholders[0].get("name", "")
            if top_holder and top_holder.lower() not in {"public", "masyarakat"}:
                red_flags.append(
                    f"Pemegang saham utama ({top_holder}) memegang porsi besar setelah aksi korporat — "
                    "perlu verifikasi rekam jejak pihak ini."
                )

        if len(red_flags) >= 2:
            label = VerdictLabel.structural_red_flag
            confidence = 0.6
        elif len(growth) >= 2 and not red_flags:
            label = VerdictLabel.growth_catalyst
            confidence = 0.55
        else:
            label = VerdictLabel.inconclusive
            confidence = 0.4

        rationale = red_flags + growth or ["Data tidak cukup untuk menyusun rationale — heuristik mock provider."]

        return Verdict(
            label=label,
            confidence=confidence,
            red_flag_signals=red_flags,
            growth_signals=growth,
            rationale_bullets=rationale,
            provider=self.name,
        )

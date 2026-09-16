"""Siklus hidup rekomendasi BUY: kapan ditangguhkan, dicabut, atau kedaluwarsa.

Dipisahkan dari keputusannya. `catalog.DECISIONS` menjawab *apa kesimpulannya*; modul ini menjawab
*apakah kesimpulan itu masih berlaku*. Menyatukan keduanya membuat "sedang ditinjau" tampak seperti
penilaian atas saham, padahal itu keadaan sebuah rekomendasi yang sudah terbit.

Penerbitan BUY masih menunggu metode valuasi Tahap 6, tetapi mesin transisinya deterministik dan
tidak membutuhkan valuasi sama sekali — jadi diterapkan dan diuji sekarang. Menunda logika yang bisa
diuji sampai ada angka valuasi berarti menulisnya terburu-buru bersamaan dengan hal yang sulit.

Riwayat tidak pernah ditimpa: setiap perubahan menambah satu langkah beserta alasan dan waktunya,
sehingga rekomendasi lama tetap dapat dievaluasi benar atau salahnya.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum

from app import catalog


def _parse_moment(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


ACTIVE = "active"
SUSPENDED = "suspended"
REVOKED = "revoked"
EXPIRED = "expired"


class Trigger(StrEnum):
    """Peristiwa yang mengubah keadaan. Bukan keputusan manusia, melainkan fakta yang terjadi."""

    ACTION_POSTPONED = "action_postponed"
    ACTION_CANCELLED = "action_cancelled"
    REPORT_REVISED = "report_revised"
    MATERIAL_INFORMATION = "material_information"
    DATA_STALE = "data_stale"
    RED_FLAG_RAISED = "red_flag_raised"
    BASIS_FAILED = "basis_failed"
    REVIEW_CLEARED = "review_cleared"
    HORIZON_PASSED = "horizon_passed"


# (keadaan sekarang, pemicu) -> (keadaan berikutnya, alasan yang dicatat)
#
# Ditunda bukan dibatalkan: penundaan menangguhkan karena dasarnya mungkin masih utuh, pembatalan
# mencabut karena dasarnya hilang. Membedakan keduanya adalah inti dari tabel ini.
TRANSITIONS: dict[tuple[str, Trigger], tuple[str, str]] = {
    (ACTIVE, Trigger.ACTION_POSTPONED): (SUSPENDED, "aksi korporasi yang menjadi dasar ditunda"),
    (ACTIVE, Trigger.REPORT_REVISED): (SUSPENDED, "laporan keuangan yang dipakai direvisi emiten"),
    (ACTIVE, Trigger.MATERIAL_INFORMATION): (SUSPENDED, "informasi material baru terbit dan belum dinilai"),
    (ACTIVE, Trigger.DATA_STALE): (SUSPENDED, "data wajib metode valuasi menjadi kedaluwarsa"),
    (ACTIVE, Trigger.ACTION_CANCELLED): (REVOKED, "aksi korporasi yang menjadi dasar dibatalkan"),
    (ACTIVE, Trigger.BASIS_FAILED): (REVOKED, "salah satu kondisi pembatalan yang dinyatakan terpenuhi"),
    (ACTIVE, Trigger.RED_FLAG_RAISED): (REVOKED, "red flag struktural terbit untuk emiten yang sama"),
    (ACTIVE, Trigger.HORIZON_PASSED): (EXPIRED, "tanggal akhir horizon terlampaui tanpa penilaian ulang"),

    # Yang ditangguhkan masih bisa pulih, gagal, atau kehabisan waktu.
    (SUSPENDED, Trigger.REVIEW_CLEARED): (ACTIVE, "tinjauan selesai dan asumsinya masih berlaku"),
    (SUSPENDED, Trigger.ACTION_CANCELLED): (REVOKED, "aksi korporasi yang menjadi dasar dibatalkan"),
    (SUSPENDED, Trigger.BASIS_FAILED): (REVOKED, "salah satu kondisi pembatalan yang dinyatakan terpenuhi"),
    (SUSPENDED, Trigger.RED_FLAG_RAISED): (REVOKED, "red flag struktural terbit untuk emiten yang sama"),
    (SUSPENDED, Trigger.HORIZON_PASSED): (EXPIRED, "tanggal akhir horizon terlampaui tanpa penilaian ulang"),
}

TERMINAL = {state.key for state in catalog.LIFECYCLE_STATES if state.terminal}


class LifecycleError(RuntimeError):
    """Transisi yang tidak sah. Gagal berisik lebih baik daripada diam-diam mengubah keadaan."""


@dataclass(frozen=True)
class Step:
    state: str
    reason: str
    trigger: str
    at: str
    actor: str = "system"


@dataclass(frozen=True)
class Recommendation:
    """Satu rekomendasi BUY beserta riwayatnya. Riwayat hanya bertambah, tidak pernah ditimpa."""

    ticker: str
    issued_at: str
    reference_price: float
    reference_price_at: str
    fair_value: float
    margin: float
    horizon_end: str
    review_due: str
    rule_version: str
    catalog_version: str
    evidence_case_ids: tuple[str, ...] = ()
    evidence_fact_ids: tuple[str, ...] = ()
    decision: str = "buy"
    history: tuple[Step, ...] = field(default_factory=tuple)

    @property
    def state(self) -> str:
        return self.history[-1].state if self.history else ACTIVE

    @property
    def is_actionable(self) -> bool:
        """Hanya yang ber-status aktif dan belum melewati batas horizon yang boleh ditampilkan sebagai saran hidup.

        Status aktif saja belum cukup untuk menentukan rekomendasi masih berlaku.
        """
        return self.is_actionable_at()

    def is_actionable_at(self, at: str | datetime | None = None) -> bool:
        """Periksa apakah rekomendasi masih berlaku pada waktu tertentu.

        Status aktif saja belum cukup: harus ber-status ACTIVE dan berada dalam batas horizon.
        Bila `at` tidak diberikan, menggunakan waktu langkah terakhir yang tercatat.
        """
        if self.state != ACTIVE:
            return False
        if at is not None:
            moment = _parse_moment(at)
        else:
            moment = _parse_moment(self.history[-1].at) if self.history else _parse_moment(self.issued_at)
        return moment <= _parse_moment(self.horizon_end)

    def apply(self, trigger: Trigger, at: str, actor: str = "system") -> "Recommendation":
        if self.state in TERMINAL:
            raise LifecycleError(
                f"'{trigger.value}' tidak berlaku untuk rekomendasi ber-status '{self.state}' "
                "(status akhir, tidak dapat diubah)"
            )

        at_dt = _parse_moment(at)
        last_step_at = self.history[-1].at if self.history else self.issued_at
        last_dt = _parse_moment(last_step_at)
        if at_dt < last_dt:
            raise LifecycleError(
                f"waktu transisi ({at}) tidak boleh mendahului langkah sebelumnya ({last_step_at})"
            )

        horizon_dt = _parse_moment(self.horizon_end)
        if trigger == Trigger.HORIZON_PASSED:
            if at_dt < horizon_dt:
                raise LifecycleError(
                    f"pemicu '{trigger.value}' tidak dapat diterima sebelum horizon berakhir ({self.horizon_end})"
                )
        elif at_dt >= horizon_dt:
            raise LifecycleError(
                f"tidak dapat menerapkan '{trigger.value}' setelah horizon berakhir ({self.horizon_end})"
            )

        target = TRANSITIONS.get((self.state, trigger))
        if target is None:
            raise LifecycleError(
                f"'{trigger.value}' tidak berlaku untuk rekomendasi ber-status '{self.state}'"
            )

        state, reason = target
        step = Step(state=state, reason=reason, trigger=trigger.value, at=at, actor=actor)
        return replace(self, history=(*self.history, step))


def issue(ticker: str, at: datetime, **fields) -> Recommendation:
    """Terbitkan rekomendasi BUY baru dalam keadaan berlaku.

    Dipanggil Tahap 6 setelah seluruh syarat `catalog.DECISIONS['buy']` terpenuhi. Modul ini tidak
    memeriksa syarat itu: valuasi bukan urusannya, dan memeriksa separuh syarat lebih berbahaya
    daripada tidak memeriksa sama sekali.
    """
    moment = at.isoformat()
    if "horizon_end" in fields:
        if _parse_moment(fields["horizon_end"]) <= _parse_moment(moment):
            raise LifecycleError(
                f"horizon_end ({fields['horizon_end']}) harus setelah waktu penerbitan ({moment})"
            )
    first = Step(state=ACTIVE, reason="diterbitkan setelah seluruh syarat buy terpenuhi",
                 trigger="issued", at=moment)
    return Recommendation(ticker=ticker, issued_at=moment, history=(first,), **fields)

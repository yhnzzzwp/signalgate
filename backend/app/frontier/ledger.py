"""Buku besar budget frontier yang tahan resume, retry, restart, dan panggilan bersamaan.

Setiap percobaan memesan (reserve) token dan biaya batas atas SEBELUM request dikirim, di dalam satu
transaksi SQLite `BEGIN IMMEDIATE`: pemeriksaan batas dan pencatatan terjadi atomik, jadi dua thread
atau proses tidak bisa sama-sama lolos dari sisa kuota yang sama, dan pekerjaan identik yang sedang
berjalan (`job_key` sama) tidak dikirim dua kali.

Setelah jawaban datang, reservasi diganti biaya estimasi dari usage API. Bila provider mungkin sudah
memproses request tapi usage tidak diketahui (timeout, error server), reservasi tetap dihitung penuh:
biayanya tidak diketahui, bukan nol. Keterbatasan: request yang sudah diterima provider tetap bisa
ditagih walau klien menyerah menunggu; batas di sini adalah batas atas dari sisi SignalGate.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

WIB = timezone(timedelta(hours=7))

SCHEMA = """
CREATE TABLE IF NOT EXISTS frontier_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key TEXT NOT NULL,
    job_key TEXT NOT NULL,
    day TEXT NOT NULL,
    status TEXT NOT NULL,
    reserved_tokens INTEGER NOT NULL,
    reserved_usd REAL NOT NULL,
    actual_tokens INTEGER,
    actual_usd REAL,
    note TEXT,
    overshoot INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS frontier_calls_run ON frontier_calls(run_key);
CREATE INDEX IF NOT EXISTS frontier_calls_day ON frontier_calls(day);
CREATE INDEX IF NOT EXISTS frontier_calls_job ON frontier_calls(job_key, status);
"""

# Biaya/token yang dihitung ke batas per status baris.
CHARGED_TOKENS = ("CASE status WHEN 'settled' THEN COALESCE(actual_tokens, reserved_tokens) "
                  "WHEN 'released' THEN 0 ELSE reserved_tokens END")
CHARGED_USD = ("CASE status WHEN 'settled' THEN COALESCE(actual_usd, reserved_usd) "
               "WHEN 'released' THEN 0 ELSE reserved_usd END")


@dataclass(frozen=True)
class Reservation:
    id: int
    run_key: str
    job_key: str
    tokens: int
    usd: float


@dataclass(frozen=True)
class Denied:
    reason: str  # calls_per_run | tokens_per_run | cost_per_run | cost_per_day | cost_total | in_flight | overshoot
    message: str


class BudgetLedger:
    def __init__(self, path: Path, *, max_calls_per_run: int, max_tokens_per_run: int, max_cost_usd_per_run: float,
                 max_cost_usd_per_day: float, stale_after_seconds: float, max_cost_usd_total: float | None = None,
                 clock=None) -> None:
        self.path = Path(path)
        self.max_calls_per_run = max_calls_per_run
        self.max_tokens_per_run = max_tokens_per_run
        self.max_cost_usd_per_run = max_cost_usd_per_run
        self.max_cost_usd_per_day = max_cost_usd_per_day
        # None = tanpa batas total (hanya per run dan per hari).
        self.max_cost_usd_total = max_cost_usd_total
        self.stale_after = timedelta(seconds=stale_after_seconds)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(frontier_calls)")}
            if "overshoot" not in columns:  # ledger dari versi sebelum pencatatan overshoot
                connection.execute("ALTER TABLE frontier_calls ADD COLUMN overshoot INTEGER NOT NULL DEFAULT 0")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _day(self, now: datetime) -> str:
        return now.astimezone(WIB).date().isoformat()

    def _expire_stale(self, connection, now: datetime) -> None:
        cutoff = (now - self.stale_after).isoformat()
        connection.execute("UPDATE frontier_calls SET status='unknown', updated_at=?, "
                           "note=COALESCE(note, 'reservasi basi: proses terhenti sebelum hasil tercatat') "
                           "WHERE status='reserved' AND created_at < ?", (now.isoformat(), cutoff))

    def reserve(self, run_key: str, job_key: str, tokens: int, usd: float) -> Reservation | Denied:
        now = self.clock()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._expire_stale(connection, now)
                if connection.execute("SELECT 1 FROM frontier_calls WHERE job_key=? AND status='reserved'",
                                      (job_key,)).fetchone():
                    connection.execute("COMMIT")
                    return Denied("in_flight", "Pekerjaan frontier yang sama sedang berjalan; tidak dikirim dua kali.")
                if connection.execute("SELECT 1 FROM frontier_calls WHERE run_key=? AND overshoot=1",
                                      (run_key,)).fetchone():
                    connection.execute("COMMIT")
                    return Denied("overshoot", "Pemakaian aktual pernah melebihi reservasi pada run ini; panggilan "
                                               "berikutnya dihentikan karena batas berbasis estimasi terbukti kurang.")
                calls, run_tokens, run_usd = connection.execute(
                    f"SELECT COUNT(*), COALESCE(SUM({CHARGED_TOKENS}), 0), COALESCE(SUM({CHARGED_USD}), 0) "
                    "FROM frontier_calls WHERE run_key=?", (run_key,)).fetchone()
                (day_usd,) = connection.execute(
                    f"SELECT COALESCE(SUM({CHARGED_USD}), 0) FROM frontier_calls WHERE day=?",
                    (self._day(now),)).fetchone()
                (total_usd,) = connection.execute(
                    f"SELECT COALESCE(SUM({CHARGED_USD}), 0) FROM frontier_calls").fetchone()
                denied = None
                if calls + 1 > self.max_calls_per_run:
                    denied = Denied("calls_per_run", f"Batas {self.max_calls_per_run} panggilan frontier per run tercapai.")
                elif run_tokens + tokens > self.max_tokens_per_run:
                    denied = Denied("tokens_per_run", f"Reservasi {tokens} token melampaui sisa batas "
                                                      f"{self.max_tokens_per_run} token per run.")
                elif run_usd + usd > self.max_cost_usd_per_run:
                    denied = Denied("cost_per_run", f"Reservasi ${usd:.4f} melampaui sisa batas biaya per run "
                                                    f"${self.max_cost_usd_per_run:.2f} (terpakai ${run_usd:.4f}, estimasi).")
                elif day_usd + usd > self.max_cost_usd_per_day:
                    denied = Denied("cost_per_day", f"Reservasi ${usd:.4f} melampaui sisa batas biaya harian "
                                                    f"${self.max_cost_usd_per_day:.2f} (terpakai ${day_usd:.4f}, estimasi).")
                elif self.max_cost_usd_total is not None and total_usd + usd > self.max_cost_usd_total:
                    denied = Denied("cost_total", f"Reservasi ${usd:.4f} melampaui sisa batas biaya total "
                                                  f"${self.max_cost_usd_total:.2f} (terpakai ${total_usd:.4f}, estimasi).")
                if denied:
                    connection.execute("COMMIT")
                    return denied
                cursor = connection.execute(
                    "INSERT INTO frontier_calls (run_key, job_key, day, status, reserved_tokens, reserved_usd, "
                    "created_at, updated_at) VALUES (?, ?, ?, 'reserved', ?, ?, ?, ?)",
                    (run_key, job_key, self._day(now), int(tokens), float(usd), now.isoformat(), now.isoformat()))
                connection.execute("COMMIT")
                return Reservation(cursor.lastrowid, run_key, job_key, int(tokens), float(usd))
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def _update(self, reservation: Reservation, status: str, tokens=None, usd=None, note=None,
                overshoot: bool = False) -> None:
        now = self.clock().isoformat()
        with closing(self._connect()) as connection:
            connection.execute("UPDATE frontier_calls SET status=?, actual_tokens=?, actual_usd=?, note=?, overshoot=?, "
                               "updated_at=? WHERE id=?",
                               (status, tokens, usd, note, int(overshoot), now, reservation.id))

    def settle(self, reservation: Reservation, tokens: int | None, usd: float | None, note: str | None = None) -> bool:
        """Usage diketahui: ganti reservasi dengan estimasi aktual. Tanpa usage, reservasi tetap dihitung.

        Mengembalikan True bila aktual melebihi reservasi (overshoot): tercatat, dihitung penuh ke batas, dan
        panggilan berikutnya pada run yang sama ditolak."""
        if tokens is None or usd is None:
            self._update(reservation, "unknown", note=note or "usage tidak dikembalikan; reservasi tetap dihitung")
            return False
        overshoot = float(usd) > reservation.usd + 1e-12 or int(tokens) > reservation.tokens
        if overshoot:
            note = (f"OVERSHOOT: aktual {int(tokens)} token/${float(usd):.6f} melebihi reservasi "
                    f"{reservation.tokens} token/${reservation.usd:.6f}" + (f"; {note}" if note else ""))
        self._update(reservation, "settled", int(tokens), float(usd), note, overshoot)
        return overshoot

    def release(self, reservation: Reservation, note: str) -> None:
        """Provider menolak sebelum memproses (mis. 401/429/koneksi gagal): tidak ada biaya, panggilan tetap dihitung."""
        self._update(reservation, "released", 0, 0.0, note)

    def mark_unknown(self, reservation: Reservation, note: str) -> None:
        self._update(reservation, "unknown", note=note)

    def usage(self, run_key: str | None = None) -> dict:
        now = self.clock()
        with closing(self._connect()) as connection:
            self._expire_stale(connection, now)
            day_calls, day_usd, unknown_day = connection.execute(
                f"SELECT COUNT(*), COALESCE(SUM({CHARGED_USD}), 0), "
                "COALESCE(SUM(CASE WHEN status='unknown' THEN 1 ELSE 0 END), 0) FROM frontier_calls WHERE day=?",
                (self._day(now),)).fetchone()
            total_calls, total_usd, overshoots = connection.execute(
                f"SELECT COUNT(*), COALESCE(SUM({CHARGED_USD}), 0), COALESCE(SUM(overshoot), 0) "
                "FROM frontier_calls").fetchone()
            result = {"day": self._day(now), "day_calls": day_calls, "day_cost_usd_charged": round(day_usd, 6),
                      "day_calls_usage_unknown": unknown_day, "max_cost_usd_per_day": self.max_cost_usd_per_day,
                      "total_calls": total_calls, "total_cost_usd_charged": round(total_usd, 6),
                      "max_cost_usd_total": self.max_cost_usd_total, "total_overshoots": overshoots}
            if run_key is not None:
                calls, tokens, usd, unknown = connection.execute(
                    f"SELECT COUNT(*), COALESCE(SUM({CHARGED_TOKENS}), 0), COALESCE(SUM({CHARGED_USD}), 0), "
                    "COALESCE(SUM(CASE WHEN status='unknown' THEN 1 ELSE 0 END), 0) FROM frontier_calls "
                    "WHERE run_key=?", (run_key,)).fetchone()
                result.update({"run_calls": calls, "run_tokens_charged": tokens, "run_cost_usd_charged": round(usd, 6),
                               "run_calls_usage_unknown": unknown, "max_calls_per_run": self.max_calls_per_run,
                               "max_tokens_per_run": self.max_tokens_per_run,
                               "max_cost_usd_per_run": self.max_cost_usd_per_run})
        result["note"] = ("Biaya adalah estimasi dari usage API dan tabel harga berversi; panggilan tanpa usage "
                          "dihitung sebesar reservasinya, bukan nol. Batas ditegakkan lewat reservasi berbasis "
                          "estimasi konservatif, bukan jaminan nominal absolut; overshoot dicatat.")
        return result

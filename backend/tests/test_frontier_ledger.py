"""Buku besar budget: atomik, persisten, batas harian/total, overshoot, dan reservasi basi."""
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone

from app.frontier import pricing
from app.frontier.ledger import BudgetLedger, Denied, Reservation


def ledger(path, clock=None, **limits):
    values = dict(max_calls_per_run=10, max_tokens_per_run=1_000_000, max_cost_usd_per_run=10.0,
                  max_cost_usd_per_day=10.0, max_cost_usd_total=None, stale_after_seconds=300)
    values.update(limits)
    return BudgetLedger(path / "ledger.sqlite", clock=clock, **values)


def test_b01_concurrent_reservations_cannot_both_pass_near_the_limit(tmp_path):
    book = ledger(tmp_path, max_cost_usd_per_run=0.015)
    results, start = [], threading.Barrier(8)

    def worker(index):
        start.wait()
        results.append(BudgetLedger(book.path, max_calls_per_run=10, max_tokens_per_run=1_000_000,
                                    max_cost_usd_per_run=0.015, max_cost_usd_per_day=10.0,
                                    stale_after_seconds=300).reserve("run", f"job-{index}", 1000, 0.01))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    granted = [item for item in results if isinstance(item, Reservation)]
    assert len(granted) == 1, results
    assert all(item.reason == "cost_per_run" for item in results if isinstance(item, Denied))


def test_b02_identical_job_in_flight_is_refused(tmp_path):
    book = ledger(tmp_path)
    assert isinstance(book.reserve("run", "same-job", 100, 0.001), Reservation)
    second = book.reserve("run", "same-job", 100, 0.001)
    assert isinstance(second, Denied) and second.reason == "in_flight"


def test_b03_counters_survive_a_new_process_and_resume(tmp_path):
    first = ledger(tmp_path, max_calls_per_run=2)
    reservation = first.reserve("run-A", "job-1", 100, 0.001)
    first.settle(reservation, 90, 0.0009)
    reservation = first.reserve("run-A", "job-2", 100, 0.001)
    first.release(reservation, "429")  # dilepas, tetapi tetap dihitung sebagai panggilan
    restarted = ledger(tmp_path, max_calls_per_run=2)
    denied = restarted.reserve("run-A", "job-3", 100, 0.001)
    assert isinstance(denied, Denied) and denied.reason == "calls_per_run"
    assert restarted.usage("run-A")["run_calls"] == 2
    assert isinstance(restarted.reserve("run-B", "job-4", 100, 0.001), Reservation)


def test_b05_daily_and_total_limits_stop_before_any_request(tmp_path):
    book = ledger(tmp_path, max_cost_usd_per_day=0.02, max_cost_usd_total=0.03)
    settle = book.reserve("run-1", "j1", 100, 0.015)
    book.settle(settle, 100, 0.015)
    denied = book.reserve("run-2", "j2", 100, 0.01)
    assert isinstance(denied, Denied) and denied.reason == "cost_per_day"

    day = [datetime(2026, 9, 28, 3, tzinfo=timezone.utc)]
    later = ledger(tmp_path, clock=lambda: day[0], max_cost_usd_per_day=1.0, max_cost_usd_total=0.03)
    day[0] = day[0] + timedelta(days=1)
    reservation = later.reserve("run-3", "j3", 100, 0.01)
    assert isinstance(reservation, Reservation)
    later.settle(reservation, 100, 0.01)
    denied = later.reserve("run-4", "j4", 100, 0.01)
    assert isinstance(denied, Denied) and denied.reason == "cost_total"
    assert later.usage()["total_cost_usd_charged"] >= 0.025


def test_b06_usage_above_the_reservation_is_recorded_and_stops_the_run(tmp_path):
    book = ledger(tmp_path)
    reservation = book.reserve("run", "job", 1000, 0.001)
    assert book.settle(reservation, 5000, 0.004) is True
    usage = book.usage("run")
    assert usage["total_overshoots"] == 1
    assert usage["run_cost_usd_charged"] >= 0.004  # aktual yang lebih besar dihitung penuh
    denied = book.reserve("run", "job-next", 100, 0.0001)
    assert isinstance(denied, Denied) and denied.reason == "overshoot"
    assert isinstance(book.reserve("run-other", "job-x", 100, 0.0001), Reservation)


def test_timeout_without_usage_is_charged_at_the_reservation_not_zero(tmp_path):
    book = ledger(tmp_path)
    reservation = book.reserve("run", "job", 1000, 0.012)
    book.mark_unknown(reservation, "timeout")
    usage = book.usage("run")
    assert usage["run_cost_usd_charged"] == 0.012 and usage["run_calls_usage_unknown"] == 1


def test_a_stale_reservation_becomes_unknown_but_stays_charged(tmp_path):
    now = [datetime(2026, 9, 28, 3, tzinfo=timezone.utc)]
    book = ledger(tmp_path, clock=lambda: now[0], stale_after_seconds=60)
    book.reserve("run", "job", 1000, 0.01)
    now[0] += timedelta(minutes=5)
    again = book.reserve("run", "job", 1000, 0.01)  # proses lama mati: pekerjaan boleh dicoba lagi
    assert isinstance(again, Reservation)
    assert book.usage("run")["run_cost_usd_charged"] == 0.02


def test_an_old_ledger_file_gains_the_overshoot_column(tmp_path):
    path = tmp_path / "ledger.sqlite"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE frontier_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, run_key TEXT NOT NULL, "
                           "job_key TEXT NOT NULL, day TEXT NOT NULL, status TEXT NOT NULL, reserved_tokens INTEGER "
                           "NOT NULL, reserved_usd REAL NOT NULL, actual_tokens INTEGER, actual_usd REAL, note TEXT, "
                           "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        connection.commit()
    book = ledger(tmp_path)
    reservation = book.reserve("run", "job", 10, 0.001)
    assert book.settle(reservation, 5, 0.0005) is False


def test_the_reservation_is_an_upper_bound_for_unicode_symbols_and_json():
    """Token input <= byte UTF-8 (BPE tingkat byte) + cadangan; teks Indonesia, simbol, dan JSON."""
    samples = ["Perseroan menerbitkan 868 juta saham (±7,37%) — Rp800 miliar; ≥ “kutipan” ✓",
               '{"artikel": [{"id": "news:ab12", "isi": "angka 1.500,25 dan 2,5 triliun"}]}' * 40,
               "漢字とカタカナ € £ ¥ " * 100]
    for text in samples:
        tokens, usd = pricing.reservation("deepseek-flash", len(text.encode("utf-8")), 8192)
        assert tokens >= len(text.encode("utf-8")) + 8192
        assert usd >= (len(text.encode("utf-8")) * 0.30 + 8192 * 1.20) / 1_000_000

"""QA 2026-09-16 P2 #5: refresh halaman atau koneksi putus tidak punya pemulihan run."""
import pathlib
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.pipeline import jobs


class JobLifecycleTests(unittest.TestCase):
    def setUp(self):
        import app.main as main
        self.main = main
        self.client = TestClient(main.app)
        self.release = threading.Event()
        self.addCleanup(self.release.set)

    def wait(self, job_id, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.client.get(f"/runs/{job_id}").json()
            if job["status"] != jobs.RUNNING:
                return job
            time.sleep(0.01)
        raise AssertionError(f"Job {job_id} tidak selesai dalam {timeout} detik")

    def start_blocking_scan(self):
        """Mulai run yang menggantung sampai dilepas, meniru inference yang memakan menit."""
        def discover(*_args, **_kwargs):
            self.release.wait(10)
            return {"run_id": "r", "coverage": "partial", "articles_checked": 0, "listing_pages": [],
                    "announcements_matched": 0, "failures": [], "candidates": [], "created_at": "",
                    "coverage_note": "catatan"}

        def no_candidates(*_args, **_kwargs):
            yield from ()

        patches = [patch.object(self.main, "DocumentSource"),
                   patch.object(self.main.Scanner, "discover", side_effect=discover),
                   patch.object(self.main, "research_queue_items", side_effect=no_candidates)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return self.client.post("/scan/run").json()

    def test_a_run_is_readable_again_after_the_page_is_reloaded(self):
        started = self.start_blocking_scan()
        active = self.client.get("/runs/active").json()
        self.assertEqual(active["id"], started["id"])
        self.assertEqual(active["kind"], "scan")
        self.release.set()
        self.assertEqual(self.wait(started["id"])["status"], jobs.COMPLETED)

    def test_the_post_answers_immediately_instead_of_holding_the_connection(self):
        """Menahan POST selama run berarti koneksi putus menghilangkan hasilnya bagi pengguna."""
        started = time.monotonic()
        job = self.start_blocking_scan()
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(job["status"], jobs.RUNNING)
        self.release.set()
        self.wait(job["id"])

    def test_a_second_run_is_refused_with_an_explanation(self):
        first = self.start_blocking_scan()
        clash = self.client.post("/scan/run")
        self.assertEqual(clash.status_code, 409)
        self.assertIn("sedang berjalan", clash.json()["detail"])
        self.release.set()
        self.wait(first["id"])

    def test_no_run_active_reads_as_null_rather_than_an_error(self):
        self.assertIsNone(self.client.get("/runs/active").json())

    def test_an_unknown_run_is_a_clean_404(self):
        self.assertEqual(self.client.get("/runs/tidak-ada").status_code, 404)

    def test_finished_runs_stay_in_the_history(self):
        job = self.start_blocking_scan()
        self.release.set()
        self.wait(job["id"])
        history = [row["id"] for row in self.client.get("/runs?limit=5").json()]
        self.assertIn(job["id"], history)
        self.assertEqual(self.client.get("/runs?limit=0").status_code, 422)

    def test_the_lock_is_free_once_the_run_closes(self):
        job = self.start_blocking_scan()
        self.release.set()
        self.wait(job["id"])
        self.assertFalse(self.main.run_lock.locked())
        self.assertIsNone(self.client.get("/runs/active").json())


class StaleRunTests(unittest.TestCase):
    """Backend yang mati di tengah run meninggalkan job `running` yang mengunci tombol selamanya."""

    def test_a_run_left_behind_by_a_dead_process_is_closed_on_startup(self):
        import app.main as main
        with main.session_factory() as session:
            stranded = jobs.create(session, "scan")
            self.assertEqual(stranded.status, jobs.RUNNING)
            released = jobs.release_stale(session)
            self.assertEqual(released, 1)
            self.assertIsNone(jobs.active(session))
            closed = jobs.serialize(session.get(type(stranded), stranded.id))
        self.assertEqual(closed["status"], jobs.FAILED)
        self.assertIn("Backend berhenti", closed["error"])

    def test_releasing_when_nothing_is_stranded_changes_nothing(self):
        import app.main as main
        with main.session_factory() as session:
            self.assertEqual(jobs.release_stale(session), 0)


class SchemaMigrationTests(unittest.TestCase):
    """`create_all` tidak pernah menyentuh tabel lama, jadi database yang sudah dipakai butuh ini."""

    def old_database(self):
        import sqlite3
        folder = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        path = folder / "lama.db"
        connection = sqlite3.connect(path)
        connection.execute(
            "create table screened_events (id integer primary key, ticker varchar, headline varchar,"
            " source_url varchar, bucket varchar, label varchar, confidence float, provider varchar,"
            " gate_status varchar, watch_status varchar, payload json, created_at datetime)"
        )
        connection.execute("insert into screened_events (ticker, label) values ('IDEA', 'inconclusive')")
        connection.commit()
        connection.close()
        return path

    def columns(self, path):
        import sqlite3
        connection = sqlite3.connect(path)
        try:
            return {row[1] for row in connection.execute("pragma table_info(screened_events)")}
        finally:
            connection.close()

    def test_missing_columns_are_added_without_touching_existing_rows(self):
        from app.config import Settings
        from app.db.session import build_engine
        path = self.old_database()
        build_engine(Settings(_env_file=None, signalgate_db_path=str(path)))
        self.assertIn("dedupe_key", self.columns(path))
        self.assertIn("updated_at", self.columns(path))
        import sqlite3
        connection = sqlite3.connect(path)
        self.assertEqual(connection.execute("select ticker from screened_events").fetchone(), ("IDEA",))
        connection.close()

    def test_running_the_migration_twice_is_harmless(self):
        from app.config import Settings
        from app.db.session import build_engine, ensure_schema
        path = self.old_database()
        settings = Settings(_env_file=None, signalgate_db_path=str(path))
        build_engine(settings)
        engine = build_engine(settings)
        self.assertEqual([item for item in ensure_schema(engine) if "." in item], [])

    def test_the_unique_index_refuses_a_second_card_for_one_candidate(self):
        """Pertahanan lapis kedua: kalau upsert terlewat, database yang menolaknya."""
        import sqlite3
        from app.config import Settings
        from app.db.session import build_engine
        path = self.old_database()
        build_engine(Settings(_env_file=None, signalgate_db_path=str(path)))
        connection = sqlite3.connect(path)
        connection.execute("insert into screened_events (ticker, dedupe_key) values ('A', 'scan:q1')")
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("insert into screened_events (ticker, dedupe_key) values ('B', 'scan:q1')")
        connection.close()


class TimestampTests(unittest.TestCase):
    """QA 2026-09-16 P2 #10: timestamp tanpa zona ditafsirkan sebagai waktu lokal oleh browser."""

    def test_job_timestamps_carry_an_explicit_offset(self):
        import app.main as main
        client = TestClient(main.app)
        with main.session_factory() as session:
            job_id = jobs.create(session, "scan").id  # dibaca di dalam sesi; setelah tutup jadi detached
            jobs.finish(session, job_id, {"processed": 0})
        job = client.get(f"/runs/{job_id}").json()
        for field in ("started_at", "finished_at"):
            self.assertTrue(job[field].endswith("+00:00"), f"{field}={job[field]}")

    def test_a_naive_datetime_is_declared_utc_rather_than_guessed(self):
        from datetime import UTC, datetime
        naive = datetime(2026, 9, 16, 7, 3, 52)
        self.assertEqual(jobs.utc_iso(naive), jobs.utc_iso(naive.replace(tzinfo=UTC)))
        self.assertIsNone(jobs.utc_iso(None))


if __name__ == "__main__":
    unittest.main()

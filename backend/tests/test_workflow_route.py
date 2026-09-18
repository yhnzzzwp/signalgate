"""Endpoint laporan: penolakan mode hemat, job latar, replay tanpa kredit, resume, dan pembacaan."""
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.workflow.snapshot import FixtureGateway
from tests import workflow_fixtures as fx
from tests.test_workflow_graph import pool, snapshot_responses


class WorkflowRouteTests(unittest.TestCase):
    def setUp(self):
        import app.main as main

        self.main = main
        self.client = TestClient(main.app)
        self.root = Path(tempfile.mkdtemp(prefix="signalgate-workflow-route-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        runner = main.workflow_runner
        self.original = (runner.settings, runner.gateway_factory, runner.model_pool_factory)
        runner.settings = runner.settings.model_copy(update={"workflow_directory": self.root})
        runner.gateway_factory = lambda: FixtureGateway(snapshot_responses())
        runner.model_pool_factory = pool()
        self.addCleanup(self.restore, runner)

    def restore(self, runner):
        runner.settings, runner.gateway_factory, runner.model_pool_factory = self.original

    def wait(self, job_id, timeout=20.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.client.get(f"/runs/{job_id}").json()
            if job["status"] != "running":
                return job
            time.sleep(0.02)
        raise AssertionError(f"Job {job_id} tidak selesai dalam {timeout} detik")

    def start(self, **body):
        response = self.client.post("/workflow/run", json=body)
        if response.status_code != 202:
            return response, None
        payload = response.json()
        return response, (payload, self.wait(payload["id"]))

    def test_a_live_run_is_refused_while_sectors_is_disabled(self):
        response = self.client.post("/workflow/run", json={"ticker": fx.TICKER})
        self.assertEqual(response.status_code, 400)
        self.assertIn("SECTORS", response.json()["detail"].upper())

    def test_a_run_without_ticker_or_replay_is_refused(self):
        self.assertEqual(self.client.post("/workflow/run", json={}).status_code, 422)

    def test_the_report_is_readable_after_the_background_job_finishes(self):
        with patch.object(self.main, "require_sectors_key", lambda: None):
            response, (job, finished) = self.start(ticker=fx.TICKER, horizon="short")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(finished["status"], "completed")
        run_id = job["run_id"]
        detail = self.client.get(f"/workflow/runs/{run_id}").json()
        self.assertEqual(detail["run"]["ticker"], fx.TICKER)
        self.assertEqual(sorted(detail["report"]["panels"]), ["fundamental", "news", "technical", "valuation"])
        self.assertEqual(detail["report"]["status"], detail["run"]["status"])
        listing = self.client.get("/workflow/runs", params={"ticker": fx.TICKER}).json()
        self.assertEqual(listing["results"][0]["run_id"], run_id)
        self.assertTrue(any(stage["key"] == "review" for stage in listing["stages"]))
        latest = self.client.get("/workflow/reports/latest", params={"ticker": fx.TICKER}).json()
        self.assertEqual(latest["report"]["run_id"], run_id)

    def test_a_replay_run_needs_no_sectors_key(self):
        with patch.object(self.main, "require_sectors_key", lambda: None):
            _response, (job, _finished) = self.start(ticker=fx.TICKER)
        response, (replay_job, finished) = self.start(replay_of=job["run_id"])
        self.assertEqual(response.status_code, 202)
        self.assertEqual(finished["status"], "completed")
        report = self.client.get(f"/workflow/runs/{replay_job['run_id']}").json()["report"]
        self.assertEqual(report["data_mode"], "replay")
        self.assertEqual(report["credits_used"], 0)

    def test_a_replay_of_an_unknown_run_is_refused(self):
        self.assertEqual(self.client.post("/workflow/run", json={"replay_of": "tidak-ada"}).status_code, 400)

    def test_progress_events_are_recorded_before_each_node_and_streamed(self):
        with patch.object(self.main, "require_sectors_key", lambda: None):
            self.start(ticker=fx.TICKER)
        audit = self.client.get("/audit", params={"limit": 200, "ticker": fx.TICKER}).json()["results"]
        nodes = [entry["detail"]["node"] for entry in audit if entry["stage"] == "workflow"]
        self.assertIn("snapshot", nodes)
        self.assertIn("publish", nodes)
        self.assertTrue(all(entry["detail"]["phase"] == "start" for entry in audit if entry["stage"] == "workflow"))

    def test_resume_is_refused_for_an_unknown_run(self):
        self.assertEqual(self.client.post("/workflow/runs/tidak-ada/resume").status_code, 404)

    def test_cancel_is_refused_when_the_run_is_not_active_here(self):
        self.assertEqual(self.client.post("/workflow/runs/tidak-ada/cancel").status_code, 409)

    def test_two_runs_cannot_overlap(self):
        with patch.object(self.main, "require_sectors_key", lambda: None):
            self.main.run_lock.acquire()
            try:
                response = self.client.post("/workflow/run", json={"ticker": fx.TICKER})
            finally:
                self.main.run_lock.release()
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()

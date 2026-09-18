"""Retest P1/P2 offline; run: backend/.venv/bin/python docs/qa_2026_09_17_reproduce.py.

Models and discovery are fake; queue, publication, SQLite and serialization are real.
All generated data lives in a temporary directory. Prints observations, not pass assertions.
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import Settings
from app.db.session import build_session_factory
from app.db.models import ScreenedEventRecord
from app.pipeline.audit import persist_screened_event
from app.pipeline.presentation import screen_outcome
from app.pipeline.schema import ActionBucket, CandidateEvent, Verdict, VerdictLabel
from app.research.models import ResearchOutcome
from app.scan import (Scanner, eligible_items, mark_published, research_queue_items,
                      summarize_scan, write_json)
from sqlalchemy import select


def emit(name, **result):
    print(name, json.dumps(result, ensure_ascii=False))


def event(ticker="TEST"):
    return CandidateEvent(ticker=ticker, headline="Rights issue September 2026", body="",
                          source_url=f"https://news.test/{ticker}/2026", published_at="2026-09-17",
                          bucket=ActionBucket.rights_issue, matched_keywords=["rights issue"])


def seed(root, item_id="00", **extra):
    item = {"id": item_id, "event": event().model_dump(mode="json"), "pdf_urls": [],
            "status": "needs_document", **extra}
    write_json(root / "queue" / f"{item_id}.json", item)
    return item


class Engine:
    def __init__(self, settings, status="completed"):
        self.settings, self.status, self.calls = settings, status, 0

    def research(self, event, *args, **kwargs):
        self.calls += 1
        outcome = ResearchOutcome(status=self.status, case_id=f"{event.ticker}-{self.calls}",
                                  verdict=Verdict(label=VerdictLabel.inconclusive, confidence=0,
                                                  provider="qa", rationale_bullets=[self.status]))
        write_json(Path(self.settings.research_cases_dir) / outcome.case_id / "decision.json",
                   outcome.model_dump(mode="json"))
        return outcome

    def close(self):
        pass


def run():
    with tempfile.TemporaryDirectory(prefix="signalgate-qa17-") as folder:
        root = Path(folder)
        settings = Settings(_env_file=None, llm_backend="off", sectors_api_enabled=False,
                            research_cases_dir=root / "cases", signalgate_db_path=str(root / "db.sqlite"))
        factory = build_session_factory(settings)

        # Original P1 #1: new candidates get a turn after failures.
        queue = root / "fairness"
        for index in range(4):
            seed(queue, str(index), event=event(f"AA{index:02}").model_dump(mode="json"))
        with patch("app.scan.build_provider", return_value=Engine(settings, "insufficient_evidence")):
            first = [e.ticker for e, _, _ in research_queue_items(settings, queue, 3)]
            second = [e.ticker for e, _, _ in research_queue_items(settings, queue, 3)]
        emit("QUEUE_FAIRNESS", first=first, second=second)

        # Original P1 #2: initial unpublished completion recovers from artifact.
        queue = root / "initial-publication"
        seed(queue)
        engine = Engine(settings)
        with patch("app.scan.build_provider", return_value=engine):
            generator = research_queue_items(settings, queue, 1)
            next(generator)
            generator.close()  # simulate failed publication
            recovered = list(research_queue_items(settings, queue, 1))
        emit("INITIAL_PUBLICATION", recovered=len(recovered), inference_calls=engine.calls)

        # Retry transitions from already-published failure to completed, then commit fails.
        queue = root / "retry-publication"
        seed(queue)
        engine = Engine(settings, "model_unavailable")
        with patch("app.scan.build_provider", return_value=engine):
            for e, outcome, item in research_queue_items(settings, queue, 1):
                with factory() as session:
                    persist_screened_event(session, screen_outcome(e, None, outcome), "scan:00")
                mark_published(queue, item["id"], item["case_id"])
            path = queue / "queue" / "00.json"
            item = json.loads(path.read_text())
            item["next_retry_at"] = "2020-01-01T00:00:00+00:00"
            write_json(path, item)
            engine.status = "completed"
            generator = research_queue_items(settings, queue, 1)
            e, outcome, item = next(generator)
            with factory() as session, patch.object(session, "commit", side_effect=RuntimeError("disk full")):
                try:
                    persist_screened_event(session, screen_outcome(e, None, outcome), "scan:00")
                except RuntimeError:
                    session.rollback()
            generator.close()
            recovered = list(research_queue_items(settings, queue, 1))
        with factory() as session:
            row = session.scalars(select(ScreenedEventRecord)).one()
            emit("RETRY_PUBLICATION", queue_status=item["status"], old_publication_marker=item.get("published_at"),
                 recovered=len(recovered), dashboard_status=row.payload["research"]["status"])

        # One matching group is accepted even with a conflicting known article date.
        queue = root / "wrong-date"
        item = seed(queue)
        report = {"candidates": [item]}
        group = {"ticker": "TEST", "urls": ["https://idx.test/2024.pdf"],
                 "dates": {"20240110"}, "numbers": {"31000001"}}
        Scanner(None, queue).attach_announcement_documents(report, [(group, ["rights issue"], "idx.co.id")])
        emit("WRONG_DATE", article_date=item["event"]["published_at"], pdf_dates=item.get("document_dates"),
             attached=item["pdf_urls"], status=item["status"], rejected=item.get("documents_rejected"))

        # Ambiguous association metadata is not passed to the research/publication boundary.
        queue = root / "ambiguous"
        item = seed(queue)
        report = {"candidates": [item]}
        group2 = {**group, "urls": ["https://idx.test/2026.pdf"], "dates": {"20260917"}, "numbers": {"32148872"}}
        Scanner(None, queue).attach_announcement_documents(report, [(g, ["rights issue"], "idx.co.id") for g in [group, group2]])
        before = item["status"]
        with patch("app.scan.build_provider", return_value=Engine(settings)):
            e, outcome, processed_item = list(research_queue_items(settings, queue, 1))[0]
        screened = screen_outcome(e, None, outcome)
        emit("AMBIGUITY_LOST", before=before, after=processed_item["status"], gate=screened.gate.status.value,
             issues=screened.research["issues"])

        # Pending is currently eligible-now, not the full queue still awaiting work.
        queue = root / "summary"
        seed(queue, status="model_unavailable", attempt_count=1, next_retry_at="2099-01-01T00:00:00+00:00")
        summary = summarize_scan(queue, {"candidates": [], "created_at": "2026-09-17"}, 0)
        emit("BACKOFF_SUMMARY", queued=1, eligible=len(eligible_items(queue)), pending=summary["pending"],
             retry_waiting=summary.get("retry_waiting"), next_retry_at=summary.get("next_retry_at"))


if __name__ == "__main__":
    run()

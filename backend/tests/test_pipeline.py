"""Jalur Sectors adalah inti menurut aturan hackathon, dan setiap run memakai kredit berbayar."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from app.config import Settings
from app.db.models import ScreenedEventRecord
from app.db.session import build_session_factory
from app.pipeline import orchestrator
from app.pipeline.schema import ActionBucket, CandidateEvent, Verdict, VerdictLabel
from app.pipeline.stream import run_events
from app.research.models import ResearchOutcome


def news(ticker, url=None, bucket=ActionBucket.rights_issue):
    return CandidateEvent(ticker=ticker, headline=f"{ticker} rights issue", body="", published_at="2026-09-17",
                          source_url=url or f"https://berita.test/{ticker}", bucket=bucket,
                          matched_keywords=["rights issue"])


class Provider:
    def __init__(self, status="completed"):
        self.status, self.researched = status, []

    def research(self, event, snapshot, report=None, require_sectors=True):
        self.researched.append(event.ticker)
        return ResearchOutcome(status=self.status, case_id=f"{event.ticker}-{len(self.researched)}",
                               verdict=Verdict(label=VerdictLabel.inconclusive, confidence=0.0,
                                               provider="fake", rationale_bullets=[]))


class Client:
    last_report = None


class PipelineTests(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp(prefix="signalgate-pipeline-"))
        settings = Settings(_env_file=None, llm_backend="off", sectors_api_enabled=False,
                            signalgate_db_path=str(folder / "pipeline.db"))
        self.session = build_session_factory(settings)()
        self.addCleanup(self.session.close)
        self.snapshots = []

    def run_with(self, events, provider, max_events=3):
        def snapshot(_client, ticker, *_args, **_kwargs):
            self.snapshots.append(ticker)  # tiap panggilan = kredit Sectors
            return None

        with patch.object(orchestrator, "collect_candidate_events", return_value=events), \
             patch.object(orchestrator, "snapshot_company", side_effect=snapshot), \
             patch.object(orchestrator, "FreeFloatLookup"), patch.object(orchestrator, "SubsectorValuationLookup"):
            return orchestrator.run_pipeline(Client(), provider, self.session, max_events)

    def cards(self, ticker):
        return self.session.scalar(select(func.count()).select_from(ScreenedEventRecord)
                                   .where(ScreenedEventRecord.ticker == ticker))

    def test_the_same_news_twice_is_one_card_and_one_research(self):
        """QA 17/09 X4: dulu dua kartu MGLV dan kredit snapshot terpakai dua kali."""
        provider = Provider()
        self.run_with([news("MGLV")], provider)
        second = self.run_with([news("MGLV")], provider)
        self.assertEqual(self.cards("MGLV"), 1)
        self.assertEqual(provider.researched, ["MGLV"])
        self.assertEqual(self.snapshots, ["MGLV"])
        self.assertEqual((len(second.results), second.already_processed), (0, 1))

    def test_settled_news_leaves_room_for_the_next_one(self):
        provider = Provider()
        self.run_with([news("MGLV"), news("HATM")], provider, max_events=1)
        self.run_with([news("MGLV"), news("HATM")], provider, max_events=1)
        self.assertEqual(sorted(provider.researched), ["HATM", "MGLV"])

    def test_an_environment_failure_is_retried_and_updates_the_same_card(self):
        self.run_with([news("APEX")], Provider(status="model_unavailable"))
        retry = Provider()
        self.run_with([news("APEX")], retry)
        self.assertEqual(retry.researched, ["APEX"])
        self.assertEqual(self.cards("APEX"), 1)
        card = self.session.scalars(select(ScreenedEventRecord).where(ScreenedEventRecord.ticker == "APEX")).one()
        self.assertEqual(card.payload["research"]["status"], "completed")

    def test_a_different_article_about_the_same_ticker_is_its_own_card(self):
        provider = Provider()
        self.run_with([news("LAPD", "https://berita.test/lapd-1")], provider)
        self.run_with([news("LAPD", "https://berita.test/lapd-2")], provider)
        self.assertEqual(self.cards("LAPD"), 2)

    def test_cases_are_announced_before_research_with_the_selected_total(self):
        events = []

        class Recording(Provider):
            def research(self_inner, event, *args, **kwargs):
                events.append(("research", event.ticker))
                return super().research(event, *args, **kwargs)

        def publish(stage, ticker, detail=None):
            if stage == "case":
                events.append((detail["phase"], ticker, detail["index"], detail["total"]))

        with patch.object(run_events, "publish", side_effect=publish):
            self.run_with([news("FORU")], Recording(), max_events=3)
        self.assertEqual(events, [("start", "FORU", 1, 1), ("research", "FORU"), ("end", "FORU", 1, 1)])


if __name__ == "__main__":
    unittest.main()

from app.research.documents import Document, Link
import json
import pathlib
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.config import get_settings
from app.pipeline.schema import ActionBucket, CandidateEvent, Verdict, VerdictLabel
from app.research.models import ResearchOutcome
from app.pipeline.presentation import screen_outcome
from app.pipeline.schema import GateStatus
from app.research.documents import as_day, published_date
from app.scan import (KEYWORDS, MAX_ATTEMPTS, PAGE_LINK, PRIORITISED_BUCKET_KEYWORDS, Scanner,
                      announcement_groups, eligible_items, is_due, mark_published, matches,
                      needs_publication, queue_backlog, research_queue_items, write_json)

IDX_LISTING = "https://www.idx.id/en/news/announcement"
NEWS_LISTING = "https://news.test/emiten"
NEWS_ARTICLE = "https://news.test/news/mknt-pmthmetd"


def pdf(name):
    return f"https://www.idx.co.id/StaticData/NewsAndAnnouncement/ANNOUNCEMENTSTOCK/From_EREP/202609/{name}.pdf"


def listing_links():
    return [
        Link(url="https://www.idx.id/en", text=""),
        Link(url=pdf("nav"), text="Daily Report of Net Asset Value and Portfolio Composition [ XMES ]"),
        Link(url=pdf("title"), text="Submission of Affiliate/Conflict of Interest Transaction Information [ LPKR ]"),
        Link(url=pdf("lamp1"), text="20260915_LPKR_Informasi Transaksi Afiliasi_32148755_lamp1.pdf"),
        Link(url=pdf("lamp2"), text="20260915_LPKR_Informasi Transaksi Afiliasi_32148755_lamp2.pdf"),
        Link(url=pdf("mknt"), text="20260915_MKNT_Keterbukaan Informasi PMTHMETD_32148000_lamp1.pdf"),
    ]


def document(url, links=(), text="Teks halaman.", title="Halaman", published_at=""):
    return Document(url=url, title=title, format="html", sha256="a" * 64, fetched_at="2026-09-16T00:00:00+00:00",
                    pages=[{"number": None, "text": text}], links=list(links), published_at=published_at)


class FakeSource:
    def __init__(self, documents):
        self.documents = documents
        self.fetched = []

    def fetch_document(self, url):
        self.fetched.append(url)
        return self.documents[url]


def test_attachments_inherit_ticker_from_announcement_title():
    groups = announcement_groups(listing_links())
    assert [group["ticker"] for group in groups] == ["XMES", "LPKR", "MKNT"]
    lpkr = groups[1]
    assert lpkr["urls"] == [pdf("title"), pdf("lamp1"), pdf("lamp2")]


def test_idx_listing_queues_one_candidate_per_announcement_without_fetching_attachments(tmp_path):
    source = FakeSource({IDX_LISTING: document(IDX_LISTING, listing_links())})
    report = Scanner(source, tmp_path).discover([IDX_LISTING])
    assert sorted(entry["event"]["ticker"] for entry in report["candidates"]) == ["LPKR", "MKNT"]
    lpkr = next(entry for entry in report["candidates"] if entry["event"]["ticker"] == "LPKR")
    assert len(lpkr["pdf_urls"]) == 3
    assert lpkr["ticker_source"] == "idx_announcement_listing"
    assert lpkr["ticker_verified"]
    assert source.fetched == [IDX_LISTING]
    assert report["failures"] == []


def test_news_candidate_without_pdf_uses_same_ticker_idx_documents(tmp_path):
    source = FakeSource({
        IDX_LISTING: document(IDX_LISTING, listing_links()),
        NEWS_LISTING: document(NEWS_LISTING, [Link(url=NEWS_ARTICLE,
                                                   text="Dilusi PMTHMETD 99,46% di MKNT\n12 jam yang lalu")]),
        NEWS_ARTICLE: document(NEWS_ARTICLE, text="PT Mitra Komunikasi Nusantara Tbk (MKNT) menjalankan PMTHMETD.",
                               title="Dilusi PMTHMETD MKNT", published_at="2026-09-15"),
    })
    report = Scanner(source, tmp_path).discover([IDX_LISTING, NEWS_LISTING])
    news = next(entry for entry in report["candidates"] if entry["event"]["source_url"] == NEWS_ARTICLE)
    assert news["event"]["headline"] == "Dilusi PMTHMETD 99,46% di MKNT"
    assert news["event"]["published_at"] == "2026-09-15"
    assert news["status"] == "pending_pdf_review"
    assert news["pdf_urls"] == [pdf("mknt")]
    assert news["documents_from"] == "idx_announcement_same_number"
    assert news["document_numbers"] == ["32148000"]


def test_same_ticker_documents_need_a_shared_keyword(tmp_path):
    unrelated = [Link(url=pdf("buyback"), text="The Withdrawal of Shares Resulting from Share Buyback [ MKNT ]")]
    source = FakeSource({
        IDX_LISTING: document(IDX_LISTING, unrelated),
        NEWS_LISTING: document(NEWS_LISTING, [Link(url=NEWS_ARTICLE, text="Dilusi PMTHMETD 99,46% di MKNT")]),
        NEWS_ARTICLE: document(NEWS_ARTICLE, text="PT Mitra Komunikasi Nusantara Tbk (MKNT) menjalankan PMTHMETD.",
                               title="Dilusi PMTHMETD MKNT"),
    })
    report = Scanner(source, tmp_path).discover([IDX_LISTING, NEWS_LISTING])
    news = next(entry for entry in report["candidates"] if entry["event"]["source_url"] == NEWS_ARTICLE)
    assert news["status"] == "needs_document"


def test_discovery_never_filters_out_an_action_the_classifier_prioritises():
    """scan.KEYWORDS drifted from hypothesize.py and lost 'takeover', so IDX's English
    announcements for takeovers were invisible to discovery even though classify() handled them."""
    missing = [keyword for keyword in PRIORITISED_BUCKET_KEYWORDS if keyword not in KEYWORDS]
    assert missing == []


def test_idx_english_takeover_announcement_is_discovered():
    title = 'Negotiation of the Takeover Plan of PT Idea Indonesia Akademi Tbk [ IDEA ]'
    attachment = '20260916_IDEA_Laporan Informasi dan Fakta Material_32148872_lamp1.pdf'
    assert matches(f'{title} {attachment}')


def test_indonesian_newsroom_verbs_are_discovered():
    assert matches('Nawasena Caplok 66,58 Persen, Saham IDEA Langsung Meroket')
    assert matches('Cimory Suntik Modal Rp125 Miliar ke Anak Usaha')


def test_a_headline_starting_with_a_digit_is_not_a_pagination_link():
    """It used to be: '3 Saham Dipantau BEI' burned one of the three listing-page slots."""
    assert not PAGE_LINK.fullmatch('3 Saham Dipantau BEI: 2 Lanjut Merosot usai UMA')
    assert PAGE_LINK.fullmatch('2') and PAGE_LINK.fullmatch('Next') and PAGE_LINK.fullmatch('Berikutnya \u00bb')


def test_routine_filings_stay_out_of_the_queue():
    """Most of what IDX publishes daily is noise; widening keywords must not drag it in."""
    for title in ('Report of Circulation Number of Participation Units [ XRDN ]',
                  'Report of Rating [ POST ]',
                  'Advertisement Submission [ IDEA ]'):
        assert not matches(title)


class QueueResearchTests(unittest.TestCase):
    """Tiga kandidat dulu menghasilkan satu kasus: yang tanpa PDF dilewati diam-diam."""

    def queue(self, *items):
        directory = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        (directory / "queue").mkdir()
        for index, pdf_urls in enumerate(items):
            event = CandidateEvent(ticker=f"AA{index:02}", headline="Pengambilalihan", body="",
                                   source_url=f"https://berita.test/{index}", published_at="",
                                   bucket=ActionBucket.control_change, matched_keywords=["akuisisi"])
            write_json(directory / "queue" / f"{index:02}.json",
                       {"id": f"{index:02}", "event": event.model_dump(mode="json"), "article_sha256": "x",
                        "first_seen": "", "ticker_source": "article_text", "ticker_verified": False,
                        "pdf_urls": list(pdf_urls), "status": "pending_pdf_review" if pdf_urls else "needs_document"})
        return directory

    def research(self, directory, limit=5):
        calls = []

        class FakeEngine:
            settings = None
            name = "fake"

            def research(self_inner, event, snapshot, require_pdf=False, require_sectors=False):
                calls.append((event.ticker, require_pdf))
                return ResearchOutcome(status="completed", case_id=f"{event.ticker}-1",
                                       verdict=Verdict(label=VerdictLabel.inconclusive, confidence=0.0,
                                                       provider="fake", rationale_bullets=[]))

            def close(self_inner):
                pass

        with patch("app.scan.build_provider", return_value=FakeEngine()):
            researched = [event.ticker for event, _outcome, _item in
                          research_queue_items(get_settings(), directory, limit)]
        return researched, calls

    def test_a_candidate_without_a_pdf_is_researched_from_its_article(self):
        researched, calls = self.research(self.queue([], ["https://idx.test/a.pdf"], []))
        self.assertEqual(researched, ["AA00", "AA01", "AA02"])
        self.assertEqual(dict(calls), {"AA00": False, "AA01": True, "AA02": False})

    def test_a_pdf_is_still_required_to_be_readable_when_one_exists(self):
        _researched, calls = self.research(self.queue(["https://idx.test/a.pdf"]))
        self.assertEqual(calls, [("AA00", True)])

    def test_the_limit_still_caps_the_work(self):
        researched, _calls = self.research(self.queue([], [], [], []), limit=2)
        self.assertEqual(len(researched), 2)


class QueueSchedulingTests(unittest.TestCase):
    """QA 2026-09-16 P1: kandidat gagal menghalangi seluruh antrean berikutnya."""

    def queue_dir(self, *items):
        directory = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        (directory / "queue").mkdir()
        for index, extra in enumerate(items):
            event = CandidateEvent(ticker=f"AA{index:02}", headline="Aksi", body="", published_at="",
                                   source_url=f"https://berita.test/{index}",
                                   bucket=ActionBucket.control_change, matched_keywords=["akuisisi"])
            write_json(directory / "queue" / f"{index:02}.json",
                       {"id": f"{index:02}", "event": event.model_dump(mode="json"), "pdf_urls": [],
                        "status": "pending_pdf_review", **extra})
        return directory

    def tickers(self, directory, retry=False):
        return [item["event"]["ticker"] for _path, item in eligible_items(directory, retry)]

    def test_a_never_tried_candidate_is_served_before_repeat_failures(self):
        directory = self.queue_dir(
            {"status": "insufficient_evidence", "attempt_count": 2, "last_attempt_at": "2026-09-01T00:00:00+00:00"},
            {},  # belum pernah dicoba
            {"status": "model_unavailable", "attempt_count": 1, "last_attempt_at": "2026-09-02T00:00:00+00:00"},
        )
        self.assertEqual(self.tickers(directory)[0], "AA01")

    def test_a_candidate_still_in_backoff_does_not_take_a_slot(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        directory = self.queue_dir({"status": "insufficient_evidence", "attempt_count": 1, "next_retry_at": future})
        self.assertEqual(self.tickers(directory), [])
        self.assertEqual(self.tickers(directory, retry=True), ["AA00"])

    def test_a_candidate_past_its_backoff_comes_back(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        directory = self.queue_dir({"status": "insufficient_evidence", "attempt_count": 1, "next_retry_at": past})
        self.assertEqual(self.tickers(directory), ["AA00"])

    def test_retries_stop_after_the_cap_so_a_broken_source_cannot_loop_forever(self):
        directory = self.queue_dir({"status": "needs_document", "attempt_count": MAX_ATTEMPTS})
        self.assertEqual(self.tickers(directory), [])

    def test_a_published_verdict_is_never_researched_again(self):
        directory = self.queue_dir({"status": "completed", "published_at": "2026-09-16T00:00:00+00:00"})
        self.assertEqual(self.tickers(directory), [])

    def test_a_corrupt_queue_file_is_skipped_rather_than_stopping_the_run(self):
        directory = self.queue_dir({})
        (directory / "queue" / "99.json").write_text("{bukan json", encoding="utf-8")
        self.assertEqual(self.tickers(directory), ["AA00"])

    def test_is_due_treats_an_unreadable_schedule_as_due(self):
        self.assertTrue(is_due({"next_retry_at": "bukan tanggal"}))


class PublicationRecoveryTests(unittest.TestCase):
    """QA 2026-09-16 P1: hasil selesai diriset tetapi tidak pernah muncul di dashboard."""

    def setUp(self):
        self.directory = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        (self.directory / "queue").mkdir()
        event = CandidateEvent(ticker="IDEA", headline="Pengambilalihan", body="", published_at="",
                               source_url="https://berita.test/x", bucket=ActionBucket.control_change,
                               matched_keywords=["akuisisi"])
        write_json(self.directory / "queue" / "00.json",
                   {"id": "00", "event": event.model_dump(mode="json"), "pdf_urls": [],
                    "status": "completed", "case_id": "IDEA-abc"})

    def write_artifact(self, settings):
        case = pathlib.Path(settings.research_cases_dir) / "IDEA-abc"
        case.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, case, ignore_errors=True)
        outcome = ResearchOutcome(status="completed", case_id="IDEA-abc",
                                  verdict=Verdict(label=VerdictLabel.inconclusive, confidence=0.0,
                                                  provider="ollama:asli", rationale_bullets=["dari artefak"]))
        (case / "decision.json").write_text(outcome.model_dump_json(), encoding="utf-8")

    def test_an_unpublished_verdict_is_recovered_without_calling_the_model(self):
        settings = get_settings()
        self.write_artifact(settings)
        with patch("app.scan.build_provider", side_effect=AssertionError("model dipanggil ulang")):
            results = list(research_queue_items(settings, self.directory, 3))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][1].verdict.provider, "ollama:asli")

    def test_once_published_it_is_left_alone(self):
        settings = get_settings()
        self.write_artifact(settings)
        mark_published(self.directory, "00", "IDEA-abc")
        with patch("app.scan.build_provider", side_effect=AssertionError("model dipanggil ulang")):
            self.assertEqual(list(research_queue_items(settings, self.directory, 3)), [])

    def test_mark_published_stamps_the_queue_file(self):
        item = mark_published(self.directory, "00", "IDEA-abc")
        self.assertIn("published_at", item)
        stored = json.loads((self.directory / "queue" / "00.json").read_text())
        self.assertEqual(stored["published_at"], item["published_at"])
        self.assertEqual(stored["published_case_id"], "IDEA-abc")

    def test_a_publication_stamp_only_covers_the_verdict_it_published(self):
        item = {"status": "completed", "case_id": "IDEA-baru", "published_at": "2026-09-16T00:00:00+00:00",
                "published_case_id": "IDEA-lama"}
        self.assertTrue(needs_publication(item))
        self.assertFalse(needs_publication({**item, "published_case_id": "IDEA-baru"}))
        self.assertFalse(needs_publication({**item, "status": "model_unavailable"}))

    def test_a_missing_artifact_falls_back_to_researching_again(self):
        settings = get_settings()  # tanpa write_artifact
        calls = []

        class FakeEngine:
            settings = None
            def research(self_inner, event, *_a, **_k):
                calls.append(event.ticker)
                return ResearchOutcome(status="completed", case_id="IDEA-baru",
                                       verdict=Verdict(label=VerdictLabel.inconclusive, confidence=0.0,
                                                       provider="fake", rationale_bullets=[]))
            def close(self_inner):
                pass

        with patch("app.scan.build_provider", return_value=FakeEngine()):
            list(research_queue_items(settings, self.directory, 3))
        self.assertEqual(calls, ["IDEA"])


class DocumentAssociationTests(unittest.TestCase):
    """QA 2026-09-16/17 P1: PDF dari aksi berbeda masuk ke bukti kasus yang sama."""

    def attach(self, *groups, published_at="2026-09-16"):
        directory = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        (directory / "queue").mkdir()
        report = {"candidates": [{"id": "artikel", "status": "needs_document",
                                  "event": {"ticker": "TEST", "matched_keywords": ["rights issue"],
                                            "published_at": published_at}}]}
        Scanner(None, directory).attach_announcement_documents(
            report, [(group, ["rights issue"], "idx.co.id") for group in groups])
        return report["candidates"][0]

    def group(self, date, url, number=None):
        name = f"{date}_TEST_HMETD_{number}_lamp1.pdf" if number else f"{date}_TEST_HMETD.pdf"
        return {"ticker": "TEST", "urls": [url], "attachments": [name],
                "dates": {date} if date else set(), "numbers": {number} if number else set()}

    def test_a_lone_announcement_from_another_year_is_not_attached(self):
        """Retest 17/09 reproduksi A: satu-satunya grup bukan bukti bahwa grup itu milik artikel."""
        entry = self.attach(self.group("20240110", "https://idx.test/2024.pdf", "31000001"),
                            published_at="2026-09-17")
        self.assertEqual(entry["status"], "needs_document")
        self.assertNotIn("pdf_urls", entry)
        self.assertEqual(entry["documents_rejected"][0]["dates"], ["20240110"])

    def test_the_other_years_announcement_is_rejected_and_the_matching_one_attached(self):
        entry = self.attach(self.group("20240110", "https://idx.test/2024.pdf", "31000001"),
                            self.group("20260916", "https://idx.test/2026.pdf", "32148872"))
        self.assertEqual(entry["status"], "pending_pdf_review")
        self.assertEqual(entry["pdf_urls"], ["https://idx.test/2026.pdf"])
        self.assertEqual(entry["document_numbers"], ["32148872"])
        self.assertEqual([item["dates"] for item in entry["documents_rejected"]], [["20240110"]])

    def test_without_an_article_date_nothing_is_attached(self):
        entry = self.attach(self.group("20260916", "https://idx.test/a.pdf", "32148872"), published_at="")
        self.assertEqual(entry["status"], "ambiguous_documents")
        self.assertNotIn("pdf_urls", entry)
        self.assertIn("Tanggal terbit artikel tidak diketahui", entry["document_review"])
        self.assertEqual(entry["candidate_documents"], ["https://idx.test/a.pdf"])

    def test_the_article_date_is_never_overwritten_by_the_attachment_date(self):
        """Mengisi tanggal artikel dari PDF membuat lampiran yang salah selalu tampak cocok."""
        group = self.group("20260916", "https://idx.test/a.pdf")
        group["urls"].append("https://idx.test/b.pdf")
        entry = self.attach(group, published_at="2026-09-18")
        self.assertEqual(entry["status"], "pending_pdf_review")
        self.assertEqual(len(entry["pdf_urls"]), 2)
        self.assertEqual(entry["event"]["published_at"], "2026-09-18")
        self.assertEqual(entry["document_dates"], ["20260916"])

    def test_two_announcements_on_the_same_date_are_still_different_actions(self):
        """Kasus nyata IDEA 16/09/2026: 32148872 fakta material, 32148902 pengumuman RUPS."""
        entry = self.attach(self.group("20260916", "https://idx.test/fakta.pdf", "32148872"),
                            self.group("20260916", "https://idx.test/rups.pdf", "32148902"))
        self.assertEqual(entry["status"], "ambiguous_documents")
        self.assertEqual(entry["document_dates"], ["20260916"])  # tanggalnya sama
        self.assertEqual(entry["document_numbers"], ["32148872", "32148902"])  # aksinya tidak
        self.assertNotIn("pdf_urls", entry)

    def test_groups_sharing_one_announcement_number_are_the_same_action(self):
        entry = self.attach(self.group("20260916", "https://idx.test/a.pdf", "32148872"),
                            self.group("20260916", "https://idx.test/b.pdf", "32148872"))
        self.assertEqual(entry["status"], "pending_pdf_review")
        self.assertEqual(len(entry["pdf_urls"]), 2)
        self.assertEqual(entry["documents_from"], "idx_announcement_same_number")

    def test_a_numbered_and_an_unnumbered_group_are_not_one_action(self):
        """QA 17/09 X2: satu nomor dari dua grup dulu terbaca 'nomornya sama'."""
        entry = self.attach(self.group("20260916", "https://idx.test/a.pdf", "32148872"),
                            self.group("20260915", "https://idx.test/b.pdf"))
        self.assertEqual(entry["status"], "ambiguous_documents")
        self.assertEqual(entry["document_candidates"], 2)

    def test_an_undated_group_cannot_be_tied_to_the_article(self):
        undated = {"ticker": "TEST", "urls": ["https://idx.test/sampul.pdf"], "attachments": [],
                   "dates": set(), "numbers": set()}
        entry = self.attach(self.group("20240110", "https://idx.test/2024.pdf", "31000001"), undated)
        self.assertEqual(entry["status"], "ambiguous_documents")
        self.assertEqual(entry["candidate_documents"], ["https://idx.test/sampul.pdf"])
        self.assertIn("tidak bertanggal", entry["document_review"])

    def test_groups_without_a_readable_number_are_refused(self):
        """Tanpa nomor pengumuman, kesamaan aksinya tidak terbukti walau tanggalnya sama."""
        entry = self.attach(self.group("20260916", "https://idx.test/a.pdf"),
                            self.group("20260916", "https://idx.test/b.pdf"))
        self.assertEqual(entry["status"], "ambiguous_documents")
        self.assertEqual(entry["document_candidates"], 2)

    def test_a_lone_group_is_attached_even_without_a_number(self):
        """Lampiran satu pengumuman memang milik aksi yang sama; tidak ada yang perlu dibuktikan."""
        entry = self.attach(self.group("20260916", "https://idx.test/a.pdf"))
        self.assertEqual(entry["status"], "pending_pdf_review")
        self.assertEqual(entry["documents_from"], "idx_announcement_single_group")

    def test_announcement_groups_carry_the_date_and_number_from_the_attachment_name(self):
        links = [SimpleNamespace(text="Rencana Pengambilalihan [ IDEA ]", url="https://idx.test/a"),
                 SimpleNamespace(text="20260916_IDEA_Laporan Informasi dan Fakta Material_32148872_lamp1.pdf",
                                 url="https://idx.test/a.pdf"),
                 SimpleNamespace(text="20260916_IDEA_Laporan Informasi dan Fakta Material_32148872_lamp2.pdf",
                                 url="https://idx.test/b.pdf")]
        group = announcement_groups(links)[0]
        self.assertEqual(group["dates"], {"20260916"})
        self.assertEqual(group["numbers"], {"32148872"})  # dua lampiran, satu aksi


class RetryPublicationTests(unittest.TestCase):
    """QA 2026-09-17 P1: putusan hasil retry hilang karena penanda terbit percobaan lama."""

    def setUp(self):
        root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.directory = root / "scan"
        self.settings = get_settings().model_copy(update={"research_cases_dir": root / "cases"})
        event = CandidateEvent(ticker="TEST", headline="Rights issue", body="", published_at="2026-09-17",
                               source_url="https://berita.test/r", bucket=ActionBucket.rights_issue,
                               matched_keywords=["rights issue"])
        write_json(self.directory / "queue" / "00.json",
                   {"id": "00", "event": event.model_dump(mode="json"), "pdf_urls": [], "status": "needs_document"})
        self.engine = self.fake_engine()

    def fake_engine(self):
        test = self

        class Engine:
            settings = None
            status, label, calls = "completed", VerdictLabel.inconclusive, 0

            def research(self_inner, event, *_a, **_k):
                self_inner.calls += 1
                outcome = ResearchOutcome(status=self_inner.status, case_id=f"TEST-{self_inner.calls}",
                                          verdict=Verdict(label=self_inner.label, confidence=0.0,
                                                          provider="fake", rationale_bullets=[]))
                case = pathlib.Path(test.settings.research_cases_dir) / outcome.case_id
                case.mkdir(parents=True, exist_ok=True)
                (case / "decision.json").write_text(outcome.model_dump_json(), encoding="utf-8")
                return outcome

            def close(self_inner):
                pass

        return Engine()

    def publish_all(self, **kwargs):
        """Seperti rute /scan/run: kartu tersimpan, baru ditandai terbit."""
        results = list(research_queue_items(self.settings, self.directory, 1, **kwargs))
        for _event, _outcome, item in results:
            mark_published(self.directory, item["id"], item["case_id"])
        return results

    def expire_backoff(self):
        path = self.directory / "queue" / "00.json"
        item = json.loads(path.read_text())
        item["next_retry_at"] = "2020-01-01T00:00:00+00:00"
        write_json(path, item)

    def test_a_retry_that_fails_to_publish_is_recovered_without_inference(self):
        with patch("app.scan.build_provider", return_value=self.engine):
            self.engine.status = "model_unavailable"
            self.publish_all()  # kartu gagal terbit
            self.expire_backoff()
            self.engine.status = "completed"
            unpublished = list(research_queue_items(self.settings, self.directory, 1))  # commit gagal
            recovered = list(research_queue_items(self.settings, self.directory, 1))
        self.assertEqual(unpublished[0][1].case_id, "TEST-2")
        self.assertEqual([outcome.case_id for _e, outcome, _i in recovered], ["TEST-2"])
        self.assertEqual(self.engine.calls, 2, "pemulihan tidak boleh memanggil model lagi")

    def test_a_cli_retry_reaches_the_dashboard_on_the_next_scan(self):
        """QA 17/09 X3: `python -m app.scan research --retry` dulu tidak pernah terbit."""
        with patch("app.scan.build_provider", return_value=self.engine):
            self.publish_all()
            self.engine.label = VerdictLabel.structural_red_flag
            list(research_queue_items(self.settings, self.directory, 1, retry=True))  # CLI tidak menerbitkan
            recovered = self.publish_all()
            after = list(research_queue_items(self.settings, self.directory, 1))
        self.assertEqual([outcome.verdict.label for _e, outcome, _i in recovered], [VerdictLabel.structural_red_flag])
        self.assertEqual(after, [])
        self.assertEqual(self.engine.calls, 2)


class AmbiguousDocumentReviewTests(unittest.TestCase):
    """QA 2026-09-17: status ambigu antrean dulu hilang dan kartunya lolos gate."""

    REASON = "2 pengumuman IDX cocok dengan artikel ini; pilih dokumen yang benar secara manual."

    def setUp(self):
        root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.directory = root / "scan"
        self.settings = get_settings().model_copy(update={"research_cases_dir": root / "cases"})
        self.event = CandidateEvent(ticker="TEST", headline="Rights issue", body="", published_at="2026-09-17",
                                    source_url="https://berita.test/a", bucket=ActionBucket.rights_issue,
                                    matched_keywords=["rights issue"])
        write_json(self.directory / "queue" / "00.json",
                   {"id": "00", "event": self.event.model_dump(mode="json"), "pdf_urls": [],
                    "status": "ambiguous_documents", "document_review": self.REASON})

    def research(self, **kwargs):
        settings = self.settings

        class Engine:
            def research(self_inner, event, *_a, **_k):
                outcome = ResearchOutcome(status="completed", case_id="TEST-1",
                                          verdict=Verdict(label=VerdictLabel.inconclusive, confidence=0.0,
                                                          provider="fake", rationale_bullets=[]))
                case = pathlib.Path(settings.research_cases_dir) / "TEST-1"
                case.mkdir(parents=True, exist_ok=True)
                (case / "decision.json").write_text(outcome.model_dump_json(), encoding="utf-8")
                return outcome

            def close(self_inner):
                pass

        with patch("app.scan.build_provider", return_value=Engine()):
            return list(research_queue_items(self.settings, self.directory, 1, **kwargs))

    def test_the_review_reason_survives_research_and_holds_the_gate(self):
        event, outcome, item = self.research()[0]
        self.assertEqual(outcome.status, "needs_review")
        self.assertIn(self.REASON, outcome.issues)
        self.assertEqual(item["status"], "needs_review")
        self.assertEqual(screen_outcome(event, None, outcome).gate.status, GateStatus.needs_review)

    def test_recovery_from_the_artifact_keeps_the_reason(self):
        self.research()  # hasil tidak pernah terbit
        with patch("app.scan.build_provider", side_effect=AssertionError("model dipanggil ulang")):
            _event, recovered, _item = list(research_queue_items(self.settings, self.directory, 1))[0]
        self.assertEqual(recovered.issues.count(self.REASON), 1)
        self.assertEqual(recovered.status, "needs_review")


class QueueProgressTests(unittest.TestCase):
    """QA 2026-09-16/17 P2: nomor kasus terbit setelah model selesai, dan total = limit."""

    def test_each_case_is_announced_before_the_model_reads_it_with_the_real_total(self):
        directory = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        for index in range(2):
            event = CandidateEvent(ticker=f"AA{index:02}", headline="Aksi", body="", published_at="",
                                   source_url=f"https://berita.test/{index}", bucket=ActionBucket.control_change,
                                   matched_keywords=["akuisisi"])
            write_json(directory / "queue" / f"{index:02}.json",
                       {"id": f"{index:02}", "event": event.model_dump(mode="json"), "pdf_urls": [],
                        "status": "needs_document"})
        timeline = []

        class Engine:
            settings = None

            def research(self_inner, event, *_a, **_k):
                timeline.append(("research", event.ticker))
                return ResearchOutcome(status="insufficient_evidence", case_id=f"{event.ticker}-1",
                                       verdict=Verdict(label=VerdictLabel.inconclusive, confidence=0.0,
                                                       provider="fake", rationale_bullets=[]))

            def close(self_inner):
                pass

        with patch("app.scan.build_provider", return_value=Engine()):
            list(research_queue_items(get_settings(), directory, 3,
                                      on_start=lambda event, index, total:
                                      timeline.append(("start", event.ticker, index, total))))
        self.assertEqual(timeline, [("start", "AA00", 1, 2), ("research", "AA00"),
                                    ("start", "AA01", 2, 2), ("research", "AA01")])


class QueueBacklogTests(unittest.TestCase):
    """QA 2026-09-17 P2: kandidat dalam jeda retry dilaporkan sebagai pending 0 tanpa penjelasan."""

    def test_waiting_and_exhausted_candidates_are_reported_separately(self):
        directory = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        soon = datetime.now(timezone.utc) + timedelta(minutes=5)
        later = soon + timedelta(hours=1)
        items = [{"status": "model_unavailable", "attempt_count": 1, "next_retry_at": later.isoformat()},
                 {"status": "model_unavailable", "attempt_count": 2, "next_retry_at": soon.isoformat()},
                 {"status": "insufficient_evidence", "attempt_count": MAX_ATTEMPTS, "next_retry_at": None},
                 {"status": "completed", "attempt_count": 1, "next_retry_at": None, "case_id": "X",
                  "published_at": "2026-09-16T00:00:00+00:00", "published_case_id": "X"}]
        for index, extra in enumerate(items):
            write_json(directory / "queue" / f"{index:02}.json", {"id": f"{index:02}", "event": {}, **extra})
        backlog = queue_backlog(directory)
        self.assertEqual((backlog["retry_waiting"], backlog["retry_exhausted"]), (2, 1))
        self.assertEqual(datetime.fromisoformat(backlog["next_retry_at"]), soon)
        self.assertEqual(eligible_items(directory), [])


class PublishedDateTests(unittest.TestCase):
    """Tanggal terbit artikel adalah jangkar yang mengikat lampiran IDX ke aksi yang benar."""

    def test_structured_metadata_is_read(self):
        self.assertEqual(published_date('<script>{"datePublished": "2026-09-14T10:17:14+07:00"}</script>'),
                         "2026-09-14")
        self.assertEqual(published_date('<meta property="article:published_time" content="2026-08-20T12:00:56+07:00">'),
                         "2026-08-20")
        self.assertEqual(published_date('<meta content="2026/09/14 08:45:14" name="publishdate" />'), "2026-09-14")

    def test_emitennews_time_posted_is_day_first(self):
        self.assertEqual(published_date('<span class="time-posted">12/09/2026, 12:45 WIB</span>'), "2026-09-12")

    def test_the_url_path_is_a_fallback(self):
        self.assertEqual(published_date("<html></html>", "https://market.bisnis.com/read/20260820/192/1997696/x"),
                         "2026-08-20")
        self.assertEqual(published_date("<html></html>", "https://news.test/2026/09/16/aksi"), "2026-09-16")

    def test_unknown_or_impossible_dates_stay_empty(self):
        self.assertEqual(published_date("<p>Tanpa tanggal</p>", "https://news.test/emiten/9"), "")
        self.assertEqual(as_day("31/02/2026"), "")

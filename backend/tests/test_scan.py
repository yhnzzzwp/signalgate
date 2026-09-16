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
from app.scan import (KEYWORDS, MAX_ATTEMPTS, PAGE_LINK, PRIORITISED_BUCKET_KEYWORDS, Scanner,
                      announcement_groups, eligible_items, is_due, mark_published, matches,
                      research_queue_items, write_json)

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


def document(url, links=(), text="Teks halaman.", title="Halaman"):
    return Document(url=url, title=title, format="html", sha256="a" * 64, fetched_at="2026-09-16T00:00:00+00:00",
                    pages=[{"number": None, "text": text}], links=list(links))


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
                               title="Dilusi PMTHMETD MKNT"),
    })
    report = Scanner(source, tmp_path).discover([IDX_LISTING, NEWS_LISTING])
    news = next(entry for entry in report["candidates"] if entry["event"]["source_url"] == NEWS_ARTICLE)
    assert news["event"]["headline"] == "Dilusi PMTHMETD 99,46% di MKNT"
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
        mark_published(self.directory, "00")
        with patch("app.scan.build_provider", side_effect=AssertionError("model dipanggil ulang")):
            self.assertEqual(list(research_queue_items(settings, self.directory, 3)), [])

    def test_mark_published_stamps_the_queue_file(self):
        item = mark_published(self.directory, "00")
        self.assertIn("published_at", item)
        stored = json.loads((self.directory / "queue" / "00.json").read_text())
        self.assertEqual(stored["published_at"], item["published_at"])

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
    """QA 2026-09-16 P1: PDF dari aksi berbeda masuk ke bukti kasus yang sama."""

    def attach(self, *groups):
        directory = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        (directory / "queue").mkdir()
        report = {"candidates": [{"id": "artikel", "status": "needs_document",
                                  "event": {"ticker": "TEST", "matched_keywords": ["rights issue"],
                                            "published_at": ""}}]}
        Scanner(None, directory).attach_announcement_documents(
            report, [(group, ["rights issue"], "idx.co.id") for group in groups])
        return report["candidates"][0]

    def group(self, date, url, number=None):
        name = f"{date}_TEST_HMETD_{number}_lamp1.pdf" if number else f"{date}_TEST_HMETD.pdf"
        return {"ticker": "TEST", "urls": [url], "attachments": [name],
                "dates": {date} if date else set(), "numbers": {number} if number else set()}

    def test_two_rights_issues_from_different_years_are_not_merged(self):
        entry = self.attach(self.group("20240110", "https://idx.test/2024.pdf", "31000001"),
                            self.group("20260916", "https://idx.test/2026.pdf", "32148872"))
        self.assertEqual(entry["status"], "ambiguous_documents")
        self.assertEqual(entry["document_numbers"], ["31000001", "32148872"])
        self.assertNotIn("pdf_urls", entry)

    def test_two_announcements_on_the_same_date_are_still_different_actions(self):
        """Kasus nyata IDEA 16/09/2026: 32148872 fakta material, 32148902 pengumuman RUPS."""
        entry = self.attach(self.group("20260916", "https://idx.test/fakta.pdf", "32148872"),
                            self.group("20260916", "https://idx.test/rups.pdf", "32148902"))
        self.assertEqual(entry["status"], "ambiguous_documents")
        self.assertEqual(entry["document_dates"], ["20260916"])  # tanggalnya sama
        self.assertEqual(entry["document_numbers"], ["32148872", "32148902"])  # aksinya tidak

    def test_attachments_from_one_announcement_are_attached_with_its_date(self):
        group = self.group("20260916", "https://idx.test/a.pdf")
        group["urls"].append("https://idx.test/b.pdf")
        entry = self.attach(group)
        self.assertEqual(entry["status"], "pending_pdf_review")
        self.assertEqual(len(entry["pdf_urls"]), 2)
        self.assertEqual(entry["event"]["published_at"], "2026-09-16")

    def test_groups_sharing_one_announcement_number_are_the_same_action(self):
        entry = self.attach(self.group("20260916", "https://idx.test/a.pdf", "32148872"),
                            self.group("20260916", "https://idx.test/b.pdf", "32148872"))
        self.assertEqual(entry["status"], "pending_pdf_review")
        self.assertEqual(len(entry["pdf_urls"]), 2)
        self.assertEqual(entry["documents_from"], "idx_announcement_same_number")

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

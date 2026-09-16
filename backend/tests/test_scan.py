from app.research.documents import Document, Link
from app.scan import KEYWORDS, PAGE_LINK, PRIORITISED_BUCKET_KEYWORDS, Scanner, announcement_groups, matches

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
    assert news["documents_from"] == "idx_announcement_same_ticker"


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

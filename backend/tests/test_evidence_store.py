import json

from app.research.documents import Document
from app.research.evidence import EvidenceStore

LAMP_ID = "https://www.idx.co.id/From_EREP/202609/lamp-id.pdf"
LAMP_EN = "https://www.idx.co.id/From_EREP/202609/lamp-en.pdf"
OPINION = "https://www.idx.co.id/From_EREP/202609/opinion.pdf"
EXTRA = "https://www.idx.co.id/From_EREP/202609/extra.pdf"


def pdf(url, digest, text):
    return Document(url=url, title=url.rsplit("/", 1)[-1], format="pdf", sha256=digest * 64,
                    fetched_at="2026-09-16T00:00:00+00:00", pages=[{"number": 1, "text": text}])


class FakeLibrary:
    def __init__(self):
        self.documents = {
            LAMP_ID: pdf(LAMP_ID, "a", "Keterbukaan informasi transaksi afiliasi."),
            LAMP_EN: pdf(LAMP_EN, "a", "Keterbukaan informasi transaksi afiliasi."),
            OPINION: pdf(OPINION, "b", "Laporan pendapat kewajaran."),
            EXTRA: pdf(EXTRA, "c", "Laporan penilaian."),
        }

    def fetch_document(self, url):
        return self.documents[url]


def test_identical_documents_are_kept_once_and_do_not_use_the_page_budget(tmp_path):
    store = EvidenceStore(tmp_path, max_pages=2, scraper=FakeLibrary())
    store.collect([LAMP_ID, LAMP_EN, OPINION, EXTRA])
    assert [item.url for item in store.items] == [f"{LAMP_ID}#page=1", f"{OPINION}#page=1"]
    assert store.duplicates == [{"url": LAMP_EN, "same_as": LAMP_ID}]
    assert store.failures == [{"url": EXTRA, "error": "Batas halaman tercapai."}]
    saved = json.loads((tmp_path / "evidence" / "duplicate_documents.json").read_text())
    assert saved == store.duplicates

import json

from app.config import Settings
from app.pipeline.schema import ActionBucket, CandidateEvent
from app.research.documents import Document
from app.research.engine import ResearchEngine, idx_form_fields

COVER = "https://www.idx.co.id/From_EREP/202609/cover.pdf"
OPINION = "https://www.idx.co.id/From_EREP/202609/opinion.pdf"
SCANNED = "https://www.idx.co.id/From_EREP/202609/scanned.pdf"
OTHER = "https://www.idx.co.id/From_EREP/202609/other.pdf"
SPARSE = "Halaman {} minim teks; periksa halaman kosong atau kebutuhan OCR."


def cover_form(code="LPKR", name="Lippo Karawaci Tbk"):
    return ("Nomor Surat\nNama Perusahaan\nKode Emiten\nLampiran\nPerihal\n070/LK-COS/VII/2026\n"
            f"{name}\n{code}\n4\nTransaksi Afiliasi\nGo To English Page\n"
            "Perseroan menyampaikan informasi tentang Transaksi Afiliasi sebagai berikut :")


def pdf(url, texts, digest, warnings=()):
    return Document(url=url, title=url.rsplit("/", 1)[-1], format="pdf", sha256=digest * 64,
                    fetched_at="2026-09-16T00:00:00+00:00", warnings=list(warnings),
                    pages=[{"number": number, "text": text} for number, text in enumerate(texts, 1)])


class FakeLibrary:
    def __init__(self, cover_code="LPKR"):
        body = "Transaksi afiliasi berupa penandatanganan perjanjian cessie antara anak perusahaan. " * 3
        self.documents = {
            COVER: pdf(COVER, [cover_form(cover_code), body], "a"),
            OPINION: pdf(OPINION, ["LAPORAN PENDAPAT KEWAJARAN PT LIPPO KARAWACI Tbk", *[body] * 6, "."], "b",
                         [SPARSE.format(8)]),
            OTHER: pdf(OTHER, ["KETERBUKAAN INFORMASI PT BANK CONTOH Tbk", body], "c"),
            SCANNED: pdf(SCANNED, ["PT LIPPO KARAWACI TBK", ".", "."], "d", [SPARSE.format(2), SPARSE.format(3)]),
        }

    def fetch_document(self, url):
        return self.documents[url]


def research(tmp_path, sources, cover_code="LPKR"):
    settings = Settings(_env_file=None, research_cases_dir=tmp_path, research_source_urls=sources,
                        research_max_pages=4, llm_backend="off")
    event = CandidateEvent(ticker="LPKR", headline="Submission of Affiliate Transaction Information [ LPKR ]",
                           body="", source_url=COVER, published_at="", bucket=ActionBucket.general_action,
                           matched_keywords=["affiliate"])
    engine = ResearchEngine(settings, scraper=FakeLibrary(cover_code))
    outcome = engine.research(event, None, require_pdf=True, require_sectors=False, use_cache=False)
    selection = json.loads((tmp_path / outcome.case_id / "evidence_selection.json").read_text())
    return outcome, selection


def test_idx_cover_form_labels_are_paired_with_values_in_order():
    fields = idx_form_fields(cover_form())
    assert fields["Kode Emiten"] == "LPKR"
    assert fields["Nama Perusahaan"] == "Lippo Karawaci Tbk"
    assert idx_form_fields("Kode Emiten\nLPKR") == {}
    english = ("Letter / Announcement No.\nIssuer Name\nIssuer Code\nAttachment\nSubject\n070/LK-COS/VII/2026\n"
               "Lippo Karawaci Tbk\nLPKR\n4\nAffiliate Transactions\nGo To Indonesian Page")
    assert idx_form_fields(english)["Kode Emiten"] == "LPKR"
    assert idx_form_fields(english)["Nama Perusahaan"] == "Lippo Karawaci Tbk"


def test_cover_form_company_name_links_attachments_without_ticker(tmp_path):
    outcome, selection = research(tmp_path, [OPINION, OTHER])
    assert outcome.document_status == "ready_text"
    assert selection["matched_pdf_urls"] == sorted([COVER, OPINION])
    assert selection["company_name"] == "Lippo Karawaci Tbk"
    assert selection["company_name_source"] == "idx_eform_kode_emiten"


def test_cover_form_for_another_ticker_is_not_trusted(tmp_path):
    outcome, selection = research(tmp_path, [OPINION], cover_code="BNII")
    assert outcome.document_status == "pdf_missing_or_unrelated"
    assert selection["company_name"] is None


def test_mostly_blank_document_still_requires_ocr_review(tmp_path):
    outcome, selection = research(tmp_path, [SCANNED])
    assert outcome.document_status == "needs_ocr_or_review"
    assert selection["needs_ocr_urls"] == [SCANNED]

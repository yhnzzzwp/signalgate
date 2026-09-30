"""Regresi dari docs/AUDIT_HASIL_ANALISIS_2026-09-28.md: SRAJ, EPAC, KETR, PART, SEMA.

Setiap test memakai kutipan asli dari kasusnya, bukan contoh buatan, supaya kegagalan yang sama
tidak bisa kembali lewat bentuk kalimat yang sedikit berbeda.
"""
import hashlib
from types import SimpleNamespace

import httpx
import pytest
from scrapling.parser import Selector

from app.frontier.client import DeepSeekClient, FrontierCallError
from app.pipeline.hypothesize import classify
from app.pipeline.presentation import screen_outcome
from app.pipeline.schema import ActionBucket, GateStatus, Verdict, VerdictLabel
from app.research.documents import DocumentSource
from app.research.engine import ResearchEngine, document_issues, issuer_name_in
from app.research.evidence import HTML_EXTRACTOR_VERSION, main_text
from app.research.facts import compare_independent_facts, party_name_issue, short_use_hints, verified_facts
from app.research.models import Evidence, Extraction, Fact, ResearchOutcome
from app.research.scoring import debt_only_signals
from app.config import Settings
from app.frontier.service import FrontierService
from tests.frontier_fakes import FAKE_KEY, ScriptedClient, completion, frontier_settings, review_json, run_review, service
from tests.test_model_rotation import READERS, RotatingModel
from tests.test_research_engine import ARTICLE, SOURCE_URL, FakeScraper, extraction, make_event

# --- kutipan asli -------------------------------------------------------------------------------------

EPAC_USES = ("Ia juga menerangkan, dan hasil PMHMETD I direncanakan untuk tiga kebutuhan belanja modal bagi "
             "pengembangan usaha Perseroan, pengurangan sebagian utang terutama utang bank, dan modal kerja.")
SRAJ_LEAD = ("PT Sejahteraraya Anugrahjaya Tbk. (SRAJ) emiten asal Mayapada Group asuhan Dato' Sri Tahir "
             "mengumumkan telah menyuntikkan modal senilai total sekitar Rp265,6 miliar kepada 5 anak usahanya.")
SRAJ_PURPOSE = ("SRAJ menjelaskan transaksi tersebut ditujukan untuk mendukung pertumbuhan dan memperkuat struktur "
                "permodalan anak-anak usaha tersebut. Perseroan tidak merinci bidang usaha maupun rencana "
                "penggunaan dananya secara spesifik.")
INDEX_STRIP = "\n".join(f"<span>{code}</span><span>-1.0%</span>" for code in ("IDXINDUST", "LQ45", "IDX30") * 40)
SRAJ_HTML = f"""<html><head><title>SRAJ</title></head><body>
<div class="content-index-header">{INDEX_STRIP}</div>
<div class="body-content">
  <div class="article-header"><h1>Emiten Sri Tahir (SRAJ) Suntik Modal 5 Entitas Bisnisnya Rp265,6M</h1>
    <span>Author: Penulis</span><span class="time-posted">28/09/2026, 10:12 WIB</span></div>
  <div class="article-body"><p>EmitenNews.com - {SRAJ_LEAD}</p><p>{SRAJ_PURPOSE}</p>
    <div class="pagination-detail"><a>1</a><a>2</a><a>&gt;</a></div></div>
  <div><h3>Related News</h3><a href="/news/part">PART Mau Gelar Rights Issue dan Buyback, Simak Rinciannya!</a></div>
  <div><h3>Trending</h3><a href="/news/x">Emiten lain umumkan HMETD</a></div>
</div></body></html>"""


def fact(value, quote=EPAC_USES, topic="use_of_funds", claim="", fact_id="F01", evidence_id="E002"):
    return Fact(id=fact_id, topic=topic, value=value, claim=claim, quote=quote, evidence_id=evidence_id)


def page(text, evidence_id="E002"):
    return Evidence(id=evidence_id, kind="scrapling", url="https://news.test/a", title="a", text=text, links=[],
                    retrieved_at="2026-09-28T00:00:00+00:00", sha256="0" * 64)


NO_FLAG = {"evidence_id": "", "quote": "", "present": False}


def extraction_with(uses=(), parties=(), **flags):
    return Extraction.model_validate({
        "action_type": "rights_issue",
        "counterparties": [{"evidence_id": "E002", "quote": quote, "name": name, "relation": relation}
                           for name, relation, quote in parties],
        "use_of_funds": [{"evidence_id": "E002", "quote": quote, "category": category} for category, quote in uses],
        **{topic: flags.get(topic, NO_FLAG) for topic in ("business_change", "old_business_divested", "asset_injection")},
    })


# --- 2. teks halaman: artikel utama, bukan indeks dan berita terkait ----------------------------------

def test_sidebar_rights_issue_headline_does_not_classify_the_sraj_article():
    text = main_text(Selector(SRAJ_HTML))
    assert SRAJ_LEAD in text and "Rights Issue" not in text and "HMETD" not in text
    assert "IDXINDUST" not in text and not text.rstrip().endswith(">")
    bucket, _ = classify("Emiten Sri Tahir (SRAJ) Suntik Modal 5 Entitas Bisnisnya Rp265,6M", text)
    assert bucket != ActionBucket.rights_issue


def test_candidate_body_starts_with_the_article_not_the_ticker_strip():
    body = main_text(Selector(SRAJ_HTML))[:1500]
    assert body.startswith("Emiten Sri Tahir (SRAJ)") and "menyuntikkan modal" in body


def test_generic_page_is_cut_at_the_related_news_heading():
    lead = "PT Contoh Tbk (CNTH) mengumumkan rights issue untuk ekspansi pabrik. " * 5
    html = f"<html><body><article><p>{lead}</p><h3>Berita Terkait</h3><a>XYZZ akuisisi pengendali</a></article></body></html>"
    text = main_text(Selector(html))
    assert "XYZZ" not in text and "ekspansi pabrik" in text


def test_old_snapshot_is_reextracted_from_raw_html_without_network(tmp_path):
    url = "https://emitennews.com/news/sraj"
    body = SRAJ_HTML.encode()
    digest = hashlib.sha256(body).hexdigest()
    (tmp_path / "objects").mkdir()
    (tmp_path / "objects" / f"{digest}.html").write_bytes(body)
    (tmp_path / "urls").mkdir()
    old = {"extractor_version": "html-pdf-v1", "url": url, "title": "SRAJ", "format": "html", "sha256": digest,
           "fetched_at": "2026-09-28T03:00:00+00:00", "pages": [{"number": None, "text": "IDXINDUST\n" + SRAJ_LEAD}]}
    import json
    (tmp_path / "urls" / f"{hashlib.sha256(url.encode()).hexdigest()}.json").write_text(json.dumps(old))

    class NoNetwork:
        def response(self, _url):
            raise AssertionError("jaringan tidak boleh dipakai untuk ekstraksi ulang")

    document = DocumentSource(tmp_path, mode="offline", transport=NoNetwork()).fetch_document(url)
    assert document.extractor_version == HTML_EXTRACTOR_VERSION
    assert "IDXINDUST" not in document.text and SRAJ_LEAD in document.text


# --- 3. EPAC: debt-only butuh bukti eksklusivitas ---------------------------------------------------

def test_mixed_use_sentence_never_becomes_debt_only_even_if_only_debt_survives():
    assert debt_only_signals([fact("debt_repayment")]) == []


def test_unverified_expansion_means_incomplete_coverage_not_debt_only():
    debt = fact("debt_repayment", quote="Dana hasil rights issue akan dipakai untuk pelunasan pinjaman bank.")
    assert debt_only_signals([debt], unverified_uses={"core_expansion"}) == []


def test_explicit_debt_only_quote_still_raises_the_flag():
    debt = fact("debt_repayment", quote="Seluruh dana hasil rights issue akan digunakan untuk pelunasan pinjaman bank.")
    assert [signal.code for signal in debt_only_signals([debt])] == ["funds_debt_only"]


def test_repaying_a_working_capital_loan_is_still_only_debt():
    debt = fact("debt_repayment", quote="Seluruh dana akan digunakan untuk pelunasan kredit modal kerja dari Bank X.")
    assert [signal.code for signal in debt_only_signals([debt])] == ["funds_debt_only"]


# --- 4. penggunaan dana yang bisa bersamaan bukan kontradiksi -----------------------------------------

def test_reader_that_only_saw_debt_does_not_contradict_expansion():
    reader = [fact("debt_repayment", quote="pengurangan sebagian utang terutama utang bank")]
    result = compare_independent_facts([fact("core_expansion"), fact("debt_repayment", fact_id="F02")], reader)
    assert [check.status for check in result.checks] == ["not_supported", "supported"]


def test_core_and_new_business_on_one_quote_still_contradict():
    result = compare_independent_facts([fact("core_expansion")], [fact("new_business")])
    assert result.checks[0].status == "contradicted"


def test_two_word_quote_inside_the_analyst_sentence_supports_the_same_use():
    store = SimpleNamespace(items=[page(EPAC_USES)])
    reader = extraction_with(uses=[("working_capital", "modal kerja")])
    facts, issues = verified_facts(reader, store)
    assert not facts and "terlalu pendek" in issues[0] and "tidak ditemukan" not in issues[0]
    result = compare_independent_facts([fact("working_capital")], facts, short_use_hints(reader, store))
    assert result.checks[0].status == "supported"


# --- 5. pemicu frontier dari label pembanding yang berbeda --------------------------------------------

def flag(quote):
    return {"evidence_id": "E002", "quote": quote, "present": True}


def screening_engine(tmp_path, reviewer_outputs, client):
    settings = Settings(_env_file=None, research_cases_dir=tmp_path / "cases", research_validator_mode="strict",
                        frontier_enabled=True, frontier_mode="shadow", deepseek_api_key=FAKE_KEY,
                        frontier_directory=tmp_path / "frontier")
    log = []
    analyst = RotatingModel("ollama:qwen2.5:14b", [extraction()], log)
    reviewers = [RotatingModel(name, [output], log) for name, output in zip(READERS, reviewer_outputs)]
    return ResearchEngine(settings, model=analyst, reviewers=reviewers,
                          frontier=FrontierService(settings, client=client, sleep=lambda _s: None),
                          scraper=FakeScraper({SOURCE_URL: ("HATM", ARTICLE, [])}))


CHANGE = flag("menerbitkan 868 juta saham baru melalui private placement")
INJECTION = flag("Saham baru diserap PT Multi Sarana Nasional")
DIVESTED = flag("Dana digunakan untuk menambah armada kapal curah perseroan")


def test_reviewers_that_both_disagree_with_different_labels_trigger_escalation(tmp_path):
    inconclusive = {**extraction(), "business_change": CHANGE}
    red_flag = {**extraction(), "business_change": CHANGE, "asset_injection": INJECTION,
                "old_business_divested": DIVESTED}
    empty_review = lambda _user: completion(review_json())
    outcome = screening_engine(tmp_path, [inconclusive, red_flag], ScriptedClient([empty_review] * 4)) \
        .research(make_event(), None, use_cache=False)
    labels = [check["independent_label"] for check in outcome.reviewer_checks]
    assert labels == ["inconclusive", "structural_red_flag"]
    assert [check["agrees_with_label"] for check in outcome.reviewer_checks] == [False, False]
    assert {"claim_id": None, "reason": "label_conflict"} in outcome.frontier["triggers"]
    assert outcome.frontier["status"] != "not_triggered"
    # shadow: keputusan lokal tidak berubah
    assert outcome.verdict.label == VerdictLabel.inconclusive and not outcome.frontier["applied"]


def test_reviewers_that_agree_with_each_other_do_not_count_as_a_label_conflict(tmp_path):
    red_flag = {**extraction(), "business_change": CHANGE, "asset_injection": INJECTION,
                "old_business_divested": DIVESTED}
    outcome = screening_engine(tmp_path, [red_flag, red_flag], ScriptedClient([])) \
        .research(make_event(), None, use_cache=False)
    assert not any(trigger["reason"] == "label_conflict" for trigger in outcome.frontier["triggers"])


# --- 6. identitas emiten dan peran pihak -----------------------------------------------------------

@pytest.mark.parametrize("name, issuers", [
    ("PT Sejahteraraya Anugrahjaya Tbk. (SRAJ)", ["SRAJ", None]),
    ("PT Megalestari Epack Sentosaraya Tbk (EPAC)", ["EPAC", "PT Megalestari Epack Sentosaraya Tbk"]),
    ("PT Ketrosden Triasmitra Tbk. (KETR)", ["KETR"]),
])
def test_issuer_with_ticker_annotation_is_not_its_own_counterparty(name, issuers):
    assert "emiten sendiri" in party_name_issue(name, "new_party", f"{name} mengumumkan aksi", issuers)


def test_another_listed_company_is_not_mistaken_for_the_issuer():
    name = "PT Trimegah Sekuritas Indonesia Tbk. (TRIM)"
    assert party_name_issue(name, "affiliate", f"{name} ditunjuk sebagai perantara", ["KETR"]) is None


def test_issuer_name_fallback_reads_tbk_with_a_period():
    """Tanpa Sectors, nama emiten diambil dari teks; dulu "Tbk. (SRAJ)" tidak cocok karena titiknya."""
    assert issuer_name_in(SRAJ_LEAD, "SRAJ") == "PT Sejahteraraya Anugrahjaya Tbk"
    assert issuer_name_in("PT Megalestari Epack Sentosaraya Tbk (EPAC) tengah", "EPAC") == \
        "PT Megalestari Epack Sentosaraya Tbk"
    assert issuer_name_in(SRAJ_LEAD, "PART") is None


def test_issuer_quoted_as_new_party_is_dropped_with_the_reason():
    parties = [("PT Sejahteraraya Anugrahjaya Tbk. (SRAJ)", "new_party", SRAJ_LEAD[:120])]
    facts, issues = verified_facts(extraction_with(parties=parties), SimpleNamespace(items=[page(SRAJ_LEAD)]),
                                   ["SRAJ", issuer_name_in(SRAJ_LEAD, "SRAJ")])
    assert not facts and "emiten sendiri" in issues[0]


# --- 7. informasi parsial tetap tampil saat hasil ditahan ------------------------------------------

def held(status, **verdict):
    values = {"label": "inconclusive", "confidence": 0.0, "provider": "test",
              "summary": "HATM mengumumkan aksi korporasi. Dari sumbernya terverifikasi dana untuk modal kerja.",
              "rationale_bullets": ["Dana untuk modal kerja [E002]"]} | verdict
    return ResearchOutcome(status=status, verdict=Verdict(**values))


def test_needs_review_keeps_verified_facts_and_names_the_hold_reason():
    screened = screen_outcome(make_event(), None, held("needs_review"))
    assert screened.gate.status == GateStatus.needs_review
    assert screened.verdict.summary.startswith("HATM mengumumkan")
    assert screened.verdict.rationale_bullets[0].startswith("Ditahan untuk pemeriksaan")
    assert "Dana untuk modal kerja [E002]" in screened.verdict.rationale_bullets


def test_infrastructure_failure_is_not_presented_as_an_analytic_verdict():
    bullets = screen_outcome(make_event(), None, held("model_unavailable")).verdict.rationale_bullets
    assert "kegagalan infrastruktur" in bullets[0]


def test_language_violation_is_still_withheld_while_held():
    screened = screen_outcome(make_event(), None, held("needs_review", rationale_bullets=["Beli sekarang"]))
    assert screened.verdict.summary == "" and "Beli" not in " ".join(screened.verdict.rationale_bullets)


# --- 8. timeout frontier menyebut fasenya; request yang tidak terkirim tidak dibebankan --------------

def failing_client(error):
    def handler(request):
        raise error("gagal", request=request)
    return DeepSeekClient(FAKE_KEY, transport=httpx.MockTransport(handler))


def test_connect_timeout_reports_the_connection_phase_and_is_not_billed():
    with pytest.raises(FrontierCallError) as caught:
        failing_client(httpx.ConnectTimeout).complete("s", "u")
    error = caught.value
    assert error.kind == "connect_timeout" and error.phase == "connect" and error.billed == "no" and not error.sent
    assert "15 detik" in error.message and "fase koneksi" in error.message and "180" not in error.message


def test_read_timeout_reports_the_read_phase_with_unknown_billing():
    with pytest.raises(FrontierCallError) as caught:
        failing_client(httpx.ReadTimeout).complete("s", "u")
    error = caught.value
    assert error.phase == "read" and error.billed == "unknown" and error.sent and "180 detik" in error.message


def connect_error():
    return FrontierCallError("connect_timeout", "Tidak tersambung (fase koneksi).", transient=True, billed="no",
                             phase="connect")


def test_unsent_attempts_cost_nothing_and_do_not_use_up_the_candidate(tmp_path):
    frontier = service(frontier_settings(tmp_path, frontier_max_calls_per_run=2),
                       ScriptedClient([connect_error(), connect_error()]))
    record = run_review(frontier)
    assert record["status"] == "failed"
    usage = frontier.ledger.usage("run-1")
    assert usage["run_cost_usd_charged"] == 0 and usage["run_tokens_charged"] == 0
    # Dua baris not_sent tidak menghabiskan batas 2 panggilan per run: kandidat masih bisa dicoba lagi.
    fresh = service(frontier_settings(tmp_path, frontier_max_calls_per_run=2),
                    ScriptedClient([completion(review_json())]))
    assert run_review(fresh, run_key="run-1")["status"] != "budget_exhausted"


def test_unreachable_host_is_skipped_for_the_rest_of_the_batch(tmp_path):
    client = ScriptedClient([connect_error(), connect_error()])
    frontier = service(frontier_settings(tmp_path), client)
    run_review(frontier)
    second = run_review(frontier, run_key="run-2")  # ScriptedClient kosong: memanggil lagi akan gagal keras
    assert second["status"] == "failed" and "tidak terjangkau" in second["calls"][0]["error"]
    assert len(client.prompts) == 2


# --- 9. SEMA: kontrak material dan akses PDF yang ditolak ------------------------------------------

def test_material_contract_form_title_is_not_a_control_change():
    bucket, _ = classify("Acquisition or Lost of Material Contract [ SEMA ]", "")
    assert bucket == ActionBucket.general_action


def test_robots_refusal_is_reported_as_access_not_ocr():
    failures = [{"url": "https://www.idx.co.id/a.pdf", "error": "robots.txt tidak tersedia (HTTP 403)."}]
    issues = document_issues("pdf_missing_or_unrelated", failures, [])
    text = " ".join(issues)
    assert "robots.txt" in text and "HTTP 403" in text and "OCR" not in text

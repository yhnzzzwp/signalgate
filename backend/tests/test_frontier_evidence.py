"""Pemilihan potongan bukti frontier (F06/T05): konteks koreksi di akhir artikel tidak hilang."""
from app.frontier.evidence import excerpt

FILLER = "Paragraf latar belakang industri pelayaran dan kinerja kuartalan perseroan. " * 40


def article():
    return ("PT Test Abadi Tbk (TEST) berencana menerbitkan 800 juta saham baru melalui private placement. "
            + FILLER + "Ralat: jumlah saham direvisi menjadi 640 juta saham sesuai keterbukaan informasi 2 September 2026.")


def test_t05_correction_at_the_end_survives_and_the_cut_is_labelled():
    text = article()
    assert len(text) > 3000
    cut = excerpt(text, anchors=["berencana menerbitkan 800 juta saham baru"], identity=["TEST"], max_chars=1500)
    assert cut["dipotong"] and cut["panjang_asli"] == len(" ".join(text.split()))
    assert "Ralat: jumlah saham direvisi menjadi 640 juta saham" in cut["teks"]
    assert "800 juta saham" in cut["teks"]
    assert len(cut["teks"]) <= 1500 + 20


def test_a_quote_deep_inside_the_article_is_included():
    text = FILLER + "Direksi menyatakan dana dipakai untuk menambah armada kapal curah." + FILLER
    cut = excerpt(text, anchors=["dana dipakai untuk menambah armada kapal curah"], max_chars=900)
    assert "dana dipakai untuk menambah armada kapal curah" in cut["teks"]


def test_short_articles_are_passed_whole():
    cut = excerpt("Artikel pendek 868 juta saham.", max_chars=500)
    assert cut == {"teks": "Artikel pendek 868 juta saham.", "dipotong": False, "panjang_asli": 30}

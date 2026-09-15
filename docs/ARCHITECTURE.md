# SignalGate — Arsitektur

## Problem statement

Investor ritel IDX kebanjiran pengumuman aksi korporasi (rights issue, private placement,
akuisisi, perubahan pengendali) tanpa cara cepat membedakan mana katalis pertumbuhan riil dan
mana pola structural red-flag (asset injection/backdoor listing). SignalGate meng-scan
pengumuman IDX lewat Sectors API, mengklasifikasikannya jadi skor+label beserta rationale yang
bisa diaudit — murni insight, bukan rekomendasi beli/jual.

## Sumber data

Sectors Financial API v2 (`api.sectors.app/v2`) adalah sumber data inti — kalau dicabut, seluruh
pipeline kehilangan fungsinya:
- `news?keyword=...` — pengumuman aksi korporat, sudah pre-tagged kategori & sentiment.
- `company/report/{ticker}` — ownership, valuation (PBV/PE/intrinsic value), financials,
  management, peers dalam satu call.
- `daily/{ticker}` — OHLCV harian, dipakai VALIDATE & WATCH buat cross-check pergerakan harga.

## Pipeline — 8 tahap, tahap ACT dihapus total

Automated trade execution dilarang oleh aturan kompetisi. Pipeline berhenti di scoring/insight,
tidak ada broker adapter sama sekali.

| Tahap | Modul | Fungsi |
|---|---|---|
| ① SENSE | `app/pipeline/sense.py` | Poll `news` (keyword aksi korporat) + `company/report` buat snapshot ownership/financials/valuation |
| ② HYPOTHESIZE | `app/pipeline/hypothesize.py` | Klasifikasi bucket kasar per keyword: control_change / non_preemptive_capital / rights_issue / general_action |
| ③ REASON | `app/pipeline/reason.py` + `app/llm/*` | LLM (provider-agnostic) hasilkan verdict terstruktur dengan rubric domain-spesifik |
| ④ VALIDATE | `app/pipeline/validate.py` | Cross-check klaim numerik LLM (PBV, isu kendali) ke data snapshot asli — cegah halusinasi |
| ⑤ GATE 🔒 | `app/pipeline/gate.py` | Deterministic, TIDAK bisa dilewati LLM: Pydantic schema keras + keyword denylist rekomendasi transaksi |
| ⑥ [ACT] | — | Dihapus. Tidak ada eksekusi order. |
| ⑦ WATCH | `app/pipeline/watch.py` | Status event: `active → resolved_growth / resolved_redflag / stale` |
| ⑧ AUDIT | `app/pipeline/audit.py` + `app/db/*` | SQLite append-only: tiap SENSE/VALIDATE/GATE decision tercatat, bisa di-query |

## Compliance Gate — bagian paling kritis

`app/pipeline/gate.py` memastikan output tidak pernah berbentuk rekomendasi investasi, secara
arsitektural (bukan cuma instruksi prompt yang bisa diabaikan LLM):

1. **Schema keras** — `Verdict` adalah Pydantic model dengan `label` terbatas ke 3 enum
   (`growth_catalyst` / `structural_red_flag` / `inconclusive`). LLM tidak bisa mengeluarkan
   teks bebas sebagai keputusan akhir.
2. **Keyword denylist** — rationale/red-flag/growth signals di-scan untuk istilah yang menyerupai
   rekomendasi transaksi ("beli", "jual", "buy", "sell", "target price", dst, word-boundary
   match, bukan substring naif). Kalau kena, event di-flag `needs_review` dan teks mentahnya
   disembunyikan dari dashboard (`sanitize_for_display`).

Teruji di `backend/tests/test_gate.py` — termasuk kasus Bahasa Indonesia dan Inggris, serta bukti
tidak ada false-positive dari substring (mis. "sellular" tidak ke-trigger "sell").

## LLM provider — multi-provider dengan mock fallback wajib

`app/llm/factory.py` memilih provider dari environment variable yang tersedia (Claude → OpenAI →
Gemini → mock), supaya juri bisa clone & jalankan pipeline penuh TANPA API key sendiri. Mock
provider (`app/llm/mock_provider.py`) adalah heuristik deterministik (bucket + PBV + nama
pemegang saham utama) — cukup buat menangkap kasus dengan sinyal kuat (PBV ekstrem), tapi
diketahui TIDAK bisa membedakan nuansa seperti "pemegang saham afiliasi lama menambah modal"
vs "operator baru masuk" — itu perlu reasoning LLM asli dengan rubric lengkap
(`app/llm/base.py::REASONING_RUBRIC`).

## Kasus validasi (bukan data sintetis)

Ditemukan & dianalisis manual sebelum pipeline dibangun, dipakai sebagai golden test
(`backend/tests/test_pipeline_golden.py`):

- **MGLV** (PT NexAI Digital Infrastruktur, dulu PT Panca Anugrah Wisesa) — bisnis furniture
  lama didivestasi total ke satu pihak, bisnis AI/data-center baru disuntik dari pihak lain,
  emiten ganti nama, PBV pasca-transaksi >250x. Pipeline (bahkan dengan mock provider) menangkap
  ini sebagai `structural_red_flag` murni dari data PBV & ownership real-time — tanpa perlu
  scraping PDF manual seperti pendekatan sebelumnya.
- **HATM** (PT Habco Trans Maritima) — private placement oleh pemegang saham afiliasi LAMA
  (bukan pihak baru) buat danai ekspansi armada kapal, laba bersih naik 667% YoY. Butuh reasoning
  LLM asli (bukan mock) buat membedakan ini dari MGLV — pembeda struktural bukan cuma "ada
  private placement", tapi siapa penerimanya dan konteks keuangannya.

## Menjalankan tanpa API key (syarat wajib brief)

```bash
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # isi SECTORS_API_KEY, biarkan LLM key kosong -> mock provider otomatis
uvicorn app.main:app --reload
```

```bash
cd frontend && npm install && npm run dev
```

Pipeline jalan penuh end-to-end (SENSE→HYPOTHESIZE→REASON [mock]→VALIDATE→GATE→AUDIT→dashboard)
tanpa satu pun LLM API key — cuma perlu `SECTORS_API_KEY`.

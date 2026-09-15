# SignalGate

Sectors Hackathon 2026 — Track 01 (AI Agents & Assistants). Tim **Info Magang**.

> **Investor ritel IDX kebanjiran pengumuman aksi korporasi (rights issue, private placement,
> akuisisi, perubahan pengendali) tanpa cara cepat membedakan mana katalis pertumbuhan riil dan
> mana pola structural red-flag (asset injection/backdoor listing).** SignalGate meng-scan
> pengumuman IDX lewat [Sectors API](https://sectors.app), mengklasifikasikannya jadi
> skor+label beserta rationale yang bisa diaudit.

## ⚠️ Disclaimer

**Ini bukan rekomendasi beli/jual.** SignalGate murni alat screening/insight — tidak pernah
mengeksekusi order, tidak terhubung ke broker/akun trading manapun, dan secara arsitektural
(lihat [Compliance Gate](docs/ARCHITECTURE.md#compliance-gate--bagian-paling-kritis)) mencegah
output berbentuk rekomendasi transaksi. Keputusan investasi sepenuhnya tanggung jawab pengguna.

## Arsitektur

8-tahap pipeline (SENSE → HYPOTHESIZE → REASON → VALIDATE → GATE 🔒 → WATCH → AUDIT, tahap ACT
dihapus total — automated trade execution dilarang). Detail lengkap + rasionalisasi tiap
keputusan di [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Menjalankan (tanpa API key LLM sekalipun)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# isi SECTORS_API_KEY di .env — dapatkan dari https://sectors.app
uvicorn app.main:app --reload
```

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

Buka `http://localhost:5173`, klik "Jalankan pipeline". Tanpa `ANTHROPIC_API_KEY` /
`OPENAI_API_KEY` / `GEMINI_API_KEY`, REASON stage otomatis pakai mock provider deterministik —
pipeline tetap jalan penuh end-to-end.

## Test

```bash
cd backend && source .venv/bin/activate && python -m pytest -v
```

`tests/test_gate.py` membuktikan Compliance Gate tidak bisa dilewati LLM.
`tests/test_pipeline_golden.py` menjalankan pipeline terhadap data Sectors LIVE untuk dua kasus
riil IDX (MGLV, HATM) yang dianalisis manual sebagai validasi konsep sebelum kode ditulis.

## Struktur

```
backend/app/sectors/   klien Sectors API v2
backend/app/pipeline/  8 tahap: sense, hypothesize, reason, validate, gate, watch, audit
backend/app/llm/       provider Claude/OpenAI/Gemini + mock fallback, satu rubric bersama
backend/app/api/       endpoint FastAPI yang dikonsumsi dashboard
backend/app/db/        model SQLAlchemy (audit trail persistent)
frontend/src/          dashboard React + TypeScript
docs/                  arsitektur & rasionalisasi keputusan teknis
```

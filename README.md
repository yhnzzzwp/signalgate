# SignalGate

Sectors Hackathon 2026 — Track 01 (AI Agents & Assistants). Tim **Info Magang**.

> **Investor ritel IDX kebanjiran pengumuman aksi korporasi (rights issue, private placement,
> akuisisi, perubahan pengendali) tanpa cara cepat membedakan mana katalis pertumbuhan riil dan
> mana pola structural red-flag (asset injection/backdoor listing).** SignalGate meng-scan
> pengumuman IDX lewat [Sectors API](https://sectors.app), mengekstrak fakta berkutipan dari
> sumber, lalu memberi label screening beserta alasan dan ketidakpastiannya.

## ⚠️ Disclaimer

**Ini bukan rekomendasi beli/jual.** SignalGate murni alat screening/insight — tidak pernah
mengeksekusi order, tidak terhubung ke broker/akun trading manapun, dan secara arsitektural
(lihat [Compliance Gate](docs/ARCHITECTURE.md#compliance-gate)) mencegah output berbentuk
rekomendasi transaksi. Keputusan investasi sepenuhnya tanggung jawab pengguna.

## Cara kerja singkat

1. **SENSE** — berita aksi korporat dan laporan emiten dari Sectors API.
2. **HYPOTHESIZE** — klasifikasi & prioritas deterministik; hanya kandidat teratas yang diriset.
3. **Ekstraksi** — qwen2.5:7b (Ollama, lokal) membaca artikel yang diambil Scrapling dan mengeluarkan
   fakta berkutipan: siapa penerima saham, untuk apa dananya, apakah ada pergantian bisnis atau
   injeksi aset.
4. **Validasi** — Python mencocokkan setiap kutipan dan nama pihak ke teks sumber; qwen3:4b memeriksa
   fakta secara independen tanpa melihat jawaban analis.
5. **Skor** — Python memberi bobot dan menetapkan label. Model tidak menentukan label dan hanya bisa
   menurunkannya ke `inconclusive`.
6. **GATE & AUDIT** — hasil berbahasa transaksi tidak pernah tampil; bukti dan setiap langkah
   tersimpan di `cases/`.

Detail lengkap, termasuk tabel bobot skor dan mode validator, di [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Menjalankan

Butuh aplikasi [Ollama](https://ollama.com/download) (gratis, lokal — tanpa API key LLM) dan
`SECTORS_API_KEY`. Buka aplikasi Ollama, lalu:

```bash
ollama pull qwen2.5:7b
ollama pull qwen3:4b
```

### Dua profil perangkat keras

`SIGNALGATE_PROFILE` memilih model lokal mana yang membaca bukti. Profil hanya menyentuh setelan
Ollama — jumlah event per run dan pemakaian kredit Sectors tidak ikut berubah. Setelan apa pun yang
kamu tulis sendiri di `.env` selalu mengalahkan profil.

| Profil | Analis | Validator | Untuk |
|---|---|---|---|
| `laptop` (default) | `qwen2.5:7b` | `qwen3:4b` | Mac / GPU kecil |
| `workstation` | `qwen2.5:14b` | `qwen3:4b` | GPU 16 GB |

```bash
ollama pull qwen2.5:14b   # hanya di mesin workstation
```

Analis workstation sengaja sekeluarga dengan yang di laptop: prompt ekstraksi dan schema JSON-nya
sudah disetel untuk Qwen, jadi ganti ukuran lebih aman daripada ganti keluarga model.

**Memakai GPU mesin lain tanpa memindahkan backend.** Di mesin ber-GPU jalankan Ollama dengan
`OLLAMA_HOST=0.0.0.0`, lalu di laptop cukup arahkan `OLLAMA_BASE_URL=http://<ip-mesin-itu>:11434`.
Backend, dashboard, dan `cases/` tetap di satu tempat.

#### Catatan GPU AMD di Windows

Installer Ollama untuk Windows sudah membawa ROCm sendiri, tetapi kartu RDNA4 (RX 9000, `gfx1200`)
baru dikenali oleh Ollama versi baru — **pasang versi terbaru lebih dulu**, jangan versi yang sudah
lama terpasang. Setelah `ollama run`, pastikan model benar-benar di GPU:

```
ollama ps
```

Kolom `PROCESSOR` harus berbunyi `100% GPU`. Kalau tertulis `100% CPU`, model jatuh ke prosesor dan
14B akan terasa sangat lambat. Yang perlu dicoba, berurutan:

1. Perbarui Ollama dan driver Adrenalin, lalu ulangi `ollama ps`.
2. Kalau masih CPU, set `HSA_OVERRIDE_GFX_VERSION=12.0.0` lewat *System Properties → Environment
   Variables*, lalu **restart Ollama dari tray**, bukan cuma tutup jendelanya.
3. Masih CPU: turunkan dulu ke `qwen2.5:7b` untuk memastikan masalahnya ukuran model atau deteksi GPU.

Angka `12.0.0` itu untuk `gfx1200` (RX 9060 XT). Kartu lain butuh angka lain, jadi jangan disalin
begitu saja ke mesin yang berbeda.

#### Muat atau tidak di 16 GB

`qwen2.5:14b` pada kuantisasi Q4 kira-kira 9 GB, ditambah KV cache untuk `OLLAMA_NUM_CTX=16384`
sekitar 3 GB. Cukup longgar di 16 GB untuk satu model. Menahan analis dan validator di VRAM
bersamaan (`OLLAMA_OFFLOAD_BETWEEN_MODELS=false`) akan lebih cepat karena tidak ada muat-ulang tiap
giliran, tapi totalnya mepet — coba, lalu periksa `ollama ps`; kalau salah satu model turun ke CPU,
kembalikan ke `true`. Semua angka ini perkiraan: yang menentukan tetap `ollama ps` di mesin itu.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

Buka `http://localhost:5173`, klik "Jalankan pipeline".

Uji pengembangan hanya dengan artikel Scrapling, tanpa memanggil Sectors API:

```bash
cd backend && source .venv/bin/activate && python -m app.scrapling_check
```

## Test

```bash
cd backend && source .venv/bin/activate && python -m pytest -v
```

Test tidak membutuhkan Ollama maupun jaringan: engine diuji dengan model dan scraper palsu
(kutipan salah memicu ekstraksi ulang, validator hanya bisa menurunkan label, mode lenient/strict,
cache, model mati), penyaringan kutipan dan nama pihak, aturan skor, klien Ollama, dan Compliance Gate.

## Struktur

```
backend/app/sectors/   klien Sectors API v2
backend/app/pipeline/  sense, hypothesize, validate, gate, watch, audit, orchestrator
backend/app/research/  engine hybrid, klien Ollama, penyaringan fakta, skor, evidence store Scrapling
backend/app/api/       endpoint FastAPI yang dikonsumsi dashboard
backend/app/db/        model SQLAlchemy (audit trail)
frontend/src/          dashboard React + TypeScript
docs/                  arsitektur & rasionalisasi keputusan teknis
```

## Evaluasi

[Arsitektur, aturan hackathon, dan rencana uji pasar Indonesia](docs/VALIDASI_HACKATHON.md).

`python -m app.evaluate` menyediakan capture bukti terbaru, replay snapshot, dan pengukuran
terhadap label acuan manusia. Hasil model tidak otomatis dianggap benar.

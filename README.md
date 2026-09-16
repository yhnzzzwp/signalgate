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

### Setup otomatis

Satu perintah menyiapkan venv, dependensi, model Ollama, dan `.env` — jalankan dengan Python sistem,
bukan dari dalam venv:

```bash
python3 scripts/setup.py --profile laptop        # Mac / GPU kecil
python3 scripts/setup.py --profile workstation   # GPU 16 GB
```

`.env` yang dihasilkan menyetel `SECTORS_API_ENABLED=false` dan membiarkan `SECTORS_API_KEY` kosong,
jadi **tidak ada satu pun kredit Sectors terpakai** sampai kamu sendiri mengisinya. Scrapling
mengambil artikel lewat HTTP biasa, jadi Ollama tetap dipakai penuh dan semua bagian riset bisa diuji
tanpa menyentuh API berbayar:

```bash
cd backend && source .venv/bin/activate
python -m app.scrapling_check --limit 1
```

`.env` yang sudah ada tidak pernah ditimpa kecuali kamu menambahkan `--overwrite-env`. Bendera lain:
`--skip-models`, `--skip-tests`, `--recreate-venv`.

Kalau nanti siap memakai data Sectors: isi `SECTORS_API_KEY` lalu ubah `SECTORS_API_ENABLED=true`.
Sebelum itu `/pipeline/run` memang menolak berjalan — itu disengaja, bukan bug.

Skrip ini tidak menjalankan `scrapling install`. Repo memakai `Fetcher` (HTTP biasa) di
`app/research/evidence.py`, bukan fetcher berbasis browser, jadi unduhan Camoufox/Chromium yang
ratusan megabita itu tidak dibutuhkan.

### Setup manual

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

| Profil | Analis | Pembaca pembanding | Untuk |
|---|---|---|---|
| `laptop` (default) | `qwen2.5:7b` | `qwen3:4b` | Mac / GPU kecil |
| `workstation` | `qwen2.5:14b` | `glm4:9b` lalu `gemma3:12b` | GPU 16 GB |

```bash
ollama pull qwen2.5:14b && ollama pull glm4:9b && ollama pull gemma3:12b
```

Analis workstation sengaja sekeluarga dengan yang di laptop: prompt ekstraksi dan schema JSON-nya
sudah disetel untuk Qwen, jadi ganti ukuran lebih aman daripada ganti keluarga model.

**Rotasi pembaca pembanding.** `OLLAMA_REVIEWER_MODELS` dibaca berurutan dan
`OLLAMA_OFFLOAD_BETWEEN_MODELS=true` mengusir tiap model setelah gilirannya
(`POST /api/generate` dengan `keep_alive: 0`), jadi urutannya:

```
qwen2.5:14b baca → evict → glm4:9b baca → evict → gemma3:12b baca → evict
```

Hanya satu model di VRAM pada satu waktu, jadi puncak pemakaian = model terbesar (~12 GB dengan KV
cache), bukan jumlah ketiganya. Tiga vendor berbeda dipilih dengan sengaja: pembanding ada untuk
*tidak setuju*, dan model sekeluarga cenderung salah di kalimat yang sama.

Harganya waktu, bukan VRAM: tiap giliran berarti satu muat-ulang model. Dengan
`RESEARCH_REVIEW_ROUNDS=2` jumlah muat-ulang ikut berlipat.

Dua batasan yang mengikat pilihan model:

- **Jangan pakai model bermode *thinking*** (`deepseek-r1`, `glm-5.x`, `glm-4.7-flash`). `make_agent()`
  hanya mengirim `think=False` untuk `qwen3`, sehingga jejak penalaran model lain akan merusak JSON
  ketat yang diharapkan schema.
- **Isi `OLLAMA_REVIEWER_MODELS` atau `OLLAMA_VALIDATOR_MODEL`, jangan keduanya.** `build_provider()`
  selalu memilih jalur reviewer, jadi validator akan terabaikan; sekarang konfigurasi seperti itu
  ditolak saat start dengan pesan yang menyebut apa yang harus dibetulkan.

Mau `llama3.1:8b` seperti contoh di `.env.example`? Ganti satu baris:
`OLLAMA_REVIEWER_MODELS=["glm4:9b","llama3.1:8b"]`. Saya memilih `gemma3:12b` sebagai default karena
Gemma 3 dilatih multibahasa secara eksplisit sementara Indonesia bukan kekuatan Llama 3.1 8B — dan
pembanding yang lemah berbahaya di sini: aturan penggabungan bersifat pesimistis (`contradicted`
menang), jadi pembanding yang salah paham menyeret hasil ke `inconclusive`, bukan sekadar jadi
suara minoritas.

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

# Setup SignalGate

## Prasyarat

- Python 3.11–3.13, Node.js 24, Git, dan Ollama yang sudah berjalan.
- Koneksi internet untuk instalasi serta mengambil berita/data baru. Model dijalankan di mesin sendiri.
- Profil `workstation`: `qwen2.5:14b`, `glm4:9b`, `gemma3:12b`, dijalankan bergantian. Gunakan profil ini untuk konfigurasi tiga model proyek; sesuaikan dengan memori perangkat.
- Profil `laptop`: `qwen2.5:7b`. Untuk pembanding independen, tambahkan `OLLAMA_VALIDATOR_MODEL=qwen3:4b` di `backend/.env`, lalu jalankan setup lagi. Tanpa pembanding, sebagian klaim laporan tidak dapat diverifikasi.

## Instalasi

Dari folder repository, macOS/Linux:

```bash
python3 scripts/setup.py --profile workstation
```

Windows:

```bat
scripts\setup.cmd --profile workstation
```

Setup membuat `backend/.venv`, memasang dependensi, menyiapkan `.env`, mengunduh model yang belum ada, membangun frontend, dan menjalankan tes. `.env` lama tetap dipertahankan; `--profile` berlaku untuk file baru. Untuk mengganti profil instalasi lama, edit `SIGNALGATE_PROFILE` di `backend/.env` dan hapus override model yang tidak dibutuhkan.

Pilihan: `--skip-models`, `--skip-tests`, `--skip-frontend`. `--overwrite-env` mengganti konfigurasi lama; gunakan hanya jika memang ingin meresetnya.

## Jalankan lokal

macOS/Linux:

```bash
cd backend
.venv/bin/python -m app.local
```

Windows:

```bat
cd backend
.venv\Scripts\python -m app.local
```

Buka **http://127.0.0.1:8000**. Dashboard dan API dilayani dalam satu proses tanpa development server. Hentikan dengan `Ctrl+C`.

Perintah ini memeriksa model sebelum startup, selalu memakai Ollama `127.0.0.1:11434`, dan menonaktifkan frontier/cloud meskipun konfigurasi runtime lama menunjuk Colab atau DeepSeek. Pemilihan Colab/frontier ditolak selama mode ini aktif. Kredensial yang sudah tersimpan tidak dihapus. Gunakan satu proses backend: kunci job dan progres SSE berada di proses tersebut.

Di tab **Runtime & GPU**, gunakan **Cek kesiapan** dengan lokasi lokal. Tombol pembuktian GPU melakukan inferensi singkat. `ollama ps` menunjukkan penempatan model pada CPU/GPU; kecepatan dan kebutuhan memori tergantung perangkat.

## Data dan konfigurasi

`backend/.env` adalah konfigurasi backend. Instalasi baru memakai `SECTORS_API_ENABLED=false`; **Scan Scrapling (tanpa API)** mengambil berita publik tanpa kredit Sectors. Situs sumber tetap membutuhkan internet dan dapat membatasi akses.

Untuk pipeline Sectors dan laporan emiten dengan data baru:

```dotenv
SECTORS_API_ENABLED=true
SECTORS_API_KEY=isi_key_anda
```

Restart backend setelah mengedit `.env`. Panggilan Sectors memakai kuota akun. Replay laporan memakai snapshot tersimpan. Inferensi tetap lokal pada `app.local`.

Model mengikuti `SIGNALGATE_PROFILE`; override `OLLAMA_MODEL` dan `OLLAMA_REVIEWER_MODELS` harus menunjuk nama persis dari `ollama list`. Jangan mengisi `OLLAMA_REVIEWER_MODELS` bersamaan dengan `OLLAMA_VALIDATOR_MODEL`. Override laporan tersedia melalui `WORKFLOW_ANALYST_MODEL` dan `WORKFLOW_REVIEWER_MODELS`.

Data tersimpan di `backend/signalgate.db`, `cases/`, dan `data/`. Path SQLite relatif selalu dihitung dari folder `backend`. Cadangkan data tersebut saat backend berhenti. File `.env`, database, cache, dan arsip lokal tidak dimasukkan ke Git. Jangan menaruh API key pada variabel `VITE_*`.

## Pengembangan dan pengujian

Backend (terminal pertama):

```bash
cd backend
.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1
```

Frontend (terminal kedua):

```bash
cd frontend
npm ci
npm run dev
```

Buka http://localhost:5173. Mode pengembangan mengikuti pengaturan runtime yang tersimpan; gunakan `app.local` untuk membatasi inferensi ke mesin ini. Frontend produksi memakai API satu origin; `VITE_API_BASE_URL` hanya digunakan saat pengembangan.

Tes backend:

```bash
cd backend
.venv/bin/python -m pytest -q
```

Pemeriksaan frontend:

```bash
cd frontend
npm run build
npm run lint
npm run test:render
```

Di Windows, ganti `.venv/bin/python` dengan `.venv\Scripts\python`. Tes memakai model/HTTP palsu; beberapa tes gateway membuka port loopback lokal. Satu tes audit arsip dilewati bila `cases/` belum berisi hasil analisis nyata. Pengujian inferensi nyata dilakukan terpisah lewat cek kesiapan atau analisis.

## Jika gagal

- **Ollama tidak bisa dihubungi:** buka aplikasi Ollama atau jalankan `ollama serve`.
- **Model belum diunduh:** jalankan `ollama pull nama:model` sesuai pesan startup, atau ulangi setup.
- **Dashboard belum dibangun:** jalankan `npm ci` dan `npm run build` dari folder `frontend`.
- **Port 8000 terpakai:** hentikan proses SignalGate lama sebelum memulai yang baru.
- **Memori kurang/timeout:** gunakan profil lebih kecil, kurangi `OLLAMA_NUM_CTX`, dan pertahankan `OLLAMA_OFFLOAD_BETWEEN_MODELS=true`.
- **Sectors ditolak:** periksa flag aktif, key, kuota, dan koneksi. Jalur Scrapling tidak membutuhkan key.
- **Hasil inconclusive:** periksa bukti dan status pembanding pada audit; sumber tidak terbaca atau klaim tidak terverifikasi bukan hasil sukses.

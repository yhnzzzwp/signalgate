# Setup SignalGate

## Prasyarat

Windows, Linux, atau macOS dengan **Python 3.11–3.13**, **Node.js 24**, Git, dan **Ollama**. Buka Ollama atau jalankan `ollama serve` sebelum setup. CPU didukung; GPU yang didukung Ollama mempercepat inferensi. Kecepatan dan model yang muat bergantung pada RAM/VRAM perangkat.

| Profil | Model | Penggunaan |
|---|---|---|
| `laptop` | `qwen2.5:7b` | Perangkat dengan memori terbatas; konfigurasi awal yang lebih ringan |
| `workstation` | `qwen2.5:14b`, `glm4:9b`, `gemma3:12b` | Analisis dengan dua pembanding, dimuat bergantian |

Untuk pembanding independen pada profil laptop, tambahkan `OLLAMA_VALIDATOR_MODEL=qwen3:4b` di `backend/.env` lalu ulangi setup. Tanpa pembanding, sebagian klaim laporan tidak dapat diverifikasi.

## Instalasi dan menjalankan

Dari folder repository:

**Windows (PowerShell atau Command Prompt)**

```bat
scripts\setup.cmd --profile laptop
py -3 run.py
```

**Linux / macOS**

```bash
python3 scripts/setup.py --profile laptop
python3 run.py
```

Ganti `laptop` dengan `workstation` jika sesuai perangkat. Setup memasang backend, mengunduh model yang belum tersedia, membangun dashboard, dan menjalankan tes backend. `.env` yang sudah ada tetap dipertahankan; edit `SIGNALGATE_PROFILE` di file itu untuk mengganti profil instalasi lama.

Buka **http://127.0.0.1:8000**. Dashboard dan API berjalan dalam satu proses lokal. Hentikan dengan `Ctrl+C`. Launcher memilih interpreter `.venv` sesuai OS, sehingga tidak perlu mengaktifkan environment atau mengubah kebijakan PowerShell. Path yang mengandung spasi didukung.

Cek kesiapan tanpa menjalankan server: `python3 run.py --check` (Windows: `py -3 run.py --check`). Tab **Runtime & GPU** memeriksa model dan penempatan CPU/GPU. Probe melepas model setelah pemeriksaan.

## Konfigurasi

`backend/.env` menyimpan konfigurasi. Inferensi memakai Ollama lokal; URL loopback dapat diubah dengan `OLLAMA_LOCAL_URL=http://127.0.0.1:11434`. Pengaturan remote lama diabaikan dan kredensialnya tidak dikirim. Launcher menonaktifkan frontier berbayar.

Model mengikuti `SIGNALGATE_PROFILE`. Override tersedia lewat `OLLAMA_MODEL`, `OLLAMA_REVIEWER_MODELS`, `WORKFLOW_ANALYST_MODEL`, dan `WORKFLOW_REVIEWER_MODELS`. Nama harus cocok dengan `ollama list`. Jangan mengisi `OLLAMA_REVIEWER_MODELS` dan `OLLAMA_VALIDATOR_MODEL` bersamaan. Pertahankan `OLLAMA_OFFLOAD_BETWEEN_MODELS=true` agar model bergantian memakai memori.

Instalasi baru menonaktifkan Sectors. **Scan Scrapling (tanpa API)** mengambil berita publik tanpa kredit Sectors, tetapi tetap membutuhkan internet. Untuk pipeline dan laporan dengan data Sectors baru:

```dotenv
SECTORS_API_ENABLED=true
SECTORS_API_KEY=isi_key_anda
```

Restart aplikasi setelah mengedit konfigurasi. Pengambilan data Sectors memakai kuota akun; replay memakai snapshot tersimpan. Inferensi model tetap lokal.

Data tersimpan di `backend/signalgate.db`, `cases/`, dan `data/`. Path database relatif dihitung dari folder `backend`. Cadangkan saat aplikasi berhenti. `.env`, cache, database, dan `.local-archive/` diabaikan Git. Jangan menaruh API key di variabel frontend `VITE_*`.

## Pengujian dan pengembangan

Dari folder repository, macOS/Linux:

```bash
backend/.venv/bin/python -m pytest backend/tests -q
npm --prefix frontend run build
npm --prefix frontend run lint
npm --prefix frontend run test:render
```

Di Windows gunakan `backend\.venv\Scripts\python` untuk perintah tes Python. Tes tidak menggunakan model atau API berbayar; tes startup memakai server Ollama palsu pada loopback. Audit arsip dilewati jika `cases/` kosong, dan tes `cmd.exe` khusus Windows dilewati pada OS lain.

Workflow GitHub Actions menguji Windows, Ubuntu, dan macOS dengan Python 3.11 serta 3.13. Workflow baru berjalan setelah perubahan dipush; konfigurasi matrix bukan bukti bahwa semua OS sudah lolos. Pengujian model/GPU nyata tetap perlu dilakukan pada perangkat tujuan.

Untuk pengembangan, jalankan `python -m uvicorn app.main:app --reload --host 127.0.0.1` dari `backend` dengan interpreter `.venv`, lalu `npm run dev` dari `frontend`. Buka http://localhost:5173. Setelah perubahan frontend, ulangi build sebelum menjalankan launcher produksi.

## Jika gagal

- **Ollama tidak terhubung:** buka aplikasinya atau jalankan `ollama serve`.
- **Model belum tersedia:** `ollama pull nama:model`, atau ulangi setup.
- **Memori kurang:** gunakan profil laptop dan kurangi `OLLAMA_NUM_CTX`. GPU bukan syarat startup.
- **Environment rusak setelah pindah OS/folder:** ulangi setup dengan `--recreate-venv`. Jangan menyalin `.venv` atau `node_modules` antarperangkat.
- **Port 8000 terpakai:** hentikan instance SignalGate sebelumnya.
- **Dashboard belum dibangun:** `npm --prefix frontend ci`, lalu `npm --prefix frontend run build`.
- **Node terlalu lama:** pasang Node.js 24, buka terminal baru, lalu ulangi setup.

Opsi setup: `--skip-models`, `--skip-frontend`, `--skip-tests`. `--overwrite-env` mereset `.env`; gunakan hanya jika memang diperlukan.

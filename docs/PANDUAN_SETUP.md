# Panduan setup lengkap SignalGate

Dari mesin kosong sampai dashboard berjalan: backend (FastAPI), frontend (React/Vite), model lokal di
MacBook atau GPU Colab, data Sectors, dan reviewer frontier DeepSeek opsional. Perintah ditulis untuk
macOS/Linux; catatan Windows ada di bagian 12.

Panduan ini melengkapi, bukan menggantikan: [README](../README.md) (ringkasan produk),
[RUNTIME_GPU.md](RUNTIME_GPU.md) (detail MacBook/Colab), [FRONTIER_DEEPSEEK.md](FRONTIER_DEEPSEEK.md)
(detail frontier), dan [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 0. Gambaran besar

```
 Browser (dashboard)            Backend (laptop)                   Model lokal (pilih salah satu)
 http://localhost:5173  ──────▶  FastAPI http://127.0.0.1:8000  ──▶  MacBook: Ollama 127.0.0.1:11434
   React + Vite                   ├─ SQLite (signalgate.db)          atau
                                  ├─ data/ (snapshot, cache, ledger) Colab GPU: tunnel HTTPS ─▶ gateway
                                  ├─ Sectors API (berbayar, opsional)             bertoken ─▶ Ollama
                                  └─ DeepSeek (berbayar, opsional)
```

- Backend dan database **selalu** di laptop kamu. Yang bisa pindah hanya tempat model Ollama berjalan.
- Semua kredensial (Sectors, DeepSeek) hanya di **`backend/.env`**. Frontend tidak pernah memegang key.
- Tanpa Sectors dan tanpa DeepSeek, SignalGate tetap jalan: scan Scrapling (0 kredit) dan replay
  snapshot memakai model lokal saja.

## 1. Prasyarat

| Kebutuhan | Versi / catatan |
|---|---|
| Python | **3.11+** (dipakai `StrEnum`); dikembangkan di 3.13 |
| Node.js | **20.19+** atau **22.12+** (syarat Vite 8) |
| Ollama | Aplikasi desktop untuk mode MacBook: <https://ollama.com/download>. Tidak perlu bila hanya memakai Colab |
| Git | untuk mengambil repo |
| Opsional | `SECTORS_API_KEY` (data emiten), `DEEPSEEK_API_KEY` (frontier), akun Google (Colab GPU) |

Ruang disk model (ukuran `ollama list` di mesin pengembang):

| Profil | Model | Ukuran |
|---|---|---|
| `workstation` | `qwen2.5:14b` (analis), `glm4:9b` lalu `gemma3:12b` (pembanding) | 9,0 + 5,5 + 8,1 GB |
| `laptop` | `qwen2.5:7b` (analis) | 4,7 GB |

Model dipakai **satu per satu** (dilepas dari memori tiap giliran), jadi puncak memori ≈ model terbesar
plus KV cache, bukan jumlah ketiganya. MacBook 16 GB bisa menjalankan profil `workstation` secara
bergiliran (lebih lambat); GPU Colab T4 16 GB adalah alternatifnya.

Cek versi:

```bash
python3 --version
node --version
ollama --version        # hanya bila memakai MacBook
```

## 2. Ambil kode

```bash
git clone <url-repo-signalgate>
cd signalgate
```

## 3. Backend

### 3.1 Cara otomatis (disarankan)

Jalankan dengan Python sistem (bukan dari dalam venv):

```bash
python3 scripts/setup.py --profile workstation     # atau --profile laptop
```

Skrip ini: membuat `backend/.venv`, memasang dependensi, **mengunduh model Ollama untuk profil itu**,
menulis `backend/.env` dalam mode hemat (`SECTORS_API_ENABLED=false`, key kosong, frontier mati), lalu
menjalankan test backend. `.env` yang sudah ada **tidak pernah ditimpa**.

| Bendera | Fungsi |
|---|---|
| `--skip-models` | lewati `ollama pull` (mis. kalau model akan dijalankan di Colab) |
| `--skip-tests` | lewati test |
| `--recreate-venv` | hapus lalu buat ulang `.venv` |
| `--overwrite-env` | timpa `backend/.env` (hati-hati: key yang sudah kamu isi ikut hilang) |

### 3.2 Cara manual

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Setelah `cp`, ganti nilai contoh `SECTORS_API_KEY=replace_with_your_sectors_api_key` menjadi kosong atau
key asli, dan set `SECTORS_API_ENABLED=false` kalau belum mau memakai kredit.

### 3.3 Mengisi `backend/.env`

`backend/.env` adalah **satu-satunya** file konfigurasi yang dibaca backend. `.env.local` dan
`.env.colab` versi lama tidak dibaca lagi; kalau masih ada, pindahkan sekali isinya yang perlu ke `.env`.

| Variabel | Isi | Keterangan |
|---|---|---|
| `SECTORS_API_KEY` | key Sectors | kosong = tidak ada kredit terpakai |
| `SECTORS_API_KEY_BACKUPS` | `[]` atau `["key2"]` | dipakai otomatis bila kuota key utama habis |
| `SECTORS_API_ENABLED` | `false` / `true` | `false` = mode hemat; `/pipeline/run` dan laporan live menolak jalan |
| `SIGNALGATE_PROFILE` | `workstation` / `laptop` | memilih model lokal (lihat bagian 1) |
| `OLLAMA_MODEL`, `OLLAMA_REVIEWER_MODELS` | biarkan **komentar** | nilai eksplisit mengalahkan profil |
| `WORKFLOW_REVIEWER_MODEL` | kosong | isi satu nama (mis. `gemma3:12b`) untuk kembali ke satu pembanding |
| `DEEPSEEK_API_KEY` | key DeepSeek | opsional; hanya di file ini (bagian 8) |
| `FRONTIER_MAX_COST_USD_PER_RUN` / `_PER_DAY` / `_TOTAL` | `0.06` / `0.60` / `1.80` | bawaan untuk saldo prabayar $2 |

Aturan precedence yang perlu diingat:

1. Nilai yang kamu tulis sendiri di `.env` > profil.
2. Pilihan dari dashboard (tab **Runtime & GPU**, disimpan di `data/runtime/`) > `OLLAMA_BASE_URL` dan
   mode frontier dari `.env`. Selama belum pernah diaktifkan dari dashboard, nilai `.env` yang dipakai.
3. Key selalu dari `.env`; dashboard tidak bisa membaca atau mengisinya.

Setiap mengubah `.env`, **restart backend**.

### 3.4 Menjalankan backend

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload
```

Atau `bash backend/scripts_local/run_local.sh` (hanya menyalakan backend; tidak lagi menyalin file env).

Verifikasi di terminal lain:

```bash
curl -s http://127.0.0.1:8000/health            # {"status":"ok"} -- backend hidup, BUKAN bukti model siap
curl -s http://127.0.0.1:8000/runtime/config    # lokasi GPU & model efektif (tanpa rahasia)
curl -s http://127.0.0.1:8000/frontier/status   # frontier aktif?, key_configured true/false
```

## 4. Frontend

```bash
cd frontend
npm install
cp .env.example .env        # VITE_API_BASE_URL=http://127.0.0.1:8000
npm run dev
```

Buka **http://localhost:5173** (atau http://127.0.0.1:5173). Port ini penting: backend hanya menerima
dashboard dari origin `localhost:5173`/`127.0.0.1:5173`, dan pengaturan runtime hanya bisa diubah dari
mesin yang sama dengan backend. Kalau Vite pindah ke 5174 karena 5173 terpakai, hentikan proses lain di
5173 lalu jalankan ulang.

Tiga tab:

| Tab | Isi |
|---|---|
| **Screening aksi korporasi** | scan Scrapling (0 kredit) dan pipeline Sectors |
| **Laporan emiten** | laporan empat panel per ticker, replay, resume |
| **Runtime & GPU** | pilih MacBook/Colab, cek kesiapan, mode frontier |

## 5. Runtime GPU — pilihan A: MacBook

1. Buka aplikasi Ollama. Pastikan model profilmu ada:
   ```bash
   ollama list
   ollama pull qwen2.5:14b      # workstation: juga glm4:9b dan gemma3:12b
   ```
2. Dashboard → **Runtime & GPU** → pilih **MacBook (Ollama lokal)**. Tidak perlu URL atau token.
3. Centang **Buktikan GPU** (memuat satu model sebentar, gratis) → **Cek kesiapan**. Hasil yang diharapkan:
   Backend hidup · Ollama tersambung · Model tersedia · GPU terbukti.
4. **Aktifkan untuk run berikutnya.**

Saat run berlangsung kamu bisa mengecek sendiri:

```bash
ollama ps       # kolom PROCESSOR sebaiknya "100% GPU"
```

`ollama ps` kosong sebelum ada run bukan kegagalan — model baru dimuat saat dipakai.

## 6. Runtime GPU — pilihan B: GPU Google Colab

1. Buka <https://colab.research.google.com> → **File → Upload notebook** → pilih
   `colab/signalgate_gpu_setup.ipynb` dari repo.
2. **Runtime → Change runtime type → T4 GPU** (atau GPU lain).
3. (Opsional) Simpan token tetap di **Colab Secrets** dengan nama `SIGNALGATE_GATEWAY_TOKEN` (≥24 karakter).
   Tanpa itu, notebook membuat token acak baru tiap sesi.
4. **Runtime → Run all.** Notebook hanya berisi **satu sel kode**; unduhan model pertama makan waktu, dan
   menjalankan ulang sel di sesi yang sama tidak mengunduh ulang model. Sel itu memeriksa sendiri:
   tanpa token → 401, dengan token → 200, pull model → 403, info GPU → 200. Lalu mencetak:
   ```
   URL layanan  : https://<acak>.trycloudflare.com
   Token gateway: <token>
   ```
5. Dashboard → **Runtime & GPU** → **GPU Colab** → tempel **URL layanan** (bukan link halaman notebook) dan
   **token** → **Cek kesiapan** → **Aktifkan untuk run berikutnya**.

Hal yang perlu diketahui:

- Ollama di Colab hanya bisa diakses lewat gateway bertoken. Token disimpan backend
  (`data/runtime/secrets.json`, izin 0600) dan tidak pernah ditampilkan ulang di browser.
- **Token terikat ke URL asalnya.** Sesi Colab baru = URL baru: tempel token baru juga. Token lama tidak
  dikirim ke host baru kecuali kamu mencentang persetujuan eksplisit.
- Jangan bagikan output sel notebook (berisi token). Gateway memakai port 11435; port 8080 milik Jupyter
  Server Colab, dan bila dashboard menulis "bukan gateway SignalGate", yang berjalan masih notebook lama.
- Colab putus (URL mati, HTTP 530) → status **terputus**. Backend **tidak** pindah otomatis ke MacBook.
  Jalankan ulang notebook, tempel URL + token baru, aktifkan lagi.
- Kalau Colab putus di tengah run laporan, biasanya run tetap selesai dengan status **perlu pemeriksaan**
  (tahap yang butuh model tercatat "model tidak tersedia"; angka hitungan kode tetap terbit). Setelah
  Colab tersambung lagi, pakai **Putar ulang snapshot** (0 kredit Sectors) untuk membaca ulang dengan model.
- Run yang berstatus **gagal** (mis. dibatalkan, batas waktu, atau error) bisa dilanjutkan dari tab
  **Laporan emiten** → **Lanjutkan dari checkpoint**. Kalau URL/model/mode frontier sudah berubah sejak run
  dimulai, dashboard menampilkan daftar perubahan dan meminta persetujuan; tahap yang sudah selesai tetap
  dipakai dan perubahan tercatat di riwayat run.

### Aktivasi yang ditolak

| Pesan | Artinya | Tindakan |
|---|---|---|
| Ollama belum tersambung | endpoint mati, token salah, atau tunnel kedaluwarsa | cek notebook/Ollama, tempel URL/token terbaru |
| Lokasi belum siap | ada model belum diunduh, atau daftar model tidak terbaca | tunggu unduhan selesai; **Simpan tetap walau belum siap** hanya bila disengaja |
| Token tersimpan terikat ke host lain | URL berganti tanpa token baru | tempel token baru dari notebook |
| Pembuktian GPU ditolak | ada run analisis berjalan | ulangi setelah run selesai (cek tanpa probe tetap bisa) |

Detail keamanan dan provenance: [RUNTIME_GPU.md](RUNTIME_GPU.md).

## 7. Data Sectors (berbayar, opsional)

1. Isi `SECTORS_API_KEY` di `backend/.env` lalu set `SECTORS_API_ENABLED=true`. Restart backend.
2. Perkiraan kredit:

| Aksi | Kredit Sectors |
|---|---|
| Scan Scrapling (tab screening) | **0** |
| Pipeline Sectors (tab screening) | ~34–36 per run |
| Laporan emiten baru | ~13 per run |
| Putar ulang snapshot (replay) | **0** |
| Lanjutkan run gagal | hanya sumber yang belum terambil |

Untuk demo tanpa kredit: jalankan satu laporan live, lalu pakai **Putar ulang snapshot terakhir**.

## 8. Reviewer frontier DeepSeek (berbayar, opsional)

Frontier hanya dipanggil saat pembanding lokal berbeda pendapat atau angka aksi korporasi antarsumber
bertentangan. Label dan skor tetap dihitung Python.

1. Tambahkan ke `backend/.env`, lalu restart backend:
   ```dotenv
   DEEPSEEK_API_KEY=<key-anda>
   ```
   Model bawaan `deepseek-flash` = **DeepSeek-V4.1-Flash**.
2. Dashboard → **Runtime & GPU** → centang **Cek key DeepSeek dan saldo** → **Cek kesiapan**. Ini hanya
   memanggil endpoint saldo dan daftar model, bukan generasi berbayar.
3. Pilih **Reviewer frontier: shadow** → **Aktifkan**. Jalankan beberapa kasus dev. Hasil tampil sebagai
   "shadow — tidak dipakai keputusan", dengan kolom *Akan menjadi*.
4. Setelah yakin, ganti ke **escalation** dengan cara yang sama.

Batas biaya bawaan (estimasi USD, terpisah dari kredit Sectors): **$0,06 per run, $0,60 per hari,
$1,80 total**. Setiap panggilan memesan biaya batas atas lebih dulu; kalau melebihi sisa batas, panggilan
tidak dikirim (`budget_exhausted`). Penghitungnya tersimpan di `data/frontier/ledger.sqlite`, jadi tidak
ter-reset saat restart. Replay laporan bawaannya hanya membaca cache frontier (tanpa biaya baru).
Detail: [FRONTIER_DEEPSEEK.md](FRONTIER_DEEPSEEK.md).

## 9. Uji pertama end-to-end (checklist)

1. `curl http://127.0.0.1:8000/health` → ok.
2. Tab **Runtime & GPU**: cek kesiapan hijau → aktifkan (MacBook atau Colab).
3. Tab **Screening**: **Scan Scrapling (tanpa API)** → kartu muncul, 0 kredit.
4. (Bila Sectors aktif) Tab **Laporan emiten**: isi ticker → **Buat laporan** → empat panel muncul.
   Ringkasan laporan menampilkan lokasi inferensi, pembanding, dan status frontier.
5. **Putar ulang snapshot terakhir** → laporan baru dengan 0 kredit Sectors.
6. (Bila frontier aktif) buka panel **Reviewer frontier** di kartu/laporan: status, alasan eskalasi, token,
   dan estimasi biaya.

## 10. Pengujian otomatis

```bash
cd backend && source .venv/bin/activate && python -m pytest -q
cd frontend && npm run build && npm run lint && npm run test:render
```

Test backend tidak butuh Ollama, Sectors, DeepSeek, atau internet: jaringan HTTP sungguhan diblokir di
`backend/tests/conftest.py`, dan test memakai direktori sementara, jadi ledger budget dan key aslimu
tidak tersentuh. `test:render` merender komponen UI tanpa browser. Keduanya **bukan** bukti GPU, tunnel
Colab, atau DeepSeek sungguhan bekerja — itu diuji manual lewat bagian 5, 6, dan 8.

## 11. Pemecahan masalah

| Gejala | Kemungkinan sebab | Tindakan |
|---|---|---|
| Dashboard kosong / "gagal dimuat" | backend belum jalan, atau `VITE_API_BASE_URL` salah | jalankan backend; cek `frontend/.env` lalu restart `npm run dev` |
| Pengaturan runtime ditolak 403 | dashboard bukan dari `localhost:5173`, atau backend diakses dari mesin lain | buka dari mesin yang sama di port 5173 |
| "Ollama tidak bisa dihubungi" | aplikasi Ollama belum dibuka / notebook mati | buka Ollama atau jalankan ulang notebook |
| "Model … belum diunduh" | profil butuh model yang belum di-pull | `ollama pull <model>` (MacBook) atau jalankan sel unduh di notebook |
| GPU "tidak di GPU (CPU)" | model jatuh ke CPU | tutup aplikasi berat, pakai profil lebih kecil, atau Colab |
| Colab "terputus" / HTTP 530 | sesi/tunnel Colab mati atau URL lama | jalankan ulang notebook, tempel URL + token baru |
| "Gateway menolak token" (401) | token salah atau dari sesi lama | tempel token terbaru dari sel terakhir notebook |
| Resume menampilkan daftar perubahan | URL/model/mode frontier berubah sejak run dimulai | setujui **Lanjutkan dengan konfigurasi baru**, atau kembalikan konfigurasi lama |
| Frontier `unavailable` | `DEEPSEEK_API_KEY` kosong | isi di `backend/.env`, restart |
| Frontier `budget_exhausted` | batas run/hari/total tercapai | tunggu hari berikutnya atau naikkan batas di `.env` dengan sadar |
| Frontier `offline_cache_miss` | replay/offline tanpa respons tersimpan untuk input itu | normal; tidak ada biaya. Set `FRONTIER_CALLS_ON_REPLAY=true` bila memang mau memanggil API |
| Frontier gagal dengan `insufficient_balance` (402) | saldo DeepSeek habis | isi saldo; hasil lokal tetap terbit |
| `/pipeline/run` ditolak | mode hemat (`SECTORS_API_ENABLED=false`) atau key kosong | disengaja; isi key dan aktifkan bila siap |
| Port 8000 terpakai | backend lama masih jalan | periksa prosesnya dulu; atau `uvicorn … --port 8001` dan ubah `VITE_API_BASE_URL` |

## 12. Catatan Windows

- Pakai `scripts\setup.cmd --profile workstation` (perintah `python3` tidak ada di Windows).
- Aktifkan venv: `.venv\Scripts\Activate.ps1` (PowerShell) atau `.venv\Scripts\activate` (cmd.exe).
- Ganti `cp` dengan `copy`, dan jalankan satu perintah per baris (PowerShell 5.1 menolak `&&`).
- GPU AMD RDNA4: lihat bagian "Catatan GPU AMD di Windows" di [README](../README.md).

## 13. Keamanan dan file yang tidak boleh di-commit

Sudah ada di `.gitignore`: `backend/.env` (+ `.env.local`, `.env.colab`), `*.db`, `cases/`,
`data/workflow/`, `data/scans/`, `data/library/`, **`data/runtime/`** (token Colab) dan **`data/frontier/`**
(cache + ledger budget). Jangan menaruh key di `backend/.env.example`, `frontend/.env`, atau variabel
`VITE_*` — file itu ikut repo atau terbaca browser. Sebelum repo dipublikasikan, periksa sekali lagi:

```bash
git status --short
git check-ignore -v backend/.env data/runtime/secrets.json data/frontier/ledger.sqlite
```

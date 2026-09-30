# Laporan audit implementasi frontier dan pilihan GPU SignalGate

Tanggal: 28 September 2026. Sasaran: panduan perbaikan dan pengujian mandiri oleh pemilik proyek.

## 1. Kesimpulan

Implementasi inti frontier sudah tersedia, tetapi belum memenuhi seluruh alur yang diminta: aktivasi backend melalui dashboard, pemilihan GPU MacBook/Colab, menempel endpoint Colab, memeriksa kesiapan, lalu menjalankan analisis dengan frontier opsional.

Hasil verifikasi yang benar-benar dilakukan:

| Pemeriksaan | Hasil |
|---|---|
| Suite backend: `.venv/bin/python -m pytest -q` | 457 passed, 1 warning, 4,23 detik pada pemeriksaan sebelumnya di sesi audit ini |
| Frontend: `npm run build` | TypeScript dan build Vite berhasil |
| Reproduksi retry menggunakan klien palsu | Total token audit hanya memuat percobaan terakhir |
| Reproduksi refresh + offline menggunakan cache sementara | Menghasilkan `offline_cache_miss` meskipun cache yang cocok tersedia |
| Pengujian DeepSeek sungguhan | Belum dilakukan |
| Pengujian GPU MacBook dengan ketiga model | Belum dilakukan dalam audit ini |
| Pengujian GPU Colab/tunnel sungguhan | Belum dilakukan |
| Pengujian UI di browser | Belum dilakukan; build bukan bukti interaksi UI benar |
| Pengukuran akurasi terhadap label manusia | Belum dilakukan |

Audit tidak mengubah kode aplikasi, kredensial, atau bobot skor. Reproduksi tambahan memakai fake client dan direktori sementara; tidak ada panggilan DeepSeek/Sectors. Working tree memiliki banyak perubahan dan file baru yang belum di-commit; laporan merujuk isi yang tersedia saat diperiksa, bukan suatu commit rilis.

Arti prioritas: **P1** = selesaikan sebelum fitur terkait dipakai normal; **P2** = perbaikan keandalan/akurasi audit yang perlu selesai sebelum menyatakan implementasi lengkap. Tidak ada penilaian persentase selesai karena belum ada cakupan pengujian fitur baru yang memadai.

## 2. Matriks implementasi

| Kebutuhan | Status berdasarkan kode | Batas kesimpulan |
|---|---|---|
| Adapter HTTP DeepSeek terpisah dari Ollama | Ada | Belum diuji ke layanan sungguhan |
| Secret key di backend dan status `key_configured` | Ada | Belum dilakukan audit kebocoran menyeluruh |
| Mode frontier off/shadow/escalation | Ada pada konfigurasi backend | Belum ada pengaturan interaktif melalui dashboard |
| Dua reviewer lokal untuk laporan | Ada pada ModelPool | Belum dibuktikan menggunakan GPU sungguhan |
| Aturan rekonsiliasi di Python | Ada | Belum ada suite khusus yang ditemukan |
| Ledger budget persisten | Ada, SQLite dan transaksi reservasi | Jaminan nominal biaya masih memakai estimasi token |
| Cache dan replay offline | Ada | Kombinasi refresh + offline bermasalah, lihat F08 |
| Kronologi aksi korporasi | Ada | Penggabungan identitas/tahap belum memiliki pembuktian regresi khusus |
| Komponen audit dan kronologi di frontend | Ada | Belum diuji interaksi/render seluruh status |
| Pemilih GPU, input URL/token, cek kesiapan | Belum ditemukan | Pengoperasian masih melalui file env dan skrip |
| API aktivasi runtime untuk run berikutnya | Belum ditemukan | GET `/frontier/status` hanya membaca status |
| Gateway autentikasi Colab | Belum ada pada notebook yang diperiksa | Tunnel langsung meneruskan Ollama |
| Konfigurasi efektif yang terikat pada run/resume | Belum memadai | Execute/resume membangun klien dari settings proses saat ini |

## 3. Temuan terperinci

### F01 — P1: alur aktivasi dan pemilih GPU belum tersedia

**Bukti:** `backend/app/main.py:306` menyediakan GET `/frontier/status`; pencarian pada API dan frontend tidak menemukan alur konfigurasi/test/activate runtime, pemilih GPU, atau input endpoint Colab. Dokumentasi aktivasi masih meminta edit env dan restart.

**Dampak:** pengguna belum bisa mengikuti alur yang disepakati langsung dari dashboard. Status `/health` hanya `ok`, bukan bukti Ollama, model, GPU, atau DeepSeek siap.

**Perbaikan:** buat kontrol lokasi inferensi, field endpoint/token Colab, tombol cek koneksi dan aktivasi untuk run berikutnya. Pisahkan status backend hidup, Ollama terhubung, model tersedia, GPU terbukti/tidak diketahui, dan key frontier terkonfigurasi. Tetapkan kontrak API konfigurasi sebelum implementasi frontend. Endpoint baru yang diusulkan bukan endpoint yang sudah ada.

**Uji:** mode lokal tidak membutuhkan field URL; mode Colab menolak link halaman notebook; key tersedia tidak ditampilkan nilainya; perubahan saat run aktif hanya memengaruhi run berikutnya; cek koneksi biasa tidak memanggil generasi DeepSeek berbayar.

**Lulus bila:** seluruh perjalanan pengguna bisa dilakukan dari UI dan konfigurasi efektifnya dapat dilihat secara aman per run.

### F02 — P1: tunnel Colab belum diautentikasi

**Bukti:** `colab/signalgate_gpu_setup.ipynb:24` menjalankan `cloudflared tunnel --url http://localhost:11434`; tidak ada gateway autentikasi pada jalur tersebut. URL `/api/tags` diuji tanpa header autentikasi.

**Dampak:** pihak yang mengetahui URL dapat mengakses layanan Ollama yang diteruskan, termasuk menggunakan sumber daya GPU; path pengelolaan model juga tidak dibatasi oleh proxy khusus dalam notebook.

**Perbaikan:** tunnel diarahkan ke gateway yang mewajibkan autentikasi, dengan daftar endpoint minimum yang diperlukan. Backend meneruskan token, tetapi token tidak disimpan di frontend/localStorage/log. Gunakan HTTPS remote. Lindungi API perubahan endpoint sebagai operasi operator lokal. Ketika fitur input URL dibuat, validasi URL, resolusi DNS dan redirect agar tidak menjadi proxy ke alamat internal/metadata; loopback lokal ditangani sebagai mode khusus.

**Uji:** lakukan hanya pada endpoint Colab milik sendiri. Tanpa token atau token salah harus ditolak; token benar dapat membaca daftar model dan melakukan inferensi; path pengelolaan yang tidak diperlukan ditolak. Jangan mengunduh atau menghapus model untuk menguji penolakan—cukup fixture/mock gateway terlebih dahulu.

**Lulus bila:** URL tanpa token tidak dapat menggunakan layanan, dan token tidak muncul di respons status/audit/browser storage.

### F03 — P1: skrip pergantian GPU menimpa `.env`

**Bukti:** `backend/scripts_local/run_colab.sh:8` berisi `cp .env.colab .env`; `run_local.sh:4` berisi `cp .env.local .env`.

**Dampak:** jika key DeepSeek atau batas biaya baru hanya disimpan di `.env`, menjalankan skrip dapat mengganti konfigurasi tersebut dengan salinan lama. Ini risiko kehilangan konfigurasi, bukan bukti bahwa key pengguna sudah hilang.

**Perbaikan:** pertahankan satu sumber kredensial backend. Pisahkan override lokasi GPU dari konfigurasi umum. Jangan menyalin seluruh file env sebagai cara memilih GPU. Dokumentasikan precedence dan gunakan konfigurasi runtime/override eksplisit.

**Reproduksi aman:** gunakan folder sementara berisi `.env` dengan sentinel `FRONTIER_ENABLED=true` dan `.env.colab` tanpa sentinel. Simulasikan operasi `cp` di folder sementara, bukan di backend asli; sentinel akan hilang.

**Lulus bila:** berganti lokal/Colab tidak mengubah key, mode frontier, atau limit biaya; restart memuat konfigurasi yang dimaksud.

### F04 — P1: pengujian fitur frontier belum memadai

**Bukti:** pencarian `frontier|chronology` di `backend/tests` hanya menemukan pengaturan isolasi pada `conftest.py`. Suite menonaktifkan frontier dan mengosongkan key. Tidak ditemukan suite khusus untuk komponen baru. `docs/FRONTIER_DEEPSEEK.md` juga mengakui belum ada test suite khusus frontier.

**Dampak:** 457 tes lolos membuktikan regresi yang dicakup suite lama, bukan membuktikan transaksi budget, rekonsiliasi, shadow, cache, dan kronologi benar. Ini celah verifikasi; tidak berarti semua fitur tersebut rusak.

**Perbaikan:** tambah pengujian fake HTTP dan fixture deterministik, dengan blok jaringan sehingga tes tidak bisa menggunakan key asli. Pisahkan fixture pengembangan dari holdout. Nama berkas tes yang disarankan: `test_frontier_client.py`, `test_frontier_service.py`, `test_frontier_ledger.py`, `test_frontier_reconcile.py`, `test_chronology.py`, dan `test_runtime_config.py`.

**Lulus bila:** matriks pada bagian 5 dipenuhi dan kegagalan sengaja yang disisipkan terdeteksi oleh tes. Hindari hanya menguji hasil yang mencerminkan implementasi sekarang.

### F05 — P2: reservasi biaya belum layak disebut batas keras

**Bukti:** `backend/app/frontier/pricing.py:40–65` memakai `ceil(jumlah_karakter / 2) + 64` untuk memperkirakan token input, kemudian menyebut biaya sebagai batas atas.

**Dampak:** rasio karakter/token bukan jaminan untuk seluruh teks, Unicode, atau format input. Ketika usage sebenarnya lebih besar dari reservasi, ledger baru mengetahuinya setelah permintaan diproses; pengeluaran dapat melampaui sisa limit yang diharapkan. Belum diuji terhadap tokenizer DeepSeek dalam audit ini.

**Perbaikan:** gunakan penghitung token yang sesuai jika tersedia dan tervalidasi, atau batas konservatif yang terbukti termasuk overhead pesan. Jika masih estimasi, ubah istilah menjadi limit berbasis estimasi, beri margin, dan jangan menjanjikan jaminan nominal absolut. Tabel tarif tetap harus berversi. Pemakaian key di luar aplikasi tidak dicakup ledger.

**Uji:** fixture Indonesia, simbol, Unicode, angka, JSON panjang; usage mock sengaja lebih besar dari reservasi; dua request bersamaan dekat batas; restart/resume dekat batas; timeout tanpa usage. Pastikan overshoot dicatat dan permintaan lanjutan dihentikan.

### F06 — P2: pemilihan bukti frontier kehilangan konteks akhir artikel

**Bukti:** `backend/app/workflow/nodes.py:654` membatasi `isi` berita ke `body.strip()[:1500]`.

**Dampak:** kutipan klaim memang ikut dikirim melalui `claim_view`, sehingga tidak benar jika dikatakan semua bukti kutipan selalu hilang. Namun konteks di sekitarnya—misalnya klarifikasi setelah karakter 1.500—bisa hilang. Frontier berpotensi menilai angka tanpa melihat koreksi atau syarat yang berada sesudahnya.

**Perbaikan:** pilih potongan di sekitar kutipan terverifikasi dan konteks koreksi/tanggal/identitas, bukan hanya awalan artikel. Beri informasi bahwa artikel dipotong. Batasi total input memakai budget yang jelas.

**Uji:** artikel sintetis dengan angka rencana di awal dan revisi eksplisit di akhir; artikel yang menyebut dua aksi berbeda; klaim dengan kutipan pada akhir artikel. Periksa payload fake HTTP sebelum menilai kualitas model.

**Lulus bila:** paket bukti menyertakan konteks yang diperlukan dan tidak menganggap potongan awal sebagai seluruh sumber.

### F07 — P2: audit token mengabaikan percobaan retry sebelumnya

**Status: direproduksi offline.** Lokasi: `backend/app/frontier/service.py:196` mengganti `call.usage` dengan usage terakhir; `_finish` pada baris 325 menjumlah `call.usage`, bukan seluruh `attempt_log`.

**Reproduksi:** fake client mengembalikan JSON invalid pada percobaan pertama dan JSON valid pada percobaan kedua. Keduanya mengembalikan input 100, output 50, total 150 token.

| Nilai | Seharusnya | Aktual |
|---|---:|---:|
| Percobaan | 2 | 2 |
| Input total | 200 | 100 |
| Output total | 100 | 50 |

**Dampak:** pengguna melihat token lebih rendah daripada seluruh pemakaian run. Ledger dan penjumlahan biaya memiliki jalur per-percobaan tersendiri; temuan ini tidak membuktikan seluruh biaya ledger ikut hilang.

**Perbaikan:** agregasikan usage setiap percobaan, termasuk respons invalid yang ditagih. Pisahkan usage asli cache dari usage baru. Untuk percobaan tanpa usage, laporkan total yang diketahui sebagai parsial serta penanda unknown.

**Lulus bila:** skenario di atas menampilkan 200/100 dan tidak menghitung cache hit sebagai token baru.

### F08 — P2: replay offline melewatkan cache jika mode global `refresh`

**Status: direproduksi offline.** Lokasi: `backend/app/frontier/service.py:129–137`.

**Reproduksi:** dengan `frontier_cache_mode='refresh'`, jalankan satu langkah memakai fake client sampai cache tersimpan. Jalankan langkah dengan input identik dan `offline=True`. Hasil aktual `offline_cache_miss`, walau file cache cocok tersedia. Tidak terjadi request jaringan tambahan—larangan jaringan tetap bekerja.

**Sebab:** cabang baca cache dilewati saat global refresh, kemudian cabang offline langsung menghasilkan miss. Runner dapat menghasilkan kombinasi ini untuk replay yang dilarang memanggil API.

**Perbaikan:** offline harus mendapat precedence atas refresh: jika offline, baca cache yang cocok terlebih dahulu; miss hanya jika cache tidak tersedia/rusak. Jangan memperbaiki dengan mengizinkan jaringan.

**Lulus bila:** refresh + offline + cache tersedia menghasilkan cache_hit; refresh + offline + cache kosong tetap offline_cache_miss; kedua kasus tidak menggunakan jaringan.

### F09 — P2: konfigurasi efektif belum terikat secara memadai pada resume

**Bukti kode:** `backend/app/workflow/runner.py:97–125` membuat ModelPool dan FrontierService dari `self.settings` setiap execute, termasuk resume. Checkpoint memuat state, tetapi tidak memaksa endpoint/model/mode yang sama dengan konfigurasi saat run dibuat. Plan menyimpan sebagian metadata, bukan kontrak runtime lengkap.

**Dampak:** restart dengan env berbeda dapat melanjutkan run lama memakai model/endpoint/mode frontier baru tanpa alur perubahan konfigurasi eksplisit. Contohnya run yang dimulai shadow dilanjutkan setelah konfigurasi menjadi escalation. Dampak end-to-end belum direproduksi dalam audit ini; alur konstruksi klien dapat dilihat pada kode.

**Perbaikan:** simpan snapshot konfigurasi efektif nonrahasia per run dan referensi kredensial, bukan nilai key. Tolak perbedaan yang tidak disetujui atau buat catatan migrasi/resume eksplisit. Penggantian URL Colab yang kedaluwarsa harus mempertahankan hasil selesai dan mencatat asal inferensi baru.

**Lulus bila:** resume tidak diam-diam berganti mode keputusan; riwayat menunjukkan konfigurasi sebelum/sesudah beserta tahap yang menggunakan masing-masing konfigurasi.

## 4. Persiapan pengujian mandiri

1. Catat git status dan simpan pekerjaan saat ini dengan cara yang biasa digunakan. Jangan melakukan reset untuk memperoleh lingkungan bersih.
2. Jalankan tes otomatis dalam venv backend yang sudah ada. Jangan mencetak isi file env ke log bersama.
3. Gunakan database/direktori audit sementara untuk tes baru. Jangan menghapus ledger asli karena itu akan mereset catatan budget.
4. Pakai key palsu dan fake transport untuk seluruh pengujian HTTP failure, rate limit, retry, dan budget. Jangan sengaja menghabiskan saldo untuk menguji 402.
5. Gunakan fixture model lokal deterministik saat membandingkan shadow dengan off. Dua run model sungguhan dapat berbeda walaupun input sama; itu dapat mengaburkan pengujian invariansi shadow.
6. Pisahkan pengujian algoritma, layanan sungguhan, dan evaluasi akurasi. Ketiganya menjawab pertanyaan berbeda.

Perintah yang sudah tersedia:

```bash
cd /Users/yhnswp/Desktop/signalgate/backend
.venv/bin/python -m pytest -q
```

```bash
cd /Users/yhnswp/Desktop/signalgate/frontend
npm run build
npm run lint
```

Lint disertakan sebagai langkah yang perlu kamu jalankan; laporan ini belum mengklaim hasil lint. Tes khusus berikut adalah target setelah file tes dibuat, bukan perintah yang saat ini dijamin menemukan tes:

```bash
cd /Users/yhnswp/Desktop/signalgate/backend
.venv/bin/python -m pytest -q tests/test_frontier_client.py tests/test_frontier_service.py tests/test_frontier_ledger.py tests/test_frontier_reconcile.py tests/test_chronology.py
```

## 5. Matriks tes otomatis yang harus ditambahkan

| ID | Kondisi/input | Hasil yang harus dibuktikan |
|---|---|---|
| C01 | Frontier disabled | Tidak ada HTTP frontier; hasil lokal tetap valid |
| C02 | Frontier enabled, key kosong | Status unavailable yang jelas; hasil lokal tidak jatuh |
| C03 | Override model eksplisit + workstation | Precedence terdokumentasi dan konsisten pada kedua jalur |
| C04 | Reviewer tunggal lama | Tetap berjalan, tidak memerlukan format daftar baru |
| H01 | JSON sah, usage sah | Pydantic berhasil, reasoning_content tidak ikut hasil/cache |
| H02 | Content kosong/JSON cacat | Retry terbatas, setiap percobaan tercatat |
| H03 | finish_reason length | Truncated, tidak menjadi supported |
| H04 | HTTP 401/402/422 | Tidak retry tanpa batas; pesan aman tanpa key |
| H05 | 429/500/503 | Retry terbatas, batas panggilan tetap berlaku |
| H06 | Timeout setelah request terkirim | Usage unknown, reservasi tidak dianggap nol |
| H07 | Struktur respons malformed | Error terkontrol, hasil lokal tetap dapat terbit |
| R01 | Kutipan/identitas salah | Frontier tidak dapat membatalkan penolakan mekanis |
| R02 | Reviewer berbeda + frontier stabil | Hanya aturan rekonsiliasi yang diizinkan diterapkan |
| R03 | Frontier berubah pendapat pada second look | Hasilnya tidak dipakai sebagai tie-break stabil |
| R04 | Second look diperlukan, budget habis | Klaim tidak otomatis naik menjadi supported |
| R05 | Shadow terhadap fixture off identik | Label/status/hasil keputusan tidak berubah; anotasi audit boleh bertambah |
| R06 | ID sumber tidak dikenal/alasan kosong | Putusan frontier ditolak |
| B01 | Dua request bersamaan dekat limit | Reservasi atomik; budget tidak lolos dua kali |
| B02 | Job identik masih in-flight | Tidak terkirim dua kali |
| B03 | Restart/resume/retry | Penghitung tetap persisten |
| B04 | Respons invalid lalu sukses | Total token/biaya mencakup semua percobaan yang diketahui |
| B05 | Batas harian/total tercapai | Request dihentikan sebelum HTTP |
| B06 | Usage melebihi perkiraan | Kelebihan tercatat dan tidak ditutupi sebagai hard cap sukses |
| K01 | Bukti/prompt/effort berubah | Kunci cache berubah |
| K02 | Offline cache hit/miss/rusak | Tidak ada jaringan dalam semua cabang |
| K03 | Global refresh + offline replay | Cache tetap dibaca, lihat F08 |
| T01 | Rencana 800 juta -> revisi 640 juta dengan bukti | Kedua tahap dipertahankan; hubungan revisi berbukti |
| T02 | Dua aksi berbeda pada emiten sama | Tidak digabung hanya karena ticker/jenis sama |
| T03 | Tanggal kejadian tidak tertulis | Tidak menyalin tanggal publikasi sebagai tanggal kejadian |
| T04 | Artikel terbaru tanpa bukti revisi | Tidak otomatis dianggap nilai berlaku |
| T05 | Konteks koreksi di akhir artikel | Payload frontier memuat konteks relevan |
| G01 | Endpoint lokal/remote valid | Hasil koneksi, model, dan GPU dibedakan |
| G02 | Link notebook/HTTP remote/token salah | Ditolak dengan petunjuk jelas |
| G03 | URL Colab kedaluwarsa | Status terputus; tidak otomatis pindah mesin |
| G04 | Ganti pengaturan saat run aktif | Run aktif tidak berubah diam-diam |
| G05 | Resume setelah perubahan endpoint/mode | Perubahan eksplisit, provenance tercatat |
| U01 | Laporan lama tanpa field frontier | Tetap dirender |
| U02 | Semua status frontier termasuk partial/unknown | Tidak ada crash dan tidak menampilkan sukses palsu |
| U03 | Audit setelah retry/cache | Angka token/biaya sesuai request baru |

## 6. Pengujian manual lokal dan Colab

### 6.1 Backend tanpa biaya API

Untuk sesi pengujian terpisah, setelah memastikan tidak ada run aktif pada backend yang akan digunakan:

```bash
cd /Users/yhnswp/Desktop/signalgate/backend
SECTORS_API_ENABLED=false FRONTIER_ENABLED=false .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Jika port 8000 sedang digunakan, jangan mematikan proses tanpa memeriksa; pilih port lain dan sesuaikan alamat frontend/curl. Perintah ini tidak menimpa file env, tetapi backend masih dapat membaca konfigurasi direktori/database dari env. Gunakan workspace/data pengujian terpisah jika akan menjalankan analisis.

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health
curl --fail --silent --show-error http://127.0.0.1:8000/frontier/status
```

Harapan: health ok, frontier disabled. Ini belum membuktikan GPU atau model siap.

### 6.2 GPU MacBook M1 Pro

```bash
ollama list
ollama ps
```

`ollama list` memeriksa model yang tersedia, bukan model yang sedang menggunakan GPU. `ollama ps` diamati saat inferensi berlangsung; daftar kosong sebelum model dimuat bukan otomatis kegagalan.

Jalankan fixture/artikel pengembangan dengan frontier off. Catat analis dan dua reviewer yang benar-benar dipanggil, pemakaian memori/swap, durasi, dan kegagalan OOM. Pastikan model dipanggil bergantian dengan offload. Jangan menyatakan 100% GPU hanya dari nama mesin atau keberhasilan HTTP.

### 6.3 GPU Colab

Perbaiki F02 terlebih dahulu. Jalankan notebook pada runtime GPU, pastikan model siap, lalu salin URL layanan yang dicetak notebook, bukan URL halaman notebook. Token harus lewat field rahasia/backend, bukan ditempel pada laporan hasil tes.

Skenario manual: koneksi benar; token salah; model belum tersedia; runtime tanpa GPU; URL lama; sesi putus saat inferensi; URL baru untuk resume. Bukti yang dicatat: status sebelum/sesudah, tahap terakhir selesai, konfigurasi efektif, dan apakah hasil yang sudah selesai tetap ada. Jangan otomatis menyalakan frontier sebagai pengganti GPU yang putus.

Pemilih GPU dan form belum ada saat audit; catat tes UI tersebut sebagai **blocked oleh implementasi**, bukan passed berdasarkan skrip shell.

## 7. Pengujian frontier dan replay

### 7.1 Offline dahulu

Gunakan fake client dengan konflik reviewer deterministik. Buktikan off/shadow/escalation secara terpisah. Jika semua reviewer sepakat dan tidak ada konflik kronologi, status `not_triggered` adalah hasil yang valid; tidak ada panggilan bukan otomatis integrasi rusak.

### 7.2 Smoke test DeepSeek sungguhan, dilakukan sendiri setelah perbaikan

Simpan key secara privat di backend. Pilih satu kasus dev yang benar-benar memicu frontier; gunakan shadow dahulu. Gunakan budget kecil yang cukup untuk dua langkah plus kemungkinan retry, jangan limit terlalu kecil sehingga tes hanya menguji budget_exhausted. Periksa tarif saat pelaksanaan; laporan ini tidak menjanjikan biaya pasti.

Pastikan bukti run menunjukkan: model respons, request ID, independent/second look bila diperlukan, jumlah percobaan, usage, biaya estimasi, status cache, serta label lokal yang tidak berubah pada shadow. Cocokkan pemakaian dengan dashboard provider dengan mempertimbangkan keterlambatan pelaporan. Jangan menjalankan live test kegagalan kuota—gunakan mock.

### 7.3 Replay yang sudah didukung API

Lihat run yang tersedia:

```bash
curl --fail --silent --show-error 'http://127.0.0.1:8000/workflow/runs?limit=10'
```

Setelah memilih run dev dengan snapshot tersimpan, ganti placeholder berikut:

```bash
curl --fail --silent --show-error -X POST http://127.0.0.1:8000/workflow/run -H 'Content-Type: application/json' -d '{"replay_of":"GANTI_DENGAN_RUN_ID_DEV"}'
```

Ini tindakan memulai run baru. Default `FRONTIER_CALLS_ON_REPLAY=false` mencegah panggilan frontier live pada replay; verifikasi konfigurasi sebelum menekan run. Replay masih dapat menjalankan model lokal. Snapshot Sectors yang sama tidak menjamin klaim lokal identik atau cache frontier hit. Untuk reproduksi cache yang deterministik, gunakan keluaran model lokal tersimpan/fake.

Resume run gagal/checkpoint yang dipilih:

```bash
curl --fail --silent --show-error -X POST http://127.0.0.1:8000/workflow/runs/GANTI_DENGAN_RUN_ID_DEV/resume
```

Resume bukan replay baru dan tidak otomatis offline. Jangan menjalankan perintah ini pada run live berbayar tanpa memeriksa konfigurasinya. F09 perlu diperbaiki agar perubahan konfigurasi resume eksplisit.

## 8. Pengukuran manfaat frontier

Sesudah tes fungsional lulus, bandingkan pada dataset dev berlabel manusia yang sama: lokal saja, shadow, dan escalation. Simpan versi model/prompt dan hash bukti. Ukur klaim salah yang diloloskan, klaim benar yang ditolak, konflik belum selesai, biaya per kasus, serta durasi total. Persentase supported yang naik bukan bukti akurasi naik. Gunakan holdout hanya untuk evaluasi akhir, bukan untuk menyesuaikan prompt.

Tetapkan ambang penerimaan sebelum evaluasi. Minimal: tidak ada pelanggaran validasi mekanis, shadow tidak mengubah keputusan, dan hasil escalation tidak memperburuk kesalahan kritis pada dataset yang disepakati. Besarnya peningkatan akurasi belum dapat dinyatakan dari audit kode ini.

## 9. Urutan pengerjaan dan kriteria selesai

1. F03 dan F02: pisahkan konfigurasi dari kredensial; lindungi layanan Colab.
2. F01 dan F09: implementasikan aktivasi GPU dan snapshot konfigurasi per run/resume.
3. F07 dan F08: perbaiki dua bug yang sudah direproduksi, sertakan tes regresi.
4. F05 dan F06: perbaiki reservasi biaya dan pemilihan konteks bukti.
5. F04: lengkapi matriks pengujian, suite regresi, build dan lint.
6. Uji GPU sungguhan dan satu smoke test frontier shadow.
7. Evaluasi terhadap label manusia sebelum memakai escalation sebagai perilaku normal.

Kriteria selesai: UI aktivasi berjalan; URL/token tervalidasi; env pengguna tidak tertimpa; pergantian/resume teraudit; retry/cache/offline/budget terbukti; data lama tetap dirender; mode shadow benar-benar tidak mengubah keputusan; semua hasil tes dicatat dengan bukti; keterbatasan biaya dan GPU didokumentasikan tanpa klaim jaminan yang belum dibuktikan.

## 10. Template pencatatan hasil tes

Salin satu baris per skenario. Gunakan PASS/FAIL/BLOCKED/NOT RUN; jangan mengisi PASS hanya karena tidak ada exception.

| Test ID | Tanggal | Versi kode | GPU/mode frontier | Run/fixture | Expected | Actual | Status | Bukti/log tersensor | Tindak lanjut |
|---|---|---|---|---|---|---|---|---|---|
| F07 | | | fake/shadow | retry invalid -> valid | input 200/output 100 | | | | |
| F08 | | | fake/offline | refresh + cache hit | cache_hit, 0 HTTP | | | | |
| G03 | | | Colab/off | endpoint kedaluwarsa | terputus, tidak pindah mesin | | | | |

Lampirkan request ID, run ID, hash bukti dan log yang relevan; jangan lampirkan API key/token tunnel atau isi lengkap env.

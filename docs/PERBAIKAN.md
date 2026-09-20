# Catatan Perbaikan SignalGate

Riwayat perbaikan, hasil uji, dan rencana lanjutan. Semua angka di bawah berasal dari uji yang benar-benar
dijalankan di mesin pengembangan (MacBook M1 Pro 16 GB), 15–16 September 2026.

Status: **belum di-commit**. Test backend terkini: 90 lolos (lihat bagian 10–11).

---

## 1. Review implementasi loop CLI (Claude → Codex)

Implementasi awal memakai Claude CLI sebagai analis dan Codex CLI sebagai validator. Temuan yang dicek langsung:

| Temuan | Bukti | Perbaikan |
|---|---|---|
| `claude -p --bare` hanya menerima `ANTHROPIC_API_KEY` (login langganan tidak dibaca) — bertentangan dengan syarat tanpa API berbayar | Teks `claude --help` untuk flag `--bare` | `--bare` dibuang; kemudian jalur CLI dihapus seluruhnya |
| CLI `codex` tidak terinstal di mesin | `which codex` kosong, binary tidak ditemukan | Jalur CLI dihapus |
| Folder kasus tertulis di luar repo (`~/Desktop/cases`) | `Path(config.py).parents[3]` = `/Users/yhnswp/Desktop` | Diganti `parents[2]` (root repo) |
| `/research/run` bisa jalan tanpa `SECTORS_API_KEY` — melemahkan hard constraint "Sectors sebagai inti" | Isi `main.py` | Endpoint menolak (HTTP 400) tanpa key |
| `/research/run` mengembalikan verdict mentah yang belum melewati Compliance Gate | Isi `main.py` | Respons memakai verdict hasil `sanitize_for_display` |
| Disclaimer "bukan rekomendasi beli/jual" terhapus dari dashboard | `Dashboard.tsx` | Dikembalikan |
| Test golden rusak (mengimpor modul yang sudah dihapus) | `pytest` gagal di tahap collection | Diganti test baru |

## 2. Pindah ke model lokal (Ollama) dan hemat konteks

Alasan: tidak ada anggaran API berbayar, dan kuota langganan cepat habis karena setiap panggilan membawa
seluruh bukti.

- Model lokal lewat Ollama, tanpa API key atau kredit.
- Hanya kandidat prioritas teratas (default 3) yang diriset.
- Laporan Sectors diringkas; laporan mentah disimpan terpisah.
- Halaman web hanya dikirim paragraf relevan (ticker, nama emiten, istilah aksi korporat), maks 3.000 karakter per sumber.
- Tahap "rencana sumber" terpisah dan review ulang dihapus.
- Hasil `completed` di-cache.
- Prompt yang melebihi `num_ctx` ditolak sebelum dikirim. Bawaan Ollama di mesin ini hanya 4.096 token
  (tercatat di log server) dan konteks lebih panjang dipotong diam-diam; SignalGate mengirim 16.384.

## 3. Arsitektur hybrid: Python memutuskan, model membaca

Dasar keputusan: kode tiga pemenang Alpaca AI Trading Agents Hackathon dibaca langsung (konsep saja, tanpa kode
diambil).

| Pemenang | Peran LLM | Peran Python |
|---|---|---|
| Juara 1 Alpha Hunter | Tidak ada panggilan LLM di kode; banyak metrik disimulasikan acak | Semua agent, risk gate |
| Juara 2 TradePilot | Keputusan BUY/SELL/HOLD dalam JSON schema | Skor kuantitatif, ambang, hak veto |
| Juara 3 VegaGuard | Hanya menjelaskan fakta tervalidasi; ada test bahwa LLM tidak bisa mengubah rencana | Scanner, backtest, risk gate, ±15 ribu baris |

Desain SignalGate:

- Model mengekstrak fakta berkutipan: penerima saham/dana, penggunaan dana, pergantian bisnis, pelepasan
  bisnis lama, injeksi aset.
- Python mencocokkan kutipan, memberi bobot, dan menetapkan label (tabel bobot di `ARCHITECTURE.md`).
- Validator hanya bisa menurunkan label ke `inconclusive`, tidak bisa menaikkan.
- Tanpa model, pipeline tetap jalan dalam mode `deterministic_only`.
- Jalur Claude/Codex CLI dihapus; `LLM_BACKEND` hanya `ollama` atau `off`.
- Perintah uji tanpa Sectors API: `python -m app.scrapling_check`.

## 4. Uji live #1 — satu model qwen2.5:7b (analis sekaligus validator)

Sumber: artikel berita yang diambil Scrapling, tanpa Sectors API.

| Kasus | Hasil | Waktu | Harapan | Penyebab |
|---|---|---|---|---|
| MGLV | inconclusive | 63 dtk | red flag | Artikel tidak menyebut pergantian bisnis furnitur → data center; "pemegang saham lama yang terdilusi" dibaca sebagai penerima; satu pihak dihitung dua kali; validator membuang fakta pengambilalihan piutang NDC yang benar |
| HATM | inconclusive | 32 dtk | growth | "Belanja modal… armada kapal" digolongkan bisnis baru |
| APEX | inconclusive | 27 dtk | bukan red flag | Arah benar, tapi pemegang saham yang terdilusi dibaca sebagai penerima |
| LAPD | inconclusive | 36 dtk | red flag | Fakta benar ditemukan, tapi kutipan sedikit berubah (kata "pengendali" diganti nama PT, "(LAPD)" hilang); cocok 87,6–97% dan ditolak pencocokan persis |
| FORU | red flag 0,70 | 38 dtk | wajar | — |

## 5. Perbaikan setelah uji #1

| Perbaikan | Alasan |
|---|---|
| Validator dipisah ke **qwen3:4b**, analis tetap qwen2.5:7b | Model generasi berbeda supaya kesalahannya tidak sama; di uji #1 validator dari model yang sama membuang fakta yang benar |
| Mode thinking qwen3 dimatikan (`think: false`) | Output JSON cepat; terukur 44 token/detik |
| Mode validator **lenient** (default, bisa `strict` lewat `RESEARCH_VALIDATOR_MODE`) | Fakta hanya dibuang bila `contradicted`; `not_supported` tetap dipakai dengan confidence turun 0,15 per sinyal |
| Pencocokan kutipan toleran: ≥85% berurutan, yang disimpan potongan teks asli | Kutipan LAPD yang benar isinya cocok 87,6% |
| Nama pihak penerima wajib spesifik dan ada di sumber | "Pemegang saham lama" di MGLV dan APEX lolos sebagai penerima |
| Pemegang saham lama dan afiliasi dihitung satu sinyal | Satu pihak MGLV terhitung dua kali |
| Prompt dipertegas: penerima = penyerap saham, belanja modal bisnis yang sama = ekspansi inti, pengambilalihan piutang/aset dari pengendali = injeksi aset | Salah golongan di HATM dan MGLV |

## 6. Uji live #2 — analis qwen2.5:7b, validator qwen3:4b, mode lenient

| Kasus | Uji #1 | Uji #2 | Waktu | Harapan | Penilaian |
|---|---|---|---|---|---|
| HATM | inconclusive | **growth 0,80** | 33 dtk | growth | ✅ Penerima afiliasi + dana untuk armada kapal |
| LAPD | inconclusive | **red flag 0,70** | 34 dtk | red flag | ✅ Inbreng oleh pengendali + divestasi anak usaha lama |
| FORU | red flag 0,70 | **red flag 0,70** | 39 dtk | wajar | ✅ Tetap benar, meski emiten sendiri keliru tercatat sebagai penerima |
| APEX | inconclusive | **inconclusive** | 37 dtk | bukan red flag | ✅ "Kreditur sindikasi luar negeri" ditolak karena terlalu umum |
| MGLV | inconclusive | **growth 0,70** | 62 dtk | red flag | ❌ Label keliru dengan yakin |

Temuan uji #2:

1. Nama "pemegang saham utama NDC" lolos filter karena mengandung "NDC", lalu dihitung growth +2. Pada backdoor
   listing, pengendali baru yang menyuntik dana memang sudah tercatat sebagai pemegang saham.
2. "Rp600 miliar untuk pengembangan fasilitas data center" digolongkan modal kerja (+1). Artikel tidak menyebut
   bisnis lama MGLV adalah furnitur.
3. **Validator qwen3:4b menyetujui 14 dari 14 fakta** di semua kasus, termasuk yang keliru. Pertanyaan
   "didukung atau tidak" hampir selalu dijawab ya oleh model 4B.
4. Ambang growth terlalu rendah: dua fakta lemah (2 + 1) sudah cukup untuk label growth.
5. Setelah uji, hanya qwen3:4b yang tersisa di memori (5,1 GB pada konteks 16.384), jadi Ollama kemungkinan
   bergantian membongkar-muat kedua model; tambahan beberapa detik per kasus.

## 7. Rencana perbaikan berikutnya (belum dikerjakan)

| # | Perbaikan | Dampak yang diharapkan |
|---|---|---|
| 1 | Tolak nama pihak yang mengandung frasa umum ("pemegang saham…") dan pihak yang merupakan emiten itu sendiri | MGLV kehilangan sinyal growth palsu; FORU tidak lagi mencatat emiten sebagai penerima |
| 2 | `growth_catalyst` wajib punya bukti dana untuk bisnis inti | MGLV menjadi inconclusive; HATM tetap growth |
| 3 | Validator menggolongkan kutipan tanpa melihat jawaban analis; Python membandingkan, beda = tidak didukung | Validator tidak lagi sekadar mengiyakan |
| 4 | Uji dengan Sectors API: Python membandingkan industri tercatat di Sectors dengan penggunaan dana/aksi korporat | Pergantian bisnis MGLV ("Household Goods" → data center) terdeteksi deterministik |
| 5 | Atur agar kedua model tetap termuat bersamaan (misalnya konteks validator lebih kecil) | Hilangkan waktu bongkar-muat model |
| 6 | Dukungan PDF keterbukaan informasi IDX | Sumber primer aksi korporasi bisa dipakai |
| 7 | `/pipeline/run` berjalan di background dengan status progres | Dashboard tidak menggantung beberapa menit |
| 8 | Paket demo/replay kasus untuk juri | Juri bisa melihat hasil riset asli tanpa Ollama |

## 8. Lingkungan yang dipakai

| Komponen | Versi / catatan |
|---|---|
| Ollama | Aplikasi desktop 0.34.0 (`/Applications/Ollama.app`), menyala otomatis saat login |
| Analis | `qwen2.5:7b` — 4,7 GB, 100% GPU, ±27 token/detik, muat 3,3 dtk |
| Validator | `qwen3:4b` — 2,5 GB, ±44 token/detik, muat 1,6 dtk |
| Scraper | Scrapling 0.4.15 dari https://github.com/D4Vinci/Scrapling (sama dengan rilis GitHub terbaru v0.4.15) |
| Python backend | 3.13 (python.org) |

## 9. Audit arsitektur dan uji data baru — 16 September 2026

Implementasi kini mempertahankan kedua model Ollama, dengan perubahan berikut:

- Validator mengekstrak sendiri dari sumber; tidak lagi menerima jawaban atau label analis.
- Growth wajib memiliki ekspansi bisnis inti. Fakta yang belum dikonfirmasi tidak dihitung,
  termasuk ketika `.env` lama masih memilih mode lenient.
- Kutipan tidak boleh mengubah kata/angka; toleransi hanya kapitalisasi, spasi, dan tanda baca.
- Filter nama menolak deskripsi peran dengan singkatan serta identitas emiten sendiri.
- Cache memeriksa ulang isi sumber, event lengkap, konfigurasi, digest model dan TTL.
- Endpoint tunggal dan batch berbagi gerbang publikasi; kegagalan memperoleh data perusahaan
  Sectors tidak dapat menghasilkan status selesai.
- WATCH tidak lagi menganggap harga naik/datar sebagai bukti tesis aksi korporasi selesai.
- Skor UI ditandai sebagai skor heuristik, bukan persentase probabilitas.
- `app.evaluate` menambahkan capture, replay tanpa jaringan sumber, dan evaluasi dengan label manusia.

Verifikasi kode: **68 tes backend lolos**, build frontend lolos. Lint frontend selesai dengan satu
warning yang sudah ada tentang pemanggilan state dalam effect pada Dashboard.

Uji baca-saja memakai Sectors company report aktual dan artikel yang ditemukan melalui Sectors news:

| Kasus | Tanggal berita di respons Sectors | Hasil | Waktu |
|---|---|---|---|
| MKNT | 15 September 2026 | needs_review / inconclusive | 101,96 detik |
| HRTA | 10 September 2026 | completed / inconclusive | 60,65 detik |

MKNT: pembaca berbeda tentang beberapa pihak dan pelepasan bisnis, sehingga tidak diterbitkan label
tegas. HRTA: fakta terkonfirmasi berupa modal kerja; penyebutan emiten sebagai penerima eksternal
ditolak, dan modal kerja saja tidak cukup menjadi growth.

Kedua model benar-benar dipanggil. Seusai run, `ollama ps` menunjukkan qwen3:4b 5,1 GB,
100% GPU, konteks 16.384. Ini tidak membuktikan kedua model termuat bersamaan.

Artefak lokal (diabaikan Git): `backend/cases/evaluation-2026-09-16-fresh/manifest.json` dan
`report.json`. Label acuan belum diperiksa manusia: **tidak ada klaim akurasi** dari dua kasus ini.

Rencana pengujian, keterbatasan, dan aturan resmi: [VALIDASI_HACKATHON.md](VALIDASI_HACKATHON.md).
Rencana di bagian 7 di atas adalah riwayat sebelum audit ini; lihat dokumen baru untuk status terkini.

## 10. Rotasi tiga model, mode hemat API, dan scan kata kunci — 16 September 2026

### Rotasi model bergantian di GPU

Urutan per kasus: **qwen2.5:7b (analis) → glm4:9b (pembaca 2) → llama3.1:8b (pembaca 3)**. Setiap model
dilepas dari GPU (`POST /api/generate {"model": ..., "keep_alive": 0}`) sebelum model berikutnya dimuat, supaya
M1 Pro 16 GB (anggaran Metal 11,8 GiB) tidak memuat dua model sekaligus.

- Label hanya terbit bila ketiga pembaca sepakat (bukan suara terbanyak); satu pembaca berbeda → `inconclusive`.
- `ResearchOutcome.model_runs` mencatat giliran, detik per model, dan hasil offload;
  `python -m app.scrapling_check` mencetak garis waktunya.
- Konfigurasi: `OLLAMA_REVIEWER_MODELS=["glm4:9b","llama3.1:8b"]`, `OLLAMA_OFFLOAD_BETWEEN_MODELS=true`.
- Test: urutan run/unload persis, offload bisa dimatikan, label wajib sepakat tiga model.

### Mode hemat API Sectors

`SECTORS_API_ENABLED=false` (default lokal di `backend/.env`) membuat endpoint yang butuh Sectors dan
`app.evaluate capture` menolak sebelum ada request. Pengujian wajib lewat Scrapling/snapshot. Test memastikan
`SectorsClient` tidak pernah dibuat dalam mode ini. `.env.example` tetap `true` untuk juri.

### Scan kata kunci (bukan crawl seluruh IDX)

Uji `app.scan discover --refresh` pertama menemukan 3 kandidat dan **8 kegagalan** "ticker belum teridentifikasi".
Penyebab: di halaman pengumuman idx.id, judul memuat `[ LPKR ]`, tetapi tautan lampiran hanya bernama
`20260915_LPKR_Informasi Transaksi Afiliasi_32148755_lamp6.pdf` dan dulu diperlakukan sebagai artikel terpisah.

| Perbaikan | Hasil pada snapshot yang sama |
|---|---|
| Lampiran dikelompokkan ke judul pengumuman sebelumnya (atau dari pola nama berkas `YYYYMMDD_TICKER_`) | LPKR jadi 1 kandidat dengan 9 PDF; kegagalan 8 → 0; artikel yang diunduh 11 → 2 |
| Ticker dari daftar pengumuman IDX ditandai `ticker_source=idx_announcement_listing` | Tidak perlu tebakan dari teks bebas |
| Kandidat berita tanpa PDF memakai PDF pengumuman IDX dengan ticker **dan** kata kunci yang sama | BNII/MKNT tetap `needs_document` karena tidak ada pengumuman IDX yang cocok di halaman yang dipindai |
| Judul berita dibersihkan dari cap waktu relatif ("4 jam yang lalu") | Headline bersih |

### Kualitas bukti PDF

| Temuan (LPKR, data nyata) | Perbaikan |
|---|---|
| 9 URL lampiran hanya berisi 5 dokumen unik; versi Inggris/Indonesia identik byte | `EvidenceStore` melewati dokumen dengan SHA-256 sama, dicatat di `evidence/duplicate_documents.json`, tidak memakan kuota `RESEARCH_MAX_PAGES` |
| Semua PDF LPKR ditolak `pdf_missing_or_unrelated`: surat pengantar IDX berupa formulir (kolom label lalu kolom nilai), lampiran hanya menulis "PT LIPPO KARAWACI TBK" tanpa ticker, dan tanpa Sectors nama emiten kosong | `idx_form_fields` membaca pasangan label–nilai formulir; bila `Kode Emiten` = ticker kandidat, `Nama Perusahaan` dipakai untuk mengenali lampiran lain (`company_name_source=idx_eform_kode_emiten`). Formulir ticker lain tidak dipercaya |
| Satu halaman tanda tangan kosong (hal. 18 dari 18) membuat seluruh kasus `needs_ocr_or_review` | OCR/review hanya diwajibkan bila >25% halaman satu dokumen minim teks; rincian di `evidence_selection.json` |

`PROMPT_VERSION` naik ke `2026-09-16.documents-5` karena pemilihan bukti berubah; cache lama tidak dipakai.

### Uji live rotasi — LPKR dari PDF IDX (tanpa Sectors, snapshot offline)

Sementara glm4:9b dan llama3.1:8b masih diunduh, rotasi diuji dengan dua model yang sudah ada
(`OLLAMA_REVIEWER_MODELS='["qwen3:4b"]'`): `python -m app.scan research --offline --limit 1`.

| Percobaan | Hasil | Penyebab |
|---|---|---|
| Sebelum perbaikan identitas | `needs_document`, 0 detik, model tidak dipanggil | Semua PDF LPKR ditolak `pdf_missing_or_unrelated` |
| Sesudah perbaikan | `needs_review` / `inconclusive`, `document_status=ready_text` | Lihat di bawah |

Isi `ollama ps` dicatat tiap 3 detik selama run:

| Waktu | Model di memori |
|---|---|
| 02:00:41 | qwen2.5:7b, 5,5 GB (analis, 78,2 dtk) |
| 02:01:54 | kosong (offload berhasil) |
| 02:01:58 | qwen3:4b, 5,1 GB (pembaca 2, 38,4 dtk) |
| 02:02:34 | kosong (offload berhasil) |

Tidak pernah ada dua model termuat bersamaan.

Bukti yang dipakai: 4 PDF unik (surat pengantar, pendapat kewajaran, keterbukaan informasi, laporan penilaian),
48 halaman, 8 potongan / 16.000 karakter hasil retrieval.

Kenapa tidak terbit label:

- Kedua pembaca menemukan pihak yang sama (PT Asiatic Sejahtera Finance dan PT Ciptadana Multifinance), tetapi
  analis menulis relasi `affiliate`, pembaca 2 `new_party`. Beda relasi → fakta dibuang.
  Surat pengantar menyebut "Di bawah pengendalian yang sama", jadi **analis yang benar, qwen3:4b yang keliru**.
  Aturan kesepakatan penuh mencegah label terbit dari bacaan yang salah.
- Kutipan `debt_repayment` dari pembaca 2 hanya "Pembiayaan" (10 karakter, di bawah minimum 12) → diabaikan.
- Transaksinya cessie piutang Rp8,35 miliar antar-afiliasi, tidak cukup untuk growth atau red flag.
  `inconclusive` wajar.

Catatan retrieval: 2 dari 8 potongan berisi daftar dokumen yang ditelaah penilai (boilerplate), tidak berguna
untuk keputusan. Ini dievaluasi setelah uji tiga model.

## 11. Uji live #3 — tiga model bergantian (qwen2.5:7b → glm4:9b → llama3.1:8b)

Unduhan glm4:9b (5,5 GB) dan llama3.1:8b (4,9 GB) selesai. Keduanya lolos uji JSON schema Ollama:
glm4:9b 14,7 detik, llama3.1:8b 15,9 detik, dan offload berhasil.

Perintah: `python -m app.scrapling_check` (5 kasus bawaan, Scrapling, tanpa Sectors API).

| Kasus | Uji #2 (2 model) | Uji #3 (3 model) | Waktu | Harapan | Penilaian |
|---|---|---|---|---|---|
| LAPD | red flag 0,70 | **red flag 0,80**, completed | 137 dtk | red flag | ✅ |
| FORU | red flag 0,70 | **red flag 0,90**, completed | 206 dtk | wajar | ✅ |
| MGLV | growth 0,70 ❌ | **inconclusive**, needs_review | 136 dtk | red flag | Tidak lagi salah yakin; belum menemukan red flag |
| HATM | growth 0,80 | **inconclusive**, needs_review | 111 dtk | growth | ❌ Regresi, lihat di bawah |
| APEX | inconclusive | insufficient_evidence | 61 dtk | bukan red flag | Artikel idxchannel timeout 20 dtk saat itu; model hanya melihat input |

Memori GPU (`ollama ps` tiap 3 detik, 03:35–03:46): selalu tepat satu model termuat. Urutannya qwen2.5:7b
5,6 GB → kosong → glm4:9b 5,9 GB → kosong → llama3.1:8b 6,9 GB → kosong, lalu kasus berikutnya. Puncak 6,9 GB
masih di bawah anggaran Metal 11,8 GiB. Rata-rata sekitar 130 detik per kasus (uji #2 sekitar 40 detik).

### Penyebab regresi HATM dan perbaikan perbandingan fakta

Ketiga model sebenarnya membaca hal yang sama (MSN afiliasi, dana untuk armada), tetapi pembanding terlalu kaku:

| Masalah | Contoh nyata | Perbaikan di `compare_independent_facts` |
|---|---|---|
| Singkatan dalam kurung membuat nama dianggap pihak lain | "PT Multi Sarana Nasional (MSN)" vs "PT Multi Sarana Nasional" | `party_key` membuang isi kurung sebelum membandingkan |
| Satu kalimat dipecah pembaca menjadi dua kategori dianggap bertentangan | qwen: `core_expansion` untuk "belanja modal … atau pembayaran pinjaman bank"; llama: `core_expansion` ("belanja modal") + `debt_repayment` | `contradicted` hanya bila pembaca tidak pernah memberi kategori yang sama pada kutipan itu |
| Kategori sama dari kalimat lain dihitung mendukung (terlalu longgar) | MGLV: "Rp600 miliar … data center" = working_capital "didukung" glm karena glm punya working_capital untuk "Sisa dana …" | use_of_funds wajib kutipan yang tumpang-tindih |

Test baru: 3 (total 87 lolos).

Replay deterministik dari ekstraksi tersimpan dengan logika baru, tanpa memanggil model:

| Kasus | Status fakta analis setelah perbaikan | Label per pembaca | Hasil |
|---|---|---|---|
| HATM | MSN afiliasi ✅✅, ekspansi inti ✅✅ | glm: inconclusive (keliru `asset_injection` pada kalimat penggunaan dana); llama: inconclusive (keliru `business_change` pada "penambahan aset berupa armada kapal") | Tetap inconclusive, sekarang karena pembaca salah baca, bukan karena pembanding |
| MGLV | Rp600 M working_capital ✗ (benar ditolak), ambil alih piutang NDC sebagai debt_repayment ✗/✅ | glm dan llama: **red flag** (business_change + asset_injection) | Tetap inconclusive: analis qwen tidak menandai injeksi aset, jadi tidak sepakat penuh |
| LAPD | PT JSI Sinergi Mas (JSI) afiliasi sekarang ✅✅ | red flag, red flag | Red flag, confidence turun ke 0,60 (lihat usulan 2) |
| FORU | tidak berubah | red flag, red flag | Red flag 0,90 |

Kesimpulan: sesuai aturan kesepakatan penuh, tidak ada label yang naik secara keliru. Dua kasus tertahan
karena **salah baca model**, bukan karena kode.

### LPKR dari PDF IDX dengan tiga model

`python -m app.scan research --offline --limit 1`: hasilnya `needs_review` / `inconclusive`, `document_status=ready_text`.
Waktu per model: qwen2.5:7b 123,6 detik, glm4:9b 122,3 detik, llama3.1:8b 100,7 detik, jadi sekitar 6 menit untuk
16.000 karakter PDF. Offload berhasil 3 kali.

Ketiga model menemukan PT Asiatic Sejahtera Finance dan PT Ciptadana Multifinance sebagai afiliasi, tetapi fakta
tidak lolos:

| Pembaca | Masalah | Perbaikan |
|---|---|---|
| glm4:9b | Kutipan persis benar, tetapi dirujuk ke E009; teksnya ada di E038/E007, jadi semua fakta dibuang | `verified_facts` memindahkan kutipan yang **persis sama** ke halaman yang memuatnya. Teks tidak pernah ditebak; id asli tetap ada di `reviewers/NN/extraction.json` |
| glm4:9b vs analis | Pihak yang sama dikutip dari halaman berbeda (E038 vs E007) dianggap subjek lain | Pihak dicocokkan dengan nama + relasi lintas halaman. Penggunaan dana dan flag tetap wajib halaman yang sama |
| llama3.1:8b | Menyalin sel tabel formulir IDX yang terpotong: "PT Asiatic Sejahtera Finance dan PT Ciptadana" dan "PT Ciptadana" | Tidak diubah: nama terpotong memang tidak boleh dianggap sama |

Hitung ulang dengan logika baru: kedua pihak didukung glm ✅, tidak didukung llama ✗, sehingga label tetap
`inconclusive`. Itu wajar: cessie piutang Rp8,35 miliar antar-afiliasi tanpa penggunaan dana inti tidak cukup untuk
label apa pun. Hitung ulang HATM, MGLV, LAPD, dan FORU tidak berubah dibanding tabel di atas.

### APEX: sumber gagal dan waktu GPU terbuang

- Dua kali `curl: (28) Operation timed out after 20003 ms with 0 bytes` ke idxchannel (03:39 dan 03:53).
  Diagnosis 04:0x dengan UA `SignalGateResearch/1.0`: `robots.txt` HTTP 200 dalam 0,1 detik, artikel HTTP 200 dalam
  0,4 detik. Jadi gangguan sementara, bukan blokir UA. UA jujur dan aturan robots tetap dipakai.
  `ScraplingSource` sekarang mencoba ulang satu kali.
- Saat semua sumber gagal, analis tetap dipanggil dua kali (41 detik) padahal kutipan dari input kandidat tidak
  pernah bisa diverifikasi. Engine sekarang langsung `insufficient_evidence` tanpa memanggil model (test: log
  model kosong).

Ulang APEX setelah retry, dengan sumber berhasil diambil:

| Hasil | Waktu | Rincian |
|---|---|---|
| `needs_review` / `inconclusive` | 70 detik (qwen 16+10, glm 20, llama 23) | Fakta analis: dana untuk melunasi utang ke kreditur. glm mengutip kalimat yang sama tetapi potongannya hanya tumpang-tindih sebagian → tidak dihitung sama; llama tidak mengekstrak penggunaan dana. "kreditur sindikasi luar negeri" dan "Pemegang Saham Lama" ditolak karena terlalu umum. glm keliru menandai `asset_injection` pada kalimat penerbitan saham |

Penilaian: sesuai harapan (konversi utang, bukan red flag), tidak ada label yang terbit keliru.

Test backend: 90 lolos.

### Usulan berikutnya (belum dikerjakan, perlu keputusan)

| # | Usulan | Alasan |
|---|---|---|
| 1 | Pembaca kedua/ketiga juga boleh mengusulkan fakta yang tidak ditemukan analis, dan fakta terbit bila ketiganya sepakat | MGLV: dua pembaca menemukan injeksi aset yang dilewatkan analis; saat ini hanya fakta analis yang diperiksa |
| 2 | Penerima afiliasi tidak dihitung growth bila ada `asset_injection` dari pihak yang sama | LAPD: inbreng oleh pengendali justru bagian pola red flag, tetapi afiliasi memberi +2 growth |
| 3 | Contoh singkat (few-shot) di prompt: "menambah armada = core_expansion, bukan business_change/asset_injection" | Salah baca glm/llama pada HATM |
| 4 | Waktu proses sekitar 130 dtk/kasus (PDF sekitar 6 menit): jalankan di background dengan progres, cache hasil per model | Rotasi tiga model menambah biaya muat model |
| 5 | Kutipan penggunaan dana dianggap sama bila berbagi rangkaian kata panjang (misalnya ≥8 kata berurutan), bukan hanya bila salah satu memuat yang lain | APEX: glm dan analis mengutip kalimat yang sama dengan potongan berbeda |

## 12. Evaluasi label manusia pertama, dua bug ditemukan, pindah ke profil workstation — 19 September 2026

Alat baru `app/workflow/labels.py` (ekspor lembar CSV buta dari status sistem → manusia menilai →
`score` menggabungkan). Panduan dan rubrik di `docs/PANDUAN_LABEL.md`. 11 emiten dijalankan live
(~143 kredit): dev IDEA/BBRI/TLKM/INDR/MKNT/HATM/PTBA, holdout BMRI/ICBP/TFCO/LAPD/MGLV (belum
dilabel/dibuka, run_id tersimpan di `data/labels/holdout-runs.txt`).

### Skor awal (analis qwen2.5:7b, pembanding gemma3:4b), 57 klaim dari 7 emiten dev

Presisi ketat 88% (CI95 72–95%), presisi longgar 91%, 8/12 klaim salah tertangkap, **9 klaim benar
ikut tertolak**. Dua bug ditemukan langsung dari daftar kerja `score` ("lolos padahal bermasalah" /
"ditolak padahal benar"):

### Bug A — PB dihitung dari ekuitas yang salah (FIXED)

`valuation:pb_mrq_calc` (`calculations.py`) membagi kapitalisasi pasar dengan `total_equity`, yang
memuat ekuitas anak usaha milik minoritas. Untuk TLKM (anak usaha signifikan) itu ~12% dari total
ekuitasnya.

| Emiten | PB lama | PB Sectors sendiri | Broker pengguna |
|---|---|---|---|
| TLKM | 1,88x | 2,17x | ±2,14x |
| BBRI | 1,51x | 1,55x | 1,56x |
| HATM | 3,16x | 3,21x | 2,93x (tidak berubah setelah fix — lihat catatan di bawah) |

PB Sectors sendiri (dari `peers.self.pb_mrq`) konsisten lebih dekat ke broker karena Sectors membagi
dengan ekuitas milik induk saja. Perbaikan: metrik baru `fundamental:parent_equity_latest`
(`stockholders_equity`, fallback ke `total_equity` bila tidak ada) dipakai untuk `pb_mrq_calc`;
ditambah pengecekan konflik PB-vs-Sectors meniru yang sudah ada untuk PE. Replay TLKM setelah fix:
1,88x → 2,14x, sekarang cocok dengan Sectors (2,17x) dan broker (±2,14x). Dikonfirmasi di 5 emiten
lain, tidak ada regresi (HATM/PTBA/INDR yang minoritasnya kecil/nol nyaris tidak bergeser — sesuai
dugaan; sisa selisih HATM vs broker berarti bukan bug ini, kemungkinan waktu snapshot dekat private
placement barunya). 3 test baru, 428 test lolos.

### Bug B — Pembanding gemma3:4b salah baca tanda metrik sendiri (FIXED via ganti model)

Penyebab utama 7 dari 9 klaim benar yang tertolak. Contoh nyata dari catatan pembanding sendiri untuk
klaim BBRI ("Pertumbuhan pendapatan menurun sebesar 9,2%", metrik = -9,2%):

> *"Pernyataan tersebut menyatakan penurunan pertumbuhan pendapatan tahunan sebesar 9,2%, sedangkan
> data menunjukkan pertumbuhan sebesar +9,2%"*

Pembanding membalik tanda metrik yang seharusnya cuma dibaca ulang, lalu menandainya `contradicted`.
Kasus lain menukar metrik FY dengan metrik YoY kuartalan sama sekali ("data menunjukkan +22,3%" untuk
klaim laba FY yang metriknya -5,8%; +22,3% adalah `earnings_growth_yoy`, metrik berbeda). Percobaan
pertama: perbaikan instruksi prompt (larang analis menggabungkan "menurun/turun" dengan tanda minus,
tambah contoh penjelas di instruksi pembanding) — **tidak cukup**. Replay BBRI setelah perbaikan
prompt: analis menulis frasa yang benar ("menurun sebesar 9,2%", tanpa minus), pembanding tetap
menolak dengan alasan yang sama persis (tanda dibalik), plus dua klaim yang sebelumnya lolos ikut
rusak.

Uji definitif: evidence dan 8 klaim BBRI yang identik, hanya model pembanding diganti (replay, 0
kredit).

| Pembanding | Hasil pada 8 klaim BBRI yang sama |
|---|---|
| gemma3:4b | 5 dari 8 klaim benar ditolak keliru |
| glm4:9b | 8 dari 8 lolos benar |

Dikonfirmasi pada skala lebih besar: replay 5 emiten dev (BBRI, HATM, INDR, PTBA, MKNT; 37 klaim
model, evidence dan tulisan analis identik, hanya pembanding diganti) — tingkat lolos naik dari
13/37 (35%) ke 27/37 (73%). Catatan: kenaikan tingkat lolos sendiri belum membuktikan akurasi
(pembanding yang asal menyetujui juga akan menaikkan angka ini); bukti akurasinya tetap perbandingan
terhadap label manusia di BBRI di atas — angka skala besar ini hanya menunjukkan arahnya konsisten.

Kesimpulan: bukan soal gaya bahasa, gemma3:4b (4B parameter) tidak cukup andal untuk tugas
bandingkan-angka ini. Keputusan: **tidak ada model di bawah 8B parameter lagi** di proyek ini,
untuk peran apa pun.

### Perubahan konfigurasi

- `backend/.env`: `SIGNALGATE_PROFILE=workstation` menggantikan `OLLAMA_MODEL`/`OLLAMA_REVIEWER_MODELS`
  eksplisit → analis `qwen2.5:14b`, pembanding rotasi `glm4:9b` lalu `gemma3:12b`, timeout 900 dtk.
  Mac pengembangan ini 16 GB unified memory (dicek `sysctl hw.memsize`), sesuai target profil.
- `scripts_local/run_live.py`: berhenti menimpa `workflow_analyst_model`/`workflow_reviewer_model`
  secara hardcode; sekarang ikut profil kecuali argumen CLI diisi eksplisit.
- Instruksi prompt `RESEARCH_INSTRUCTION` dan `REVIEW_VERDICT_INSTRUCTION` (`prompts.py`) tetap
  diperbaiki (frasa "menurun sebesar -X%" dilarang, contoh penjelas untuk pembanding) — tidak
  menyelesaikan Bug B sendirian, tapi tetap kebersihan penulisan yang benar untuk model manapun.
- Status saat catatan ini ditulis: unduhan `qwen2.5:14b` + `gemma3:12b` masih berjalan (koneksi
  lambat, ~1-1,5 jam), dan replay ulang 7 emiten dev dengan analis qwen2.5:7b (sementara, sambil
  menunggu 14b) + pembanding glm4:9b sedang berjalan di latar belakang.

### Belum diperbaiki, dicatat untuk lanjutan

| Temuan | Contoh | Keputusan |
|---|---|---|
| HATM: 3 klaim private placement, 3 angka saham berbeda (640jt/7,37%, 868jt/10%, 800jt/9,22%) | Ternyata dari 4 artikel tanggal berbeda meliput tahap berbeda aksi korporasi yang sama (rencana → revisi → selesai); bukan salah baca | Perlu keputusan produk: artikel mana yang diutamakan saat angka berubah dari waktu ke waktu. Belum dikerjakan |
| Kode terlalu ketat menolak selisih dua metrik yang dikutip benar | TLKM: "margin naik 2,0 poin" dari 14,2%→16,2%, ditolak karena `unexplained_numbers` tidak menghitung delta antar-metrik | Dampak kecil (1 kasus di 57 klaim), butuh desain hati-hati (kombinasi metrik mana yang bermakna); belum dikerjakan |

## 13. Instalasi qwen2.5:14b selesai, uji replay 0-kredit, bug identitas emiten ditemukan dan diperbaiki — 19 September 2026

Unduhan `qwen2.5:14b` (terhenti sebelumnya, koneksi lambat) dilanjutkan sampai selesai: `ollama show`
mengonfirmasi 14,8B parameter, Q4_K_M, context length 32768, digest `7cdf5a0187d5`. `gemma3:12b` belum
diunduh (di luar cakupan sesi ini). `.env` (`SIGNALGATE_PROFILE=workstation`) sudah menunjuk ke model ini
tanpa perlu override lain, jadi berlaku otomatis.

**Smoke test** (API Ollama langsung, `num_ctx=4096`): jawaban koheren, ~13 token/detik, load 8,7 detik.

**Uji fungsional**: replay 0-kredit (`scripts_local/run_live.py <TICKER> glm4:9b <run_id lama>`) di dua
emiten dev yang evidence-nya sudah tersimpan, analis `qwen2.5:14b` dari profil + pembanding tetap
`glm4:9b` (validasi Bug B). Tidak menyentuh run_id holdout.

### Bug ditemukan — `identity_issue` menolak parafrase nama BUMN tanpa "(Persero)" (FIXED)

Replay TLKM pertama: 6 dari 6 klaim model di panel news ditolak dengan alasan sama, "Klaim tidak
menyebut emiten". qwen2.5:14b menulis "PT Telkom Indonesia Tbk" (parafrase wajar, tanpa anotasi bentuk
badan hukum), sementara `_identity()` (`app/workflow/nodes.py`) hanya menghasilkan tiga bentuk dari nama
resmi ("TLKM", "PT Telkom Indonesia (Persero) Tbk", "Telkom Indonesia (Persero)") — ketiganya
mensyaratkan literal "(Persero)" hadir di kalimat, jadi tidak ada yang cocok.

Ini bukan bug baru dari 14B; baseline interim `qwen2.5:7b` sebelumnya **tidak pernah sampai
menghasilkan klaim news sama sekali** untuk TLKM/BBRI (lihat `model_gap`: "output terpotong batas
token" di run-run awal), jadi celah ini baru kelihatan sekarang setelah analis yang lebih besar berhasil
mengekstrak klaim.

Perbaikan: `_name_variants()` (baru) menghasilkan varian nama tanpa anotasi kurung selain varian
tanpa "PT"/"Tbk" yang sudah ada, dipakai oleh `_identity()`. 4 test baru di
`tests/test_workflow_graph.py` (`NameVariantsTests`), termasuk regresi langsung memakai kalimat asli
qwen2.5:14b yang tertolak. Full suite: 432 lolos (naik dari 428).

Replay ulang TLKM setelah fix: 0 dari 6 klaim ditolak karena identitas (turun dari 6/6). Replay BBRI
(base run pra-Bug-B, analis+pembanding lama) dengan analis 14B: seluruh klaim "PT Bank Rakyat Indonesia
Tbk" juga lolos pemeriksaan identitas. Fundamental & valuation tidak berubah oleh fix ini (di luar
domain news, `identity_issue` tidak berlaku).

### Temuan baru, belum diperbaiki — pembanding news kehabisan konteks

Setelah fix di atas, klaim news TLKM/BBRI tidak lagi tertolak identitas, tapi mentok masalah lain:
pembanding `glm4:9b` gagal untuk *semua* klaim news pada kedua emiten — "prompt 24422–24673 karakter
melebihi batas konteks 21504" — sehingga klaim tertahan status `pending`, bukan `supported`/`rejected`.
Kemungkinan besar ini juga tersembunyi sebelumnya oleh bug identitas (klaim keburu tertolak duluan
sebelum sampai ke pembanding). Butuh keputusan: pecah batch klaim news per panggilan pembanding, atau
naikkan `workflow_num_ctx`/budget khusus role reviewer-news, atau pangkas panjang kutipan yang dikirim.
Belum dikerjakan.

### Sanity lain yang tercatat dari kedua replay

- BBRI: mekanisme `comparison_issues` menangkap sendiri klaim PE TTM dan PB MRQ 14B yang salah arah
  ("7,97x sedikit lebih tinggi" padahal Sectors 8,02x lebih tinggi) — validator kode bekerja seperti
  dirancang, bukan bug baru.
- Memori/performa (M1 Pro 16 GB): `qwen2.5:14b` resident 100% GPU ~10 GB saat aktif, unload bersih antar
  role (`OLLAMA_OFFLOAD_BETWEEN_MODELS=true`), swap naik ke ±1 GB lalu pulih, tidak ada crash. Satu run
  penuh (analis+pembanding, replay) ≈ 6,6–8,2 menit, 0 kredit Sectors.

Status: **belum di-commit** (fix `_name_variants` + test baru termasuk di working tree, sama seperti
perubahan lain yang tercatat di file ini).

## 14. Key Sectors cadangan dengan rotasi otomatis — 20 September 2026

User punya key Sectors kedua (kuota/kredit terpisah dari yang lama). Dipasang sebagai cadangan yang
otomatis dipakai kalau key utama kehabisan kredit, bukan pengganti — mengurangi risiko run macet di
tengah demo juri hanya karena satu key habis kuota.

### Perubahan

- `backend/app/sectors/client.py`: `SectorsClient` menerima satu key atau daftar key. `get()` kini
  membaca `code` di body error JSON (`insufficient_credits`, `monthly_limit_exceeded`,
  `subscription_not_active`, `subscription_does_not_allow` — sesuai
  [changelog v2 Sectors](https://docs.sectors.app/api-references/v2/changelog), entri "structured
  error codes" 2026-07-31) atau HTTP 429, lalu pindah ke key berikutnya dan mengulang permintaan yang
  sama. Error lain (mis. 500/400/503 `service_unavailable`) langsung dilempar tanpa mencoba key
  cadangan, karena semua key akan gagal dengan cara yang sama — mencoba lagi cuma buang panggilan.
  Key duplikat otomatis digabung supaya key mati tidak dicoba dua kali secara identik.
- `backend/app/config.py`: field baru `sectors_api_key_backups` (`SECTORS_API_KEY_BACKUPS`, JSON list)
  + properti `sectors_api_keys` yang menggabungkan key utama dan cadangan tanpa duplikat.
- `backend/app/main.py` (3 titik) dan `backend/app/evaluate.py`: konstruksi `SectorsClient` sekarang
  memakai `settings.sectors_api_keys` (daftar), bukan `settings.sectors_api_key` (satu string).
- `backend/.env.example`: dokumentasi `SECTORS_API_KEY_BACKUPS=[]`. `backend/.env` (tidak di-commit)
  sudah diisi key kedua sebagai cadangan.
- 5 test baru di `tests/test_sectors_client.py` (`KeyRotationTests`): rotasi saat kredit habis, rotasi
  saat HTTP 429 tanpa `code` yang dikenali, error tak terkait langsung dilempar (key cadangan tidak
  ikut kepakai), key habis di semua slot tetap melempar error, key duplikat tidak diulang dua kali.
  Full suite: 437 lolos (naik dari 432).

### Catatan

HTTP status persis yang dipakai Sectors untuk tiap `code` tidak didokumentasikan publik (changelog
cuma menyebut 503 untuk `service_unavailable`); rotasi jadi bergantung ke field `code` di body, bukan
status code, supaya tidak salah tebak lalu diam-diam menyembunyikan error lain sebagai "kuota habis".

Status: **belum di-commit**.

## 15. Klaim berita pembanding kehabisan konteks (dari temuan #13) — diperbaiki, 20 September 2026

Kelanjutan temuan belum-diperbaiki di bagian 13: `glm4:9b` gagal untuk semua klaim news TLKM/BBRI
karena `verdict_packet` (packet + catatan independen + daftar klaim) tidak pernah dihitung ke budget
karakter, hanya packet mentahnya saja. Ini bug kode di `review_node`, bukan soal kapabilitas model —
`max_prompt_chars` adalah pagar yang kita pasang sendiri dari `WORKFLOW_NUM_CTX`/`WORKFLOW_NUM_PREDICT`,
berlaku sama untuk model apa pun di peran reviewer.

### Perbaikan pertama (belum cukup) — batch klaim per panggilan

`app/workflow/prompts.py`: fungsi baru `claim_batches()` memecah daftar klaim jadi beberapa batch yang
masing-masing muat di sisa budget, dipakai `review_node()` supaya panggilan "putusan pembanding" tidak
lagi satu panggilan raksasa berisi semua klaim sekaligus. Packet juga dibangun ke budget instruksi
putusan (lebih ketat dari budget instruksi notes), bukan cuma budget notes seperti sebelumnya.

Replay 0-kredit TLKM (`scripts_local/run_live.py TLKM glm4:9b TLKM-2026-09-19-4c5d4284`) langsung
sembuh: 2 panggilan putusan terpisah (5222 & 8476 karakter), tidak ada klaim `pending`. Tapi replay
BBRI (`BBRI-2026-09-19-1214d525`) masih gagal berulang — 10 panggilan gagal berturut-turut, semua di
kisaran 21818–21920 karakter.

### Bug kedua, ditemukan dari replay BBRI — packet tidak menyisakan ruang untuk catatan independen

Sebab: packet berita di-fit sampai HAMPIR PENUH ke budget-nya (`fit()` selalu greedy mengisi sampai
batas), lalu `catatan_independen_anda` (hasil panggilan pertama, ukurannya baru diketahui setelah
panggilan itu selesai) ditambahkan di atasnya tanpa disisihkan ruang dari awal. Untuk BBRI, packet +
catatan independen SENDIRIAN sudah menghabiskan budget putusan sebelum satu klaim pun ditambahkan,
jadi `claims_budget` jatuh ke 0 dan setiap klaim terpaksa dipaksa masuk sendirian (jalur darurat di
`claim_batches()`) — dan tetap overflow karena memang tidak ada ruang sama sekali.

Perbaikan: `notes_reserve = settings.workflow_num_predict * CHARS_PER_TOKEN` (batas keluaran maksimum
model untuk panggilan catatan independen, ±3.072 karakter di profil workstation) disisihkan dari budget
packet SEJAK DIBANGUN, bukan baru dikurangi setelah notes-nya balik.

### Verifikasi

Replay ulang BBRI dengan fix kedua: 5 panggilan putusan, semua `ok=True` (4983–20544 karakter, semua di
bawah batas 21504), tidak ada satupun error konteks. Keenam klaim berita mendapat putusan nyata (5
`supported`, 1 `unsupported` karena kutipan memang tidak ditemukan — bukan overflow). Replay TLKM tetap
bersih di kedua fix.

- `tests/test_workflow_prompts.py` (baru): 4 test murni untuk `claim_batches()` — muat satu batch,
  pecah ke beberapa batch dan tetap mencakup semua klaim, klaim tunggal kelebihan ukuran tetap dapat
  batch sendiri (tidak infinite loop), daftar kosong.
- `tests/test_workflow_graph.py`: 1 test baru (`test_review_splits_many_claims_into_batches_...`) — 6
  klaim berita dengan budget kecil menghasilkan >1 panggilan `ReviewVerdicts`, semua klaim tetap dapat
  putusan. Helper `pool()` dan `FakePool` diberi parameter `budget` opsional.
- `backend/scripts_local/run_live.py`: ikut dipindah ke `settings.sectors_api_keys` (lihat bagian 14).
- Full suite: 442 lolos (naik dari 437).

Status: **belum di-commit**.

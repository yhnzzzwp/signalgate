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

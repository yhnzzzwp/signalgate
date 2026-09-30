# Reviewer frontier DeepSeek (opsional, berbayar)

SignalGate tetap berjalan penuh dengan model lokal (Ollama). Reviewer frontier adalah lapisan
**opsional** di atasnya: DeepSeek membaca bukti terpilih ketika pembanding lokal berbeda pendapat atau
angka aksi korporasi lintas waktu belum terselesaikan. Frontier **tidak pernah** menentukan label, skor,
atau status terbit — itu tetap Python.

Semua parameter API diverifikasi dari dokumen resmi DeepSeek pada 28 September 2026:
[harga & model](https://api-docs.deepseek.com/quick_start/pricing/),
[thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/),
[JSON mode](https://api-docs.deepseek.com/guides/json_mode/),
[chat completion](https://api-docs.deepseek.com/api/create-chat-completion),
[kode error](https://api-docs.deepseek.com/quick_start/error_codes).

## Yang benar-benar dikirim

| Parameter | Nilai | Catatan |
|---|---|---|
| endpoint | `POST {FRONTIER_BASE_URL}/chat/completions` | format OpenAI, adapter `app/frontier/client.py` (terpisah dari Ollama) |
| `model` | `deepseek-flash` | = **DeepSeek-V4.1-Flash** (baris "MODEL VERSION" halaman harga). Nama lama `deepseek-v4-flash` juga dilayani V4.1-Flash |
| `thinking` | `{"type": "enabled"}` | |
| `reasoning_effort` | `high` (`low`/`high`/`max`) | |
| `response_format` | `{"type": "json_object"}` | prompt memuat kata "json" dan contoh bentuk jawaban |
| `max_tokens` | `8192` | mencakup token penalaran; jawaban `finish_reason=length` dicatat `truncated`, tidak dipakai |
| `temperature` | tidak dikirim | diabaikan di thinking mode |

Hanya `message.content` (jawaban akhir) yang di-parse lalu divalidasi Pydantic
(`app/frontier/prompts.py::FrontierReview`). `reasoning_content` tidak pernah disimpan, digabung ke
JSON, atau ditampilkan.

## Langkah aktivasi (dilakukan sendiri)

1. **Isi key di `backend/.env` saja** (bukan `frontend/.env`, bukan variabel `VITE_*`, bukan
   `.env.local`/`.env.colab` — skrip run tidak lagi menyalin file env, jadi `.env` adalah satu-satunya
   sumber kredensial backend):
   ```dotenv
   DEEPSEEK_API_KEY=<key-anda>
   ```
   Key Sectors dan cadangannya tidak berubah. Restart backend setelah mengubah `.env`.
2. **Pilih mode dari dashboard.** Tab **Runtime & GPU** → *Reviewer frontier*: `mati` / `shadow` /
   `escalation` → **Aktifkan untuk run berikutnya**. Shadow/escalation hanya bisa dipilih bila key sudah
   terisi di backend. Alternatif lama tetap berlaku selama belum pernah diaktifkan dari dashboard:
   `FRONTIER_ENABLED=true` + `FRONTIER_MODE=shadow` di `.env`.
3. **Cek key tanpa biaya.** Centang *Cek key DeepSeek dan saldo* lalu **Cek kesiapan**: backend hanya
   memanggil `GET /user/balance` dan `GET /models` (endpoint metadata), tidak pernah generasi berbayar.
   `GET /frontier/status` juga hanya memuat `key_configured: true/false`, tidak pernah nilai key.
4. **Shadow dulu.** Jalankan beberapa run dev (bukan holdout). Hasil frontier tampil di kartu screening dan
   panel laporan sebagai "shadow — tidak dipakai keputusan", dengan kolom *Akan menjadi*.
5. **Escalation setelah yakin**, dari dashboard yang sama. Run yang sedang berjalan tidak ikut berubah;
   resume run lama dengan mode berbeda harus disetujui eksplisit dan tercatat di riwayat run.

Kalau frontier aktif tetapi key kosong: backend tetap start, frontier berstatus `unavailable` dengan pesan
yang menyebut apa yang harus diisi, dan hasil lokal tetap terbit.

## Kapan frontier dipanggil

Hanya bila ada **pemicu**:

- `reviewer_conflict` — pembanding lokal berbeda putusan untuk klaim/fakta yang sama. Butuh ≥2
  pembanding lokal (profil `workstation`: `glm4:9b` lalu `gemma3:12b`). Dengan satu pembanding tidak
  ada konflik antarpembanding, jadi pemicu ini tidak muncul.
- `label_conflict` (screening) — pembanding berbeda soal label; fakta tambahan dari pembanding yang
  tidak setuju ikut dinilai.
- `timeline_conflict` (laporan emiten) — angka aksi modal yang sama berbeda antarsumber tanpa bukti
  revisi (lihat Kronologi).

Klaim yang gagal pemeriksaan mekanis (kutipan palsu, identitas salah, angka/perhitungan tidak valid,
Compliance Gate) **tidak pernah dieskalasi**. Bukti yang hilang bukan pemicu.

Setiap eskalasi berisi dua langkah, keduanya dihitung ke budget:

1. `independent` — frontier membaca bukti (artikel terpilih + ID sumber + tanggal terbit, metrik,
   aksi korporasi Sectors, angka dari kutipan) dan menilai klaim **tanpa** melihat pendapat model lokal.
2. `second_look` — hanya untuk klaim yang putusan independennya berbeda dari pembanding lokal:
   frontier melihat pembacaannya sendiri dan pendapat lokal, lalu menilai ulang dari bukti.

## Aturan rekonsiliasi (`app/frontier/reconcile.py`)

| Aturan | Kondisi | Hasil |
|---|---|---|
| R0 | klaim gagal cek mekanis | status lokal tetap; frontier tidak bisa menganulir |
| R1 | frontier tanpa putusan sah (gagal, budget habis, cache miss offline, ID bukti tak dikenal, tanpa alasan) | status lokal tetap |
| R2 | putusan berubah antara langkah 1 dan 2, atau langkah 2 perlu tapi tidak berjalan | tidak dipakai; status lokal tetap |
| R3 | pembanding lokal berbeda dan frontier (stabil) sama dengan salah satunya | putusan itu dipakai |
| R3_no_match | frontier berbeda dari semua pembanding lokal | konflik tetap; status lokal (pesimistis) |
| R4 | pembanding lokal sepakat `supported`, frontier stabil lebih pesimistis | turun ke `unsupported` |
| R5 | selain itu | status lokal tetap; frontier tidak pernah menaikkan status yang disepakati lokal |

Screening: label tetap dihitung Python dari fakta akhir. Konflik label dianggap selesai hanya bila
**setiap** fakta yang dipersengketakan diputus R3 dan setiap fakta tambahan pembanding yang tidak setuju
ditolak lewat R3; selain itu hasilnya tetap `inconclusive`/`needs_review`.

Mode **shadow** menjalankan aturan yang sama tetapi hanya mencatat hasilnya (`local_outcome` vs
`reconciled_outcome`, `decision_changes`); label, status, dan laporan yang terbit tidak berubah.

## Kronologi aksi korporasi (`app/research/chronology.py`)

Contoh kebutuhan produk: beberapa artikel bertanggal berbeda menyebut angka private placement yang
berbeda (kasus seperti HATM). Aturannya:

- Tanggal terbit sumber dipisah dari tanggal kejadian. Tanggal kejadian hanya dipakai bila tertulis di
  kutipan/teks sumber; tanggal dari model yang tidak ditemukan disimpan sebagai `event_date_claimed`.
- Tahap (`plan`/`revision`/`approval`/`realization`) dari kata kunci kutipan — heuristik yang dicatat di
  `stage_basis`. Ketentuan (rencana/persetujuan/revisi) dan realisasi dibandingkan terpisah.
- Aksi dipisah per jenis dan per identitas yang **tertulis di bukti** (mis. nomor surat, diverifikasi
  ada di teks sumber). Tanpa identitas, beberapa sumber sejenis ditandai "hubungan belum terbukti".
- Revisi dihubungkan hanya bila kutipan revisi memuat kata revisi **dan** angka lama ("semula X menjadi
  Y"), atau frontier menunjuk sumber yang direvisi dan teks sumber revisi memuat angka lamanya.
- Angka berbeda tanpa bukti revisi = konflik: semua angka ditampilkan dengan sumber dan tanggalnya,
  nilai berlaku dibiarkan kosong, dan panel berita berstatus perlu pemeriksaan. Angka terbaru tidak
  pernah dipilih otomatis berdasarkan tanggal artikel.

Kronologi lokal dibangun dari klaim berita yang lolos cek mekanis, jadi tetap ada walau frontier mati.
Entri timeline frontier hanya dipakai bila kutipannya ditemukan di artikel dan angkanya ada di kutipan;
di mode shadow hasilnya hanya ditampilkan sebagai pembanding.

## Budget dan biaya

| Setelan | Bawaan | Arti |
|---|---|---|
| `FRONTIER_MAX_CALLS_PER_RUN` | 3 | semua percobaan termasuk retry |
| `FRONTIER_MAX_RETRIES` | 1 | retry hanya untuk error sementara (timeout, jaringan, 429, 5xx, konten kosong/JSON cacat) |
| `FRONTIER_MAX_TOKENS_PER_RUN` | 80000 | token reservasi/aktual per run |
| `FRONTIER_MAX_COST_USD_PER_RUN` | 0.06 | batas biaya per run (estimasi USD); tiga panggilan pada input maksimum harus muat |
| `FRONTIER_MAX_INPUT_CHARS` | 24000 | ukuran prompt maksimum; paket bukti dipilih agar muat, yang lebih besar tidak dikirim |
| `FRONTIER_MAX_COST_USD_PER_DAY` | 0.60 | batas biaya per hari WIB |
| `FRONTIER_MAX_COST_USD_TOTAL` | 1.80 | batas seumur ledger (semua run dan hari); disetel untuk saldo prabayar $2 dengan margin |

Perkiraan untuk `deepseek-flash` (tabel harga 28 Sep 2026): reservasi satu panggilan ±$0,011–0,014
(prompt 8–30 ribu karakter, `max_tokens` 8192, harga peak). Biaya aktual satu panggilan dengan ±4 ribu
token masuk dan 1,5–5 ribu token keluar (termasuk penalaran) ±$0,003–0,007 di jam peak, separuhnya di
luar jam peak; satu eskalasi biasanya dua panggilan. Dengan batas total $1,80, saldo $2 cukup untuk
kira-kira ratusan eskalasi, dan tidak bisa habis dalam sehari karena batas harian $0,60. Batas total
hanya tahu pemakaian lewat SignalGate di mesin ini (ledger lokal), bukan pemakaian key yang sama di tempat
lain. Bila saldo DeepSeek benar-benar habis, API membalas 402 dan frontier berstatus `failed`; hasil
lokal tetap terbit.

- **Batas berbasis reservasi konservatif, bukan jaminan nominal absolut.** Sebelum tiap percobaan,
  `app/frontier/ledger.py` memesan biaya batas atas dalam transaksi SQLite `BEGIN IMMEDIATE`: token input
  dihitung sebagai **jumlah byte UTF-8 prompt + 512** (tokenizer DeepSeek adalah BPE tingkat byte, jadi satu
  token mewakili sedikitnya satu byte — berlaku juga untuk Unicode, simbol, dan JSON), semuanya cache miss,
  output penuh `max_tokens`, harga **peak**. Kalau reservasi melampaui sisa batas run, harian, atau total,
  panggilan tidak dikirim (`budget_exhausted`). Asumsi tokenizer itu terdokumentasi, bukan dibuktikan dengan
  tokenizer resmi: bila usage aktual pernah melebihi reservasi, ledger mencatat **overshoot**, menghitung
  aktualnya penuh, dan menghentikan panggilan berikutnya pada run itu.
- **Persisten.** Buku besar ada di `data/frontier/ledger.sqlite`: resume, retry, dan restart backend
  tidak mereset penghitung. Kunci run laporan = `run_id`; kunci screening = ticker + URL kandidat, jadi
  mencoba ulang kandidat yang sama tetap memakai penghitung yang sama.
- **Tanpa dobel panggilan.** Pekerjaan identik (kunci cache sama) yang sedang berjalan ditolak
  `in_flight`, dan cache diperiksa ulang setelah reservasi didapat.
- **Biaya = estimasi.** Dihitung dari `usage` API (cache hit/miss, completion termasuk penalaran) × tabel
  harga berversi `deepseek-2026-09-28` di `app/frontier/pricing.py` (peak 01–04 & 06–10 UTC Senin–Jumat;
  libur Tiongkok tidak diketahui kode sehingga dihitung peak — estimasi bisa lebih tinggi, tidak lebih
  rendah). Kredit Sectors dicatat terpisah (`credits_used`) dan tidak pernah dicampur.
- **Usage tidak diketahui ≠ nol.** Timeout setelah request terkirim atau error server tanpa usage
  dihitung ke budget sebesar reservasinya dan ditampilkan "tidak diketahui". Keterbatasan: request yang
  sudah diterima provider tetap bisa ditagih walau klien berhenti menunggu; batas di sini adalah batas
  atas dari sisi SignalGate.
- Error `401`/`402`/`400`/`422`/`429` dan koneksi gagal dianggap tidak diproses provider (reservasi
  dilepas, percobaan tetap dihitung ke batas panggilan).

## Bukti yang dikirim

Artikel tidak lagi dipotong dari awal saja. `app/frontier/evidence.py` memilih potongan dengan prioritas:
kutipan klaim → kata koreksi/revisi/ralat → pembuka → tanggal dan angka beraksi → nama emiten → penutup.
Potongan ditandai `dipotong: true` beserta `panjang_asli`, dan prompt memberi tahu frontier bahwa itu bukan
seluruh sumber. Total dibatasi `FRONTIER_MAX_INPUT_CHARS`.

## Cache, replay, dan mode offline

- Respons disimpan di `data/frontier/cache/<kunci>.json` (hanya jawaban akhir + usage). Kunci mencakup
  provider, model, versi prompt/schema, effort, max_tokens, hash bukti, klaim, pendapat lokal (langkah
  kedua), dan hash prompt utuh — bukti berubah berarti kunci berubah, jadi hasil basi tidak dipakai.
- `FRONTIER_CACHE_MODE=offline`: hanya respons tersimpan, tanpa jaringan dan tanpa biaya. Cache miss
  dicatat eksplisit `offline_cache_miss`; API tidak pernah dipanggil otomatis. Key boleh kosong di mode
  ini.
- `FRONTIER_CACHE_MODE=refresh`: selalu memanggil API (hasil baru tetap disimpan). Mode offline (termasuk
  replay) selalu membaca cache lebih dulu walau global `refresh`; jaringan tidak pernah dipakai offline.
- Total token/biaya audit menjumlahkan **semua percobaan** (termasuk jawaban invalid yang tetap ditagih);
  cache hit tidak menambah token baru (usage aslinya disimpan terpisah sebagai `cached_usage`).
- **Replay snapshot Sectors tidak otomatis bebas biaya LLM.** Karena itu run `replay_of` bawaannya
  memakai frontier dalam mode offline; set `FRONTIER_CALLS_ON_REPLAY=true` kalau memang ingin memanggil
  API saat replay. Replay hanya kena cache bila input frontier identik — kalau model lokal menulis klaim
  berbeda, hasilnya `offline_cache_miss`, bukan panggilan baru.

## Audit yang disimpan

Di `research.frontier` (kartu screening, juga `cases/<case_id>/frontier.json`) dan `report.frontier` +
`claim.frontier` (laporan emiten): provider/model/versi model, versi prompt/schema/harga, alasan
pemanggilan, hash bukti dan kunci cache, putusan per klaim (independen dan langkah kedua) dengan ID
bukti dan alasan singkat, aturan rekonsiliasi per klaim, durasi, token (masuk/keluar/penalaran), biaya
estimasi, pemakaian budget, dan error yang sudah disensor (key dan pola `Bearer …`/`sk-…` dibuang).

## Profil workstation dan override

`SIGNALGATE_PROFILE=workstation` memilih analis `qwen2.5:14b` dan pembanding `glm4:9b` lalu
`gemma3:12b`. Nilai eksplisit di `backend/.env` selalu menang atas profil. Kalau ingin mengikuti profil,
hapus (atau jadikan komentar) baris `OLLAMA_MODEL`, `OLLAMA_REVIEWER_MODELS`, `WORKFLOW_ANALYST_MODEL`,
`WORKFLOW_REVIEWER_MODEL`, `WORKFLOW_REVIEWER_MODELS` bila ada. Laporan emiten memakai **semua** pembanding
lokal berurutan; untuk kembali ke satu pembanding isi `WORKFLOW_REVIEWER_MODEL=gemma3:12b`. Lokasi GPU
(MacBook/Colab) dipilih di dashboard — lihat [`RUNTIME_GPU.md`](RUNTIME_GPU.md).

## Status pengujian

Otomatis, offline (HTTP palsu, fixture sintetis, jaringan httpx diblokir di `tests/conftest.py`):
`test_frontier_client.py`, `test_frontier_service.py`, `test_frontier_ledger.py`, `test_frontier_reconcile.py`,
`test_frontier_evidence.py`, `test_chronology.py`, `test_frontier_integration.py`, `test_runtime_config.py`,
`test_colab_gateway.py`, dan `npm run test:render` untuk UI. Bug yang dilaporkan QA (retry, refresh+offline,
shadow, overshoot, pemotongan artikel, angka terbaru, gateway tanpa token, resume diam-diam, token ke host
baru, daftar model tak terbaca, aktivasi belum siap, probe saat run) sudah disisipkan kembali sementara dan
terbukti ditangkap tes. **Belum** dijalankan terhadap DeepSeek, GPU MacBook, atau tunnel Colab sungguhan —
itu uji langsung yang dilakukan pemilik proyek.

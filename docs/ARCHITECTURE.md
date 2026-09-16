# SignalGate — Arsitektur

## Problem statement

Investor ritel IDX kebanjiran pengumuman aksi korporasi (rights issue, private placement,
akuisisi, perubahan pengendali) tanpa cara cepat membedakan mana katalis pertumbuhan riil dan
mana pola structural red-flag (asset injection/backdoor listing). SignalGate meng-scan
pengumuman IDX lewat Sectors API, mengekstrak fakta berkutipan dari sumber, lalu memberi label
screening beserta alasan dan ketidakpastiannya — bukan rekomendasi beli/jual.

## Prinsip: Python memutuskan, model lokal membaca

| Dikerjakan model lokal (Ollama) | Dikerjakan Python (deterministik) |
|---|---|
| **Analis — qwen2.5:7b:** membaca teks berita dan mengekstrak fakta: penerima saham, penggunaan dana, pergantian bisnis, pelepasan bisnis lama, injeksi aset — masing-masing dengan kutipan | Mengambil data Sectors dan halaman Scrapling |
| **Validator — qwen3:4b:** mengekstrak fakta secara independen dari bukti tanpa melihat jawaban analis. Python membandingkan keduanya | Mencocokkan seluruh kata/angka kutipan ke teks bukti; toleransi hanya spasi, kapitalisasi, dan tanda baca dan memastikan nama pihak spesifik serta benar-benar ada di sumber |
| | Memberi bobot dan skor, menetapkan label |
| | Menurunkan label ke `inconclusive` bila validator membantah fakta atau tidak menyetujui label |
| | Compliance Gate, audit, cache |

Model tidak pernah menentukan label secara langsung dan tidak bisa menaikkan hasil.

## Sumber data

Sectors Financial API v2 adalah sumber data inti produk: `/pipeline/run` dan `/research/run`
menolak berjalan tanpa `SECTORS_API_KEY`; hasil tidak dapat selesai jika data perusahaan Sectors tidak tersedia.

- `news?keyword=...` — kandidat aksi korporat.
- `company/report/{ticker}` — ownership, valuation, financials, overview, management; diringkas sebelum
  dipakai. Hanya lima seksi itu yang diminta: endpoint menagih 1 kredit per seksi, dan tiga seksi sisanya
  (`future`, `peers`, `dividend`) tidak dibaca siapa pun.
- `free-float?sub_sector=...` — porsi kepemilikan publik per emiten, diambil sekali per subsektor lalu
  dipakai ulang sepanjang satu run (1 kredit per 100 emiten, tidak ada filter per simbol).
- `financials/quarterly/{ticker}?n_quarters=4` — laporan kuartalan lengkap, terbaru dulu. Jendelanya sengaja
  pendek: endpoint menagih 1 kredit per kuartal.

Scrapling (HTML publik statis, patuh robots.txt, URL non-publik ditolak) mengambil artikel sumber.

**Jalur tanpa Sectors.** `app/scan.py` menemukan kandidat langsung dari halaman publik yang didaftarkan
di `SCAN_SOURCES`, lalu meriset PDF-nya. Nol permintaan Sectors. `POST /scan/run` menjalankan jalur ini
dan menyimpan hasilnya ke database lewat `screen_outcome()` yang sama dengan `/pipeline/run`, sehingga
tidak ada pintu samping yang melewati Compliance Gate. CLI (`python -m app.scan`) dan endpoint memakai
`research_queue_items()` yang sama, jadi keduanya tidak bisa menyimpang satu sama lain.

## Pipeline

| Tahap | Modul | Fungsi |
|---|---|---|
| ① SENSE | `pipeline/sense.py` | Kandidat dari Sectors `news` + `company/report` |
| ② HYPOTHESIZE | `pipeline/hypothesize.py`, `orchestrator.pick_events` | Bucket & prioritas; hanya `PIPELINE_MAX_EVENTS` teratas (default 3) yang diriset |
| ③ REASON | `research/engine.py` + qwen2.5:7b | Ekstraksi fakta berkutipan (maks `RESEARCH_EXTRACTION_ATTEMPTS`, default 2) |
| ④ VALIDATE | `research/facts.py` + qwen3:4b, `pipeline/validate.py` | Pencocokan kutipan & nama pihak oleh Python, cek fakta oleh validator, cek numerik |
| ④b SCORE | `research/scoring.py` | Bobot deterministik → label + confidence |
| ⑤ GATE 🔒 | `pipeline/gate.py` | Schema keras + denylist bahasa transaksi **dan** penilaian nilai, dipindai refleksif ke seluruh field prosa |
| ⑥ [ACT] | — | Dihapus. Tidak ada broker adapter atau eksekusi order. |
| ⑦ WATCH | `pipeline/watch.py` | Logika transisi status (belum dijadwalkan) |
| ⑧ AUDIT | `cases/<kasus>/`, SQLite | Bukti, ekstraksi, validasi, dan keputusan tersimpan |

## Progres dan pemulihan run

Backend menerbitkan tahapnya lewat SSE di `/run/stream`, satu pesan per peristiwa:

| Event | Kapan | Isi |
|---|---|---|
| `run` | awal dan akhir run | `kind`, status akhir |
| `case` | awal dan akhir tiap kasus | `index`, `total`, dan `label` saat selesai |
| `model` | awal dan akhir tiap pembacaan model | `role`, `model`, `case_id`, `seconds` |
| `scan` / `sense` / `research` / `gate` | saat baris audit ditulis | ringkasan tahapnya |

Event `case` akhir diterbitkan **setelah** kartunya di-commit, bukan setelah gate lewat, supaya
hitungan "selesai" tidak pernah mendahului apa yang benar-benar tersimpan. Setiap payload membawa
`run_id` agar sisa event run sebelumnya tidak terbaca sebagai progres.

Dashboard menentukan tahap aktif dari operasi **terakhir**, bukan indeks tertinggi sepanjang run —
kalau tidak, tahap yang tidak pernah dijalankan ikut tampak selesai hanya karena indeksnya lebih
rendah. Daftar tahap juga berbeda antara jalur scan dan pipeline.

## Compliance Gate

`pipeline/gate.py` memastikan output tidak berbentuk rekomendasi investasi secara arsitektural:
schema label terbatas tiga nilai, dan denylist istilah transaksi dengan word boundary. Alasan pada
verdict disusun dari template Python, bukan kutipan mentah, supaya istilah legal seperti "perjanjian
jual beli" di artikel tidak memicu gate. Respons `/research/run`, `/scan/run`, dan data dashboard
selalu memakai verdict yang sudah melewati gate.

Dua celah ditutup setelah sinyal data pasar ditambahkan:

- **Denylist diperluas ke penilaian nilai.** Versi lama hanya menangkap kata transaksi (beli, jual,
  rekomendasi, target price). Kalimat seperti *"sahamnya bagus"*, *"prospek cerah"*, *"layak
  dikoleksi"*, atau *"undervalued"* lolos semuanya — padahal itulah bentuk kalimat yang dihasilkan
  fitur komposit. Kata bersayap seperti "murah" dan "mahal" sengaja **tidak** dimasukkan: keduanya
  muncul wajar di kutipan fakta dan akan menahan hasil yang sah.
- **Pemindaian kini refleksif.** `apply_gate()` dulu menggabungkan tiga field yang ditulis tangan,
  sehingga field baru apa pun di `Verdict` melewati gate tanpa diperiksa sama sekali.
  `prose_fields()` sekarang menemukan sendiri seluruh field prosa lewat `model_fields`, dan
  `sanitize_for_display()` mengosongkan semuanya. Field baru terjaga secara bawaan, bukan karena
  seseorang ingat memperbarui daftarnya. `label`, `confidence`, dan `provider` dikecualikan: itu
  identitas mesin, bukan prosa, dan tetap utuh untuk audit.

## Aturan skor

| Fakta terverifikasi | Arah | Bobot |
|---|---|---|
| Pergantian/penambahan bidang usaha baru | red | 3 |
| Bisnis atau anak usaha lama dilepas | red | 2 |
| Injeksi aset / inbreng / pengambilalihan aset atau piutang dari pihak terkait | red | 2 |
| Penerima saham/dana pihak baru | red | 2 |
| Dana untuk bisnis baru di luar bisnis inti | red | 1 |
| Valuasi jauh di atas sebayanya (lihat di bawah) | red | 1 |
| Penerima saham/dana pemegang saham lama atau afiliasinya | growth | 2 |
| Dana untuk ekspansi bisnis inti | growth | 2 |
| Saham ditawarkan ke seluruh pemegang saham (HMETD) | growth | 1 |
| Dana untuk modal kerja | growth | 1 |

### Sinyal data pasar

Dihitung Python dari Sectors, bukan dari model, dan hanya aktif pada bucket yang menerbitkan saham atau
menggalang dana (`rights_issue`, `non_preemptive_capital`) — di luar itu keduanya diam.

| Kondisi terukur | Arah | Bobot |
|---|---|---|
| Free float < 15% saat emiten menambah modal (dilusi memusatkan kendali) | red | 1 |
| Arus kas operasi negatif ≥ 3 dari 4 kuartal terakhir saat emiten menggalang dana | red | 1 |
| Klaim ekspansi bisnis inti sementara pendapatan turun beruntun 4 kuartal | red | 1 |
| PBV > 3x PB subsektornya; bila data subsektor tak ada, jatuh ke ambang mutlak 20x | red | 1 |

Aturan valuasi membandingkan emiten terhadap **subsektornya sendiri**, bukan terhadap satu angka
untuk seluruh pasar. `EXTREME_PBV = 20` lama praktis tidak pernah menyala di sektor bervaluasi
rendah: PB agregat subsektor `banks` pada 2026 ada di 0,80x, jadi bank dengan PBV 4,2x — lima kali
lipat sebayanya — lolos tanpa sinyal. Ambang mutlak tetap dipertahankan sebagai cadangan ketika
data subsektor tidak tersedia. `subsector/report` memakai bentuk berbeda dari company report:
`historical_valuation` di sana dict berkunci tahun, bukan list.

Tiga aturan berbasis kuartal memakai jendela utuh: satu kuartal tanpa angka membuat aturannya diam, bukan menebak.
Alasan sinyal free float mengutip peringkat emiten di dalam subsektornya sendiri (`tertipis ke-N dari M`)
supaya pembaca bisa memeriksa ulang pembandingnya, bukan ambang indeks eksternal yang tak terlihat.

**Invarian:** seluruh sinyal data pasar berbobot 1 sedangkan `RED_FLAG_THRESHOLD` = 4, jadi data pasar
tanpa satu pun fakta terverifikasi dari dokumen sumber selalu berakhir `inconclusive`. Data pasar
memberi konteks pada aksi korporasi; ia tidak pernah menilai sahamnya. Dikunci oleh
`test_sectors_market_data_alone_can_never_produce_a_label`.

Setiap jenis fakta dihitung sekali; pemegang saham lama dan afiliasi dianggap satu sinyal. Label:

- `structural_red_flag` — red ≥ 4 dan red ≥ growth + 2
- `growth_catalyst` — growth ≥ 3, red ≤ 1, dan wajib ada bukti ekspansi bisnis inti
- `inconclusive` — selain itu

Bobot ini heuristik awal yang eksplisit dan bisa diaudit, bukan hasil kalibrasi statistik.

## Validasi

Python mencocokkan semua kata dan angka kutipan; perbedaan kapitalisasi, spasi, dan tanda baca
boleh diterima. Nama penerima harus spesifik, muncul dalam kutipan, dan bukan identitas emiten sendiri.
Pembaca kedua melakukan ekstraksi independen tanpa melihat fakta atau label analis. Python
membandingkan kategori, pihak, dan sumber, kemudian menghitung ulang label.

`strict` adalah default. Mode `lenient` menyimpan fakta yang belum dikonfirmasi untuk inspeksi,
tetapi **keduanya hanya memberi skor pada fakta berstatus supported**. Perbedaan label antara
pembacaan pertama dan kedua menurunkan hasil ke `inconclusive`. Kesepakatan dua model bukan
jaminan kebenaran; label acuan evaluasi tetap berasal dari peninjauan manusia.

## Status hasil riset

- `completed` — pembacaan independen selesai; label dihitung dari fakta yang lolos.
- `needs_review` — pembacaan berbeda atau fakta belum terkonfirmasi.
- `insufficient_evidence` — tidak ada fakta yang lolos pemeriksaan.
- `missing_sectors_data` — workflow publik tidak memperoleh data perusahaan Sectors.
- `deterministic_only` — `LLM_BACKEND=off`; hanya sinyal data Sectors, praktis selalu `inconclusive`.
- `model_unavailable` — Ollama mati, model belum diunduh, timeout, atau output tidak sesuai schema.

## Hemat konteks dan memori

1. Hanya kandidat prioritas teratas yang diriset.
2. Laporan Sectors diringkas; laporan mentah disimpan terpisah.
3. Halaman web hanya dikirim paragraf yang menyebut ticker, nama emiten, atau istilah aksi korporat
   (`RESEARCH_CONTEXT_CHARS`); kutipan tetap dicocokkan ke teks penuh.
4. Dua sampai tiga panggilan model per kasus: ekstraksi (ulang sekali bila ada fakta yang ditolak) dan
   validasi. Validasi dilewati bila tidak ada fakta.
5. Hasil `completed` tanpa fetch gagal di-cache setelah sumber diambil ulang. Kunci mencakup hash bukti, seluruh event, konfigurasi, versi prompt, dan digest model; TTL default satu jam.
6. Batas prompt diperkirakan dari karakter dan anggaran konteks; ini estimasi, bukan tokenizer
   yang membuktikan konteks tidak terpotong. Output yang terpotong ditolak.
7. Mode thinking qwen3 dimatikan untuk validator.
8. Kedua model dipanggil bergantian. Periksa `ollama ps` untuk residensi aktual; jangan menganggap
   keduanya selalu berada di GPU bersamaan.

## Struktur folder kasus

```
cases/<TICKER>-<id>/
  evidence/E001.json, E002.json, ...     bukti (input, sectors_api, scrapling) + hash
  evidence/sectors_report_raw.json
  evidence/fetch_failures.json
  extraction/01.{json,md}                ekstraksi analis + hasil penyaringan Python
  independent_extraction.json            ekstraksi kedua tanpa jawaban analis
  validation.{json,md}                   pemeriksaan fakta oleh validator
  decision.{json,md}                     sinyal, label, dan catatan
cases/_cache/<hash>.json
```

## Menjalankan

Pasang aplikasi Ollama dari https://ollama.com/download (atau `brew install --cask ollama-app`),
buka aplikasinya, lalu unduh kedua model:

```bash
ollama pull qwen2.5:7b
ollama pull qwen3:4b
```

```bash
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # isi SECTORS_API_KEY
uvicorn app.main:app --reload
```

Uji pengembangan hanya dengan artikel Scrapling, tanpa memanggil Sectors API:

```bash
python -m app.scrapling_check                   # kasus bawaan: MGLV, HATM, APEX, LAPD, FORU
python -m app.scrapling_check BBCA=https://...  # kasus sendiri
```

## Rujukan konsep

Pola "LLM terbatas, Python yang memutuskan" dipilih setelah membaca kode tiga pemenang Alpaca AI
Trading Agents Hackathon (konsep saja, tanpa kode yang diambil): VegaGuard memakai LLM hanya untuk
menjelaskan fakta yang sudah divalidasi, TradePilot memberi Python hak veto atas keputusan LLM.

## Keterbatasan yang diketahui

- Uji lokal menemukan salah kategori pada kedua model. Belum ada perbandingan terkontrol dengan
  model lain; ketidaksepakatan atau kekurangan bukti menghasilkan `inconclusive`.
- Satu artikel sering tidak memuat konteks penuh (misalnya pergantian bisnis MGLV ada di data industri
  Sectors dan pengumuman divestasi, bukan di berita rights issue-nya).
- Bobot skor belum dikalibrasi terhadap data historis.
- Asosiasi lampiran IDX bersandar pada tanggal di nama berkas; pengumuman tanpa tanggal yang bisa
  diturunkan tidak digabungkan dan diserahkan ke pemeriksaan manusia.
- `/pipeline/run` dan `/scan/run` berjalan di latar dan langsung mengembalikan `run_id`; status
  dibaca ulang lewat `/runs/active` atau `/runs/{id}`, jadi refresh halaman tidak menghilangkan
  progres. Satu run pada satu waktu, dijaga `run_lock`; job yang tertinggal saat backend mati
  ditutup otomatis saat start berikutnya.
- Capture/replay lokal sudah ada di `app.evaluate`; paket demo yang siap dibagikan ke juri belum dibuat.

## Evaluasi dan aturan hackathon

Lihat [rencana validasi dan temuan arsitektur](VALIDASI_HACKATHON.md). Skor confidence adalah heuristik,
bukan probabilitas benar. Uji pasar berjalan hanya membaca data, tanpa eksekusi order.

# Panduan label manusia: klaim laporan empat panel

Yang sudah terbukti sejauh ini hanya dua: sistemnya berjalan, dan klaim yang salah secara mekanis
ditolak. Seberapa sering tafsiran model benar belum diukur sama sekali. Panduan ini mengukurnya.

## Apa yang diukur

Unit penilaian adalah **satu klaim model** (penulis `model:*`) di panel fundamental, valuation,
technical, atau news. Klaim buatan kode tidak dilabel; angkanya berasal dari `calculations.py`.

| Angka | Arti | Pertanyaan yang dijawab |
|---|---|---|
| Presisi ketat | klaim tampil (`supported`) yang dinilai `benar` | Kalau pengguna membaca klaim terverifikasi, seberapa sering itu benar? |
| Presisi longgar | tampil dan dinilai `benar` atau `berlebihan` | Seberapa sering tampil tanpa kesalahan fakta/logika? |
| Ditolak padahal benar | klaim benar yang dibuang, dipisah per penolak (kode/pembanding/analis menarik) | Seberapa mahal kehati-hatian sistem? |
| Tingkat tangkap | klaim `salah_*` yang tidak tampil | Seberapa efektif lapis validasi? |

Presisi selalu dilaporkan dengan interval 95% (Wilson). Dengan puluhan klaim rentangnya lebar;
itu memang kondisi datanya, jangan dipotong.

## Aturan main

1. **Buta.** Nilai dari lembar CSV, bukan dari dashboard. Lembar sengaja tidak memuat status sistem,
   catatan pembanding, atau alasan penolakan. Jangan buka laporan di tab *Laporan emiten* sebelum
   selesai melabel emiten itu.
2. **Nilai terhadap bukti per tanggal acuan.** Pakai kolom `bukti` (metrik yang dirujuk beserta
   masukannya), `kutipan`, dan file di kolom `sumber` (relatif terhadap `data/workflow/`), ditambah
   pengetahuan keuangan umum. Pergerakan harga sesudah tanggal acuan bukan bukti.
3. **Nilai kalimat apa adanya.** Kolom `jenis` adalah tag dari analis sendiri, jadi jangan dipakai
   sebagai patokan. Bahasa campuran (misalnya kalimat berbahasa Inggris) tidak membuat klaim salah;
   catat saja di `catatan`.
4. **`catatan` wajib diisi untuk semua putusan selain `benar`.** Satu kalimat: apa yang salah.
   Catatan inilah yang nanti diubah menjadi aturan kode atau prompt.
5. **Isi `pelabel`** dengan nama atau inisial. Pelabel kedua mengekspor lembar yang sama ke file
   sendiri lalu melabel tanpa melihat hasil pelabel pertama.

## Lima putusan

Pertanyaannya bukan "apakah angkanya sesuai sumber", tetapi: **kalau investor membuka laporan
emiten ini, apakah kalimat ini benar seluruhnya dan pantas ada di sini?** Angka yang benar tentang
emiten lain, atau angka benar yang dibandingkan dengan cara keliru, tetap bukan `benar`. Kalau
angkanya sudah kamu cek ke sumber lain (broker, laporan keuangan), tulis itu di `catatan`.

Kalau lebih dari satu berlaku, pilih yang paling atas di urutan ini:
`salah_fakta` > `salah_tafsir` > `berlebihan` > `benar`.

| Putusan | Kapan |
|---|---|
| `salah_fakta` | Ada yang bisa dicek dan keliru: angka, periode, arah perbandingan, **angka ditempel ke metrik/subjek yang salah**, atau klaim tentang emiten lain. |
| `salah_tafsir` | Faktanya benar, tetapi kesimpulan atau perbandingannya tidak mengikuti: membandingkan hal yang tidak sebanding (peer vs historis sendiri, PE vs PB), sebab-akibat tanpa dasar, atau kalimat yang tidak bermakna. |
| `berlebihan` | Fakta dan logikanya benar, tetapi kata penilaiannya lebih luas daripada yang didukung metrik yang dirujuk ("sehat", "efisien", "kuat") dan tidak ada pembanding (peer/historis) yang dikutip. |
| `benar` | Fakta benar, dan tafsirannya tidak melampaui metrik yang dirujuk. Klaim yang hanya mengulang angka dengan benar juga masuk sini. |
| `tak_bisa_dinilai` | Bukti di lembar dan file sumber tidak cukup untuk memutuskan. Tidak ikut dihitung, tapi jumlahnya dilaporkan. |

### Contoh kalibrasi

Semua contoh diambil dari run `IDEA-2026-09-19-13fe0c08`, yang **tidak** ikut lembar `dev-01` dan
tidak dihitung skornya. Putusan di bawah adalah usulan rubrik. Kalau kamu tidak setuju, ubah
rubriknya **sebelum** mulai melabel, lalu pakai rubrik itu dengan konsisten.

| Klaim | Usulan | Alasan |
|---|---|---|
| Pertumbuhan pendapatan tahunan mencapai +13,2% dari FY2024 ke FY2025. | `benar` | Sama dengan `revenue_growth_fy`, tanpa tafsiran tambahan. |
| Rasio utang berbunga terhadap ekuitas 0,17x, menunjukkan tingkat leverage yang rendah. | `benar` | "Leverage rendah" hanya menggambarkan rasio itu sendiri, dan 0,17x memang rendah di hampir semua sektor non-bank. |
| Rasio liabilitas terhadap ekuitas sebesar 0,19x, menunjukkan posisi keuangan yang sehat. | `berlebihan` | "Posisi keuangan sehat" mencakup likuiditas, arus kas, dan profitabilitas; satu rasio leverage tidak cukup. |
| PB MRQ (dihitung) sebesar 2,05x, lebih rendah dari PB MRQ (Sectors) 1,87x. | `salah_fakta` | Arah terbalik. Aturan kode sekarang sudah menangkap ini. |
| PB emiten sebesar 2,05x lebih rendah dari median PB peer, sebesar -9,0%. | `salah_fakta` | PB emiten (Sectors) 1,87x; 2,05x adalah median peer. **Aturan kode saat ini masih meloloskannya** karena 2,05 memang nilai salah satu metrik masukan. |
| Median PE TTM peer sebesar 63,61x, sementara PB peer juga sebesar 2,05x, menunjukkan penilaian pasar yang lebih tinggi dibandingkan dengan rasio keuangan. | `salah_tafsir` | PE dan PB dibandingkan seolah satu besaran; kesimpulannya tidak bermakna. Juga lolos aturan kode saat ini. |
| PT Tower Bersama Infrastructure Tbk leads cash dividend payouts … (di laporan IDEA) | `salah_fakta` | Benar tentang TBIG, tetapi bukan tentang IDEA. |
| BEI monitors three stocks after Unusual Market Activity: PT Idea Indonesia Akademi Tbk (IDEA) spikes to Auto-Reject Atas … | `benar` | Sesuai artikel dan relevan untuk IDEA; bahasa Inggris dicatat saja. |

Di run itu kesembilan klaim model berstatus `supported`. Dengan aturan kode terbaru, klaim arah
terbalik dan klaim TBIG sudah ditolak, tetapi dua klaim valuasi yang salah di atas masih lolos
pemeriksaan mekanis dan hanya bisa ditangkap pembanding. Pembandingnya, gemma3:4b, menyetujui
keduanya. Celah seperti ini yang diukur lembar label.

## Prosedur

Semua perintah dijalankan dari `backend/`.

### 1. Pilih emiten dan bagi dua

Target: 10–12 emiten, kira-kira 90–110 klaim model. Pilih yang beragam, karena tiap jenis
memancing kesalahan yang berbeda:

- bank (rasio utang tidak bermakna, PB penting)
- emiten yang sedang rugi (PE negatif dikecualikan)
- emiten dengan split atau rights issue baru-baru ini (seri harga dipotong)
- emiten besar dengan banyak berita, dan emiten kecil dengan UMA/ARA seperti IDEA
- sektor yang berbeda-beda

Bagi menjadi **dev** (6–7 emiten, termasuk IDEA) dan **holdout** (4–5 emiten). Dev boleh dipakai
untuk memperbaiki prompt dan aturan. Holdout tidak boleh dilihat sampai perbaikan terakhir selesai.

### 2. Ambil snapshot (live, sekitar 13 kredit Sectors per emiten)

```bash
.venv/bin/python scripts_local/run_live.py BBRI gemma3:4b
```

Untuk emiten holdout, jalankan sekarang juga supaya snapshot-nya terkunci, lalu **jangan buka
laporannya**. `run_live.py` mencetak isi laporan ke terminal, jadi saring keluarannya dan simpan
hanya `run_id`-nya:

```bash
.venv/bin/python scripts_local/run_live.py ADRO gemma3:4b | grep '^run_id' >> ../data/labels/holdout-runs.txt
```

### 3. Ekspor dan label (0 kredit, tanpa model)

```bash
.venv/bin/python -m app.workflow.labels export --ticker BBRI --ticker TLKM --split dev \
  --output ../data/labels/dev-02.csv --skip-labeled ../data/labels/dev-01-IDEA.csv
```

Buka CSV di Google Sheets, Numbers, atau Excel. Isi `putusan`, `catatan`, dan `pelabel`, lalu
simpan kembali sebagai CSV; koma maupun titik koma sama-sama terbaca. Perkiraan waktunya 1–2 menit
per klaim.

### 4. Skor

```bash
.venv/bin/python -m app.workflow.labels score --labels ../data/labels/*.csv --output ../data/labels/skor.json
```

Secara default skor memakai run terbaru per (emiten, tanggal acuan, analis, pembanding). Dua daftar
di akhir keluaran adalah daftar kerja:

- **Lolos padahal bermasalah**: tiap baris jadi aturan kode (bila bisa dicek mekanis) atau
  instruksi prompt, plus regression test yang memakai kalimat aslinya.
- **Ditolak padahal benar**: bila penolaknya `kode`, itu bug di `evidence.py`, dan prioritasnya
  tinggi karena penolakan kode tidak bisa dianulir.

### 5. Perbaiki, putar ulang, ulangi (0 kredit)

Setelah mengubah prompt atau aturan, putar ulang run dev:

```bash
.venv/bin/python scripts_local/run_live.py BBRI gemma3:4b <run_id_lama>
```

Klaim yang kalimat dan buktinya tidak berubah otomatis memakai label lama. Ekspor hanya klaim baru
dengan `--skip-labeled ../data/labels/*.csv`.

Cara yang sama dipakai untuk memutuskan pembanding: putar ulang snapshot yang sama dengan pembanding
lain. Skor muncul per konfigurasi `analis -> pembanding`. Sudah diputuskan 2026-09-19: gemma3:4b
dibuang dari peran pembanding (dan model manapun di bawah 8B parameter) setelah terbukti salah baca
tanda metrik sendiri di label BBRI — lihat `docs/PERBAIKAN.md`. Default sekarang mengikuti profil
`workstation` (`SIGNALGATE_PROFILE=workstation` di `.env`): analis qwen2.5:14b, pembanding glm4:9b.

### 6. Holdout, sekali saja

Setelah perbaikan terakhir, putar ulang run holdout dengan kode final, ekspor dengan
`--split holdout`, label, lalu skor. Angka holdout inilah yang dilaporkan. Kalau sesudahnya kamu
mengubah prompt lagi karena melihat holdout, holdout itu sudah menjadi dev.

## Dua pelabel

Idealnya anggota tim kedua melabel minimal 30 klaim yang sama di file terpisah. `score` melaporkan
persentase kesepakatan dan Cohen's kappa. Klaim yang putusannya berbeda tidak dihitung sampai ada
baris dengan `pelabel=konsensus` (salin barisnya, isi putusan hasil diskusi).

## Yang dilaporkan

Tulis apa adanya, termasuk rentangnya:

> Pada N klaim model dari K emiten holdout yang dilabel manusia tanpa melihat status sistem,
> X% klaim yang tampil sebagai terverifikasi dinilai benar (CI95 a–b%). Sistem menahan Y dari Z
> klaim yang salah, dan ikut menahan W klaim yang sebenarnya benar.

Angka ini bukan akurasi seluruh pasar: sampelnya kecil, emitennya dipilih tim, dan pelabelnya
anggota tim sendiri.

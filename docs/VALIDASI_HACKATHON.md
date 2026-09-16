# Validasi SignalGate dan ketentuan hackathon

Diperiksa 16 September 2026. Implementasi tetap memakai qwen2.5:7b sebagai analis dan qwen3:4b
sebagai pembaca kedua melalui Ollama. Dua model dijalankan bergantian; keberhasilan pemanggilan
keduanya tidak membuktikan bahwa bobot keduanya terus berada di GPU atau bahwa hasilnya akurat.

## Ketentuan resmi yang relevan

Sumber: [Official rules](https://hackathon.sectors.app/rules) dan
[Track 01](https://hackathon.sectors.app/tracks/ai-agents-assistants).

- Sectors REST API/MCP harus menjadi sumber inti. Key yang terisi saja tidak membuktikan integrasi.
- MVP harus berjalan end-to-end; deployment publik tidak wajib.
- Track 01 mensyaratkan AI/LLM dan logika agent/orkestrasi buatan tim. Ollama dan Scrapling dapat
  dipakai karena pilihan stack bebas; kelayakan akhir tetap diputuskan juri.
- Eksekusi order otomatis pada akun nyata atau yang terhubung broker dilarang. Produk harus
  diposisikan sebagai informasi/analisis dan memuat disclaimer yang relevan.
- Bobot penilaian: kegunaan nyata 40%, video/storytelling 30%, kedalaman teknis 30%.
- Registrasi tutup 22 September; submission 30 September 2026, keduanya 23:59 WIB.
- Submission mencakup repo publik, teaser satu menit, video penjurian maksimal tiga menit,
  problem statement, track/anggota, dan posting sosial sesuai aturan.
- Proyek dibekukan saat disubmit atau saat deadline, mana yang lebih awal.

Aturan yang dibaca tidak mensyaratkan jumlah kasus uji, keuntungan investasi, backtest return,
atau perdagangan dengan uang nyata. Rencana di bawah adalah rekomendasi teknis tim.

## Temuan dan perbaikan arsitektur

1. **Validator mendapat jawaban analis.** Diganti ekstraksi independen: pembaca kedua hanya menerima
   bukti, bukan fakta/kategori/label analis. Python membandingkan hasil. Model berbeda tetap bisa
   salah bersama; audit manusia masih diperlukan.
2. **Growth terlalu mudah lolos.** Pemegang saham lama + modal kerja tidak cukup. Harus ada sinyal
   ekspansi bisnis inti, selain syarat skor yang sudah ada.
3. **Nama pihak generik atau emiten sendiri.** Deskripsi seperti “pemegang saham utama NDC” ditolak;
   nama harus muncul dalam kutipan dan tidak sama dengan identitas emiten yang tersedia.
4. **Pencocokan 85% bisa menghapus negasi/angka.** Sekarang semua kata/angka harus cocok berurutan;
   toleransi hanya kapitalisasi, spasi, dan tanda baca. Bukti asli tetap disimpan.
5. **Fakta belum didukung masuk skor.** Default strict. Mode lenient hanya menyimpan fakta tersebut
   untuk inspeksi; skor yang ditampilkan tetap memakai fakta yang terkonfirmasi.
6. **Cache mengabaikan perubahan artikel.** Sumber diambil ulang sebelum lookup. Kunci meliputi hash
   teks, seluruh event, konfigurasi, versi prompt, dan digest model; TTL default satu jam. Cache
   rusak/kedaluwarsa dilewati dan fetch gagal tidak menjadi cache sukses. Ini menghemat inferensi,
   bukan panggilan Sectors/Scrapling.
7. **Endpoint batch dan tunggal berbeda validasi.** Keduanya sekarang melalui `screen_outcome`.
   Hilangnya data perusahaan Sectors menghasilkan `missing_sectors_data`, bukan hasil selesai.
8. **Harga naik dianggap tesis terbukti.** WATCH tidak lagi menyelesaikan kasus dari pergerakan harga.
   Status hanya menjadi stale karena umur; resolusi memerlukan bukti lanjutan yang ditinjau.
9. **Confidence terlihat seperti probabilitas.** Dashboard menampilkan skor heuristik `/100`;
   angka belum terkalibrasi sebagai probabilitas benar atau prediksi return.

## Tiga lapis pengujian

### A. Regresi offline, pada setiap perubahan logika

```bash
cd backend
.venv/bin/python -m pytest -q
```

Tes kontrak, kutipan salah, angka/negasi berubah, pihak generik, emiten sendiri, growth palsu,
ketidaksepakatan pembaca, cache berubah/kedaluwarsa, kegagalan sumber, dan gerbang publikasi.
Tes ini membuktikan perilaku program pada fixture, bukan akurasi terhadap seluruh pasar.

### B. Evaluasi snapshot dengan label acuan manusia

Target awal praktis: 20–30 peristiwa berbeda, mencakup ekspansi bisnis inti, perubahan bisnis,
konversi utang, modal kerja, dan kasus ambigu. Angka ini target awal, bukan syarat hackathon.

- Pisahkan kasus pengembangan dari holdout yang belum dipakai mengubah prompt/bobot.
- Simpan tanggal kejadian, tanggal sumber, waktu pengambilan, dan bukti yang tersedia ketika dinilai.
- Label acuan berdasarkan dokumen, bukan opini model, rumor, atau harga setelah peristiwa.
- Dua peninjau bila memungkinkan; selesaikan perbedaan penilaian atau beri `inconclusive`.
- Jangan menggabungkan snapshot Sectors hari ini ke backtest seolah tersedia pada masa lalu.
- Laporkan precision per label, kesalahan growth pada kasus risiko, tingkat abstain, coverage,
  kegagalan operasional, dan waktu proses. Jangan hanya melaporkan agreement antar-model.

### C. Pengamatan pasar berjalan, baca-saja

Jalankan pada pengumuman IDX terbaru selama beberapa hari pasar sebelum submission. Simpan hasil
pertama, kemudian tinjau bukti tambahan secara terpisah. Ini menguji pergantian format sumber,
ketahanan scraper, kecepatan, dan relevansi. Tidak perlu transaksi atau akun broker.

## Capture, replay, dan pengukuran

Capture berikut mengambil satu laporan Sectors per kasus dan HTML sumber. Maksimum tiga kasus
per perintah untuk membatasi penggunaan API/waktu. Pilih direktori keluaran baru setiap run.

```bash
cd backend
.venv/bin/python -m app.evaluate capture \
  --case 'HATM=https://alamat-artikel-publik' \
  --output cases/evaluation-baru
```

Isi `expected_label`, `reviewer`, dan `review_notes` di `manifest.json` setelah penilaian manusia.
Kolom `split` dapat diberi `development` atau `holdout`. Program tidak membuat label acuan otomatis.

```bash
.venv/bin/python -m app.evaluate score --manifest cases/evaluation-baru/manifest.json
.venv/bin/python -m app.evaluate replay \
  --manifest cases/evaluation-baru/manifest.json \
  --output cases/evaluation-replay
```

Replay memakai laporan dan teks HTML beku, tidak menghubungi Sectors atau situs sumber; Ollama
lokal tetap diperlukan. Gunakan snapshot yang sama untuk membandingkan model/prompt. Ini bukan
backtest historis otomatis. Capture saat ini tidak mengetahui tanggal publikasi bila URL diberikan
manual; tambahkan metadata terverifikasi sebelum memasukkan kasus ke evaluasi historis.

## Pekerjaan lanjutan

- PDF keterbukaan informasi dan perluasan sumber relevan di luar seed URL.
- Background job dengan status progres/cancel; endpoint saat ini sinkron dan lock hanya satu proses.
- Paket replay/demo yang dapat dibagikan ke juri setelah hak distribusi sumber diperiksa.
- Kalibrasi skor pada holdout dan perluasan data untuk mengukur false positive/negative.
- Evaluasi kualitas pemilihan excerpt; dua model masih bisa kehilangan konteks penting yang sama.

# Audit hasil analisis SignalGate — 28 September 2026

## Ruang lingkup dan bukti

Kode yang diperiksa: commit HEAD `fd134d1` (working tree bersih sebelum laporan dibuat). Database dibaca read-only. Pemeriksaan mencakup seluruh 14 kartu screening tersimpan, rincian lima kartu terbaru tanggal 28 September, dan status laporan terbaru per ticker dari 70 laporan workflow. Laporan workflow terbaru yang tersedia dibuat 21 September, sehingga bukan bukti eksekusi arsitektur frontier terbaru.

Audit menggunakan snapshot sumber yang dipakai aplikasi, bukan verifikasi ulang kondisi pasar hari ini. Tidak memanggil DeepSeek/Sectors, tidak menjalankan ulang model, tidak mengubah hasil lama, kredensial, atau kode aplikasi. Label holdout tidak dibaca atau dipakai untuk menyetel model.

## 1. Seluruh screening tertahan, tetapi penyebab berbeda

Database memiliki 14 kartu: semuanya `inconclusive` dan `needs_review`. Lima hasil terbaru:

| Kartu | Case ID | Status riset | Frontier | Penyebab utama yang ditemukan |
|---|---|---|---|---|
| SEMA / 108 | SEMA-6b0821b04a3c | needs_document | tidak ada | Pengambilan PDF diblokir setelah robots.txt HTTP 403; judul kontrak juga diklasifikasi sebagai perubahan pengendali |
| KETR / 109 | KETR-3b32c846ff7f | needs_review | failed | Konflik ekstraksi; dua percobaan frontier timeout sekitar 15 detik masing-masing |
| PART / 110 | PART-b12abc97c75f | needs_review | budget_exhausted | Konflik ekstraksi; batas tiga percobaan per kandidat telah tercapai, sebagian tercatat pada eksekusi sebelumnya |
| SRAJ / 111 | SRAJ-6ab54d09499a | needs_review | not_triggered | Salah kategori rights issue, klaim ekspansi terlalu kuat, label reviewer bertentangan tetapi pemicu gagal |
| EPAC / 112 | EPAC-2e3aa51a830f | needs_review | failed | Sinyal debt-only keliru setelah fakta multi-penggunaan dana dibuang; frontier timeout |

Seluruh lima kartu memiliki summary kosong dan `gate.rejected_terms=[]`. Ini penting: kosongnya ringkasan tidak berarti selalu ada bahasa rekomendasi transaksi yang dilarang.

## 2. P1 — Teks halaman mencampurkan artikel dengan indeks dan berita terkait

Bukti: `cases/SRAJ-6ab54d09499a/evidence/E002.json` dan `cases/EPAC-2e3aa51a830f/evidence/E002.json` berisi deretan indeks saham dua kali, profil penulis/editor, artikel utama, Related News, dan Trending. Candidate body dipotong 1.500 karakter sehingga sebagian besar ruang habis oleh indeks; judul baru muncul di ujung.

Lokasi kode: `backend/app/research/documents.py:152`, `backend/app/research/evidence.py:90`, `backend/app/scan.py:245` dan klasifikasi pada `pipeline/hypothesize.py`.

Reproduksi pada SRAJ: classify(title, seluruh_teks) menghasilkan `rights_issue`; setelah bagian artikel utama dipisahkan dari Related News, classify menghasilkan None (yang pada antrean menjadi general_action). Kata rights issue berasal dari judul terkait PART, bukan aksi utama SRAJ.

Perbaikan: ekstraksi artikel utama yang teruji untuk sumber yang dipakai; pisahkan navigasi/rekomendasi berita dari evidence. Jalankan klasifikasi pada artikel bersih. Jangan sekadar menambah panjang konteks. Simpan teks mentah untuk audit dan teks bersih dengan versi ekstraktor. Cache lama harus diinvalidasi secara berversi ketika cara ekstraksi berubah.

Tes penerimaan: menambahkan sidebar berisi rights issue tidak boleh mengubah kategori artikel SRAJ; kandidat ringkas harus memuat inti artikel, bukan ticker indeks.

## 3. P1 — EPAC: debt-only dihitung dari fakta yang tersisa, bukan bukti eksklusivitas

Sumber EPAC menyebut tiga penggunaan: belanja modal pengembangan usaha, pengurangan sebagian utang bank, dan modal kerja. Namun fakta yang lolos hanya debt_repayment dan `research.signals` memberi red +2 dengan kode `funds_debt_only`, beralasan seluruh dana hanya melunasi utang.

Lokasi: `backend/app/research/scoring.py:162`, fungsi `debt_only_signals`. Aturan memeriksa apakah kategori debt ada dan kategori pembangun bisnis tidak ada dalam daftar fakta tersaring. Hilangnya fakta ekspansi/modal kerja diperlakukan sebagai bukti bahwa penggunaan tersebut tidak ada. Reproduksi memakai fakta EPAC yang tersimpan menghasilkan sinyal keliru yang sama.

Perbaikan: eksklusivitas memerlukan bukti positif yang eksplisit serta pemeriksaan konteks, atau status cakupan penggunaan dana yang lengkap. Jika ada kategori lain yang gagal validasi, simpulkan penggunaan dana belum lengkap, bukan hanya utang. Jangan mengubah bobot global agar contoh ini terlihat hijau.

Tes penerimaan: kutipan campuran ekspansi + utang + modal kerja tidak menghasilkan debt-only walaupun hanya fakta utang tersisa setelah review. Kasus benar-benar hanya utang diuji secara terpisah dengan bukti eksplisit.

## 4. P1 — Penggunaan dana yang dapat bersamaan dianggap saling bertentangan

Lokasi: `backend/app/research/facts.py:169`, `compare_independent_facts`. Bila dua fakta dengan topik use_of_funds mengutip kalimat yang sama tetapi kategorinya berbeda, kode dapat memberi `contradicted`.

Reproduksi: fakta core_expansion dan debt_repayment dari kutipan multi-penggunaan dana EPAC menghasilkan contradicted. Padahal keduanya bisa benar bersamaan. Salah satu reviewer yang hanya mengekstrak satu kategori bukan bukti bahwa kategori lainnya salah.

Perbaikan: bedakan omission/tidak ditemukan oleh reviewer dari kontradiksi eksplisit. Use of funds harus mendukung beberapa kategori dalam satu kalimat. Kontradiksi perlu menyatakan objek, tahap, dan nilai yang memang tidak kompatibel. Jangan langsung meloloskan fakta yang tidak didukung; gunakan pending/not_supported sesuai bukti.

Tes penerimaan: pembaca A menemukan ekspansi+utang dan B hanya utang; kategori ekspansi tidak otomatis menjadi contradicted. Bukti yang secara eksplisit menyangkal penggunaan tertentu tetap menolak fakta tersebut.

## 5. P1 — Pemicu frontier melewatkan label reviewer yang jelas berbeda

Bukti SRAJ: reviewer pertama `structural_red_flag`, kedua `growth_catalyst`, keduanya `agrees_with_label=false`; frontier menyimpan `not_triggered` dengan pesan tidak ada konflik.

Lokasi: `backend/app/research/engine.py:477–478`. `label_conflict` ditentukan dari perbedaan boolean setuju terhadap label analis, bukan dari perbedaan label reviewer yang sesungguhnya. Dua label berbeda bisa sama-sama tidak setuju dengan analis.

Perbaikan: gunakan label aktual dan cakupan fakta penyebab konflik. Eskalasikan bukti relevan tanpa membuat suara mayoritas menjadi kebenaran. Bedakan reviewer sepakat menolak analis dari reviewer saling bertentangan.

Tes penerimaan: input dua label di atas dengan flags [False, False] harus mencatat konflik dan menjalankan jalur eskalasi bila frontier aktif dan budget tersedia. Mode shadow tetap tidak mengubah keputusan.

## 6. P1 — Kutipan benar belum menjamin kategori fakta benar

SRAJ memiliki fakta supported `core_expansion` dari kalimat umum tentang mendukung pertumbuhan/permodalan anak usaha. Kalimat berikutnya dalam sumber justru menyatakan bidang usaha dan penggunaan dana spesifik tidak dirinci. Klaim 'ekspansi bisnis inti' lebih kuat daripada yang dibuktikan sumber.

Reviewer juga mencatat nama emiten sendiri sebagai new_party, serta suntikan modal kepada anak usaha sebagai asset_injection. KETR menunjukkan perusahaan sekuritas yang ditunjuk sebagai pihak yang diklasifikasi affiliate. Itu masalah peran pihak dan arah transaksi, bukan sekadar kecocokan nama/kutipan.

Lokasi: `research/facts.py:party_name_issue`, `verified_facts`, serta identitas emiten yang dihimpun di `research/engine.py:268`. Regex fallback nama emiten berakhir `Tbk` langsung diikuti ticker; bentuk `Tbk. (SRAJ)` tidak cocok. Nama dengan `(EPAC)` yang ikut dihasilkan model juga tidak identik dengan nama issuer tanpa anotasi pada normalisasi saat ini.

Perbaikan: normalisasi identitas emiten secara konsisten, bedakan issuer/penerima/broker/afiliasi beserta hubungan dan arah transaksi. Validasi kutipan adalah lapisan pertama; ekstraksi perlu konteks sebelum/sesudah dan kategori unknown bila bukti spesifik tidak tersedia. Uji parafrase dan negasi, bukan hanya kehadiran kata 'pertumbuhan'.

## 7. P1 — Ringkasan dan informasi parsial dikosongkan untuk setiap needs_review

Lokasi: `backend/app/pipeline/presentation.py:screen_outcome` mengubah gate ke needs_review bila status riset bukan completed. `pipeline/gate.py:76` kemudian menghapus seluruh field prosa untuk gate selain passed.

Dampak teramati: lima kartu terbaru kehilangan ringkasan walaupun daftar istilah dilarang kosong. Konflik satu fakta atau koneksi gagal berujung pesan generik, sehingga pengguna tidak melihat fakta yang masih tersedia, apa yang belum diketahui, dan tindakan berikutnya.

Perbaikan: pisahkan gate kepatuhan bahasa dari status kelengkapan/keandalan analisis. Tetap tahan kesimpulan yang belum sah, tetapi tampilkan fakta parsial yang benar-benar lolos, sumber, konflik spesifik, dan kegagalan infrastruktur. Label akhir inconclusive tidak boleh ditampilkan seolah berarti aksi korporasi berisiko struktural.

Tes penerimaan: ketika satu klaim gagal tetapi fakta lain valid, kartu menampilkan fakta valid dan alasan penahanan. Konten yang benar-benar melanggar gate tetap tidak diterbitkan. Jangan sekadar mengganti warna atau memaksa label completed.

## 8. P2 — Timeout frontier tidak diagnostik; retry menghabiskan budget tanpa penilaian

Bukti EPAC/KETR: masing-masing dua percobaan sekitar 15 detik, tetapi pesan menyebut tidak ada jawaban dalam 180 detik. PART mencapai batas tiga percobaan per kandidat. Tidak ada verdict frontier yang berhasil pada kasus-kasus ini.

Lokasi: `frontier/client.py:103` menetapkan connect timeout min(15, timeout), tetapi seluruh `httpx.TimeoutException` pada baris 163 dipetakan ke pesan timeout umum. Pola waktu konsisten dengan connect timeout, tetapi jenis timeout asli tidak disimpan sehingga akar jaringan tidak dapat dipastikan dari audit ini.

Perbaikan: bedakan ConnectTimeout, ReadTimeout, WriteTimeout, PoolTimeout dan durasi aktual; simpan fase yang aman tanpa key. Diagnostik koneksi/metadata harus tersedia terpisah dari generasi berbayar. Jangan langsung memperbesar timeout generasi 180 detik untuk masalah koneksi 15 detik. Pertahankan pencatatan budget konservatif saat biaya tidak diketahui; jangan menghapus ledger asli untuk mengulang percobaan.

Tes penerimaan: fake ConnectTimeout melaporkan fase koneksi dan batas 15 detik; ReadTimeout melaporkan fase baca. Retry terbatas, pesan UI menyebut masalah akses frontier, tidak menyamarkannya sebagai keputusan analitis.

## 9. P2 — SEMA: dua masalah berbeda disatukan

Judul sumber: `Acquisition or Lost of Material Contract [ SEMA ]`. Kata acquisition memicu control_change padahal judul tersebut mengenai kontrak material, bukan bukti akuisisi perusahaan/pengendali. Isi PDF belum tersedia sehingga kategori final tidak boleh dipastikan.

`cases/SEMA-6b0821b04a3c/evidence/fetch_failures.json` mencatat robots.txt HTTP 403 untuk dua URL PDF. UI hanya menyebut PDF belum tersedia atau memerlukan OCR. Belum ada bukti bahwa PDF telah dibaca dan membutuhkan OCR.

Perbaikan: klasifikasi frasa kontrak dengan konteks; jangan menyamakan acquisition of contract dengan perubahan pengendali. Propagasikan kegagalan robots/source HTTP secara spesifik. Dapatkan sumber melalui jalur yang diizinkan atau dokumen yang disediakan pengguna; jangan melewati kontrol akses.

## 10. Kalkulasi dan laporan emiten

Dalam pemeriksaan sebelumnya pada sesi audit ini, 31 hitungan dari enam laporan terbaru dicocokkan: TTM revenue/earnings/OCF terhadap empat baris sumber; PE terhadap market cap/laba TTM; PB terhadap market cap/ekuitas induk; dan konversi kas terhadap OCF/laba. Tidak ditemukan selisih aritmetika pada cakupan tersebut. Ini bukan audit lengkap seluruh formula, basis periode, mata uang, indikator teknikal, atau kebenaran data Sectors.

Contoh BBRI tersimpan: laba TTM 62.322.523.000.000; OCF TTM 14.075.589.000.000; konversi kas 0,225850757; market cap 496.643.692.352.970; PE hitungan 7,9689279003. Nilai ini konsisten secara aritmetika dengan input tersimpan.

Tetapi klaim model tidak selalu benar atau reviewer terlalu ketat: pada BBRI, reviewer menolak klaim yang menggabungkan OCF dan konversi kas dengan alasan format, bukan bukti angkanya salah. Pada TLKM, model menyebut arah premi/diskon tidak sesuai metrik dan ditolak kode. Jangan menyamakan semua klaim ditolak dengan bug rumus; jangan pula menyamakan hitungan konsisten dengan interpretasi benar.

Laporan terbaru per ticker masih berasal dari periode pengujian sebelumnya. PTBA gagal menghubungi endpoint Ollama Colab; HATM memiliki klaim berita pending karena pembanding Gemma gagal; INDR kekurangan snapshot berita replay. Sejumlah valuation pada ticker lain insufficient_data. Mengubah kode hari ini tidak menghitung ulang laporan lama secara otomatis.

## 11. Urutan perbaikan dan pengujian

1. Simpan fixture regresi dari hasil dan sumber yang sudah ada sebelum mengubah apa pun.
2. Bersihkan ekstraksi artikel dan klasifikasi kandidat (SRAJ/SEMA).
3. Perbaiki multi-use-of-funds, bukti eksklusivitas debt-only, serta identitas/peran pihak.
4. Perbaiki pemicu konflik label frontier dan diagnostik timeout.
5. Pisahkan informasi parsial, kesimpulan analitis, kepatuhan bahasa, dan kegagalan infrastruktur pada UI.
6. Jalankan fixture deterministik tanpa jaringan: hasil before/after, alasan perubahan, fakta yang tetap tertahan. Jangan memaksa semua kartu hijau.
7. Setelah itu, replay terkendali pada data dev dengan cache/snapshot yang jelas; generasi frontier berbayar hanya bila dipilih secara eksplisit. Jangan overwrite artefak audit lama.
8. Evaluasi label terhadap manusia dan periksa peran/tanggal/satuan selain hitungan aritmetika. Uji data holdout hanya sebagai evaluasi akhir, bukan untuk menyetel perbaikan.

Kriteria selesai: EPAC tidak lagi menghasilkan debt-only dari bukti campuran; SRAJ tidak diklasifikasi rights issue akibat sidebar; label reviewer SRAJ yang berbeda memicu eskalasi; issuer tidak menjadi penerima eksternal sendiri; timeout menjelaskan fase sebenarnya; kegagalan PDF berbeda dari OCR; pengguna tetap melihat fakta terverifikasi dan alasan penahanan yang spesifik.

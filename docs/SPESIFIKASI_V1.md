# SignalGate — Spesifikasi Produk v1

Status: **draft untuk disepakati tim**. Tahap 1 dari backlog 16 September 2026.
Dokumen ini menetapkan apa yang produk janjikan dan apa yang sengaja tidak dijanjikan.

Katalog sinyal **tidak disalin ke sini**. Sumber kebenarannya `backend/app/catalog.py`, yang dapat
dieksekusi dan diuji: `tests/test_catalog.py` menolak setiap aturan yang ada di kode tetapi tidak di
katalog, dan sebaliknya. Dokumen yang menyalin aturan akan menyimpang, dan ketika itu terjadi tidak
ada yang tahu mana yang benar.

---

## 1. Lingkup v1

| Aspek | Keputusan |
|---|---|
| Cakupan emiten | Banyak saham IDX, dibatasi watchlist yang didaftarkan pengguna — bukan seluruh bursa |
| Jenis data | Fundamental kuartalan/tahunan dan aksi korporasi |
| Penyimpanan | Lokal, satu instalasi SQLite; tanpa layanan pihak ketiga untuk data hasil |
| Model | Ollama lokal sebagai bawaan; adapter API sebagai opsi (Tahap 5) |
| Pengguna | Tiga orang pada satu instalasi, masing-masing punya identitas untuk jejak review |

**Di luar lingkup v1, dan disebutkan supaya tidak diasumsikan ada:** intraday dan sinyal teknikal,
eksekusi order atau integrasi broker, data selain IDX, berita kebijakan pemerintah, serta indeks
sektoral sebagai pembanding. Semuanya ada di daftar pengembangan setelah fondasi stabil.

## 2. Horizon

- **Horizon rekomendasi: enam bulan.** Setiap rekomendasi menyatakan tanggal terbit dan tanggal
  jatuh temponya secara eksplisit.
- **Evaluasi antara pada bulan ketiga.** Bukan penerbitan ulang, melainkan pemeriksaan apakah
  asumsinya masih berlaku.
- **Peninjauan di luar jadwal** dipicu informasi material: revisi laporan keuangan, pembatalan atau
  penundaan aksi korporasi, atau perubahan pengendali.

Horizon adalah bagian dari rekomendasi, bukan konteks di sekitarnya. Rekomendasi tanpa horizon tidak
dapat dievaluasi benar atau salahnya, jadi tidak boleh terbit.

## 3. Keluaran

Produk punya **dua kosakata keluaran yang terpisah**, dan membedakannya adalah keputusan produk yang
paling penting di dokumen ini.

### 3.1 Label penyaringan — menilai aksi korporasinya

Sudah berjalan hari ini. Menjawab: *pola aksi korporasi ini seperti apa?* Tidak menilai sahamnya.

| Label | Definisi | Syarat |
|---|---|---|
| `structural_red_flag` | Pola cocok dengan injeksi aset atau pergantian cangkang usaha | Bobot merah ≥ 4 **dan** unggul ≥ 2 atas bobot pertumbuhan |
| `growth_catalyst` | Penggalangan dana yang penerima dan penggunaannya sejalan dengan bisnis berjalan | Bobot pertumbuhan ≥ 3, merah ≤ 1, **dan** ada fakta ekspansi inti |
| `inconclusive` | Bukti belum cukup, atau pembaca independen tidak menyetujui pembacaan analis | Keadaan bawaan |

Contoh nyata dari kasus yang sudah dijalankan — **MGLV**, rights issue Rp2,54 triliun: terverifikasi
dana untuk ekspansi inti (2) dan modal kerja (1) melawan injeksi aset dari pihak terkait (2). Tidak
satu pun ambang tercapai, dan pembanding independen tidak menyetujui label draft. Hasil:
`inconclusive`, dan itu jawaban yang jujur untuk kasus yang memang belum jelas.

### 3.2 Keputusan rekomendasi — menilai sahamnya

**Belum diterapkan.** Menunggu metode valuasi Tahap 6.

| Keputusan | Definisi |
|---|---|
| `buy` | Harga acuan di bawah nilai wajar dengan selisih minimal `MARGIN_OF_SAFETY`, pada horizon yang dinyatakan |
| `belum ada rekomendasi` | **Keadaan bawaan.** Syarat BUY belum terpenuhi, termasuk ketika data lengkap tetapi selisih harga terhadap nilai wajar belum memenuhi ambang |

Hanya dua. Syarat lengkapnya di `catalog.DECISIONS`.

**Tidak ada `sell`.** Menyarankan jual kepada orang yang tidak memegang saham itu bukan saran keluar
melainkan ajakan short — produk berbeda dengan kewajiban berbeda. Pencabutan berarti rekomendasi
pembelian tidak lagi berlaku; bukan instruksi menjual. Ini juga menghapus ketergantungan yang sempat
muncul di draft sebelumnya: lapisan portofolio tidak lagi menjadi prasyarat Tahap 6.

### 3.3 Siklus hidup rekomendasi — apakah kesimpulannya masih berlaku

Sumbu terpisah dari keputusan, dan hanya berlaku bagi BUY yang sudah terbit. `belum ada rekomendasi`
tidak menerbitkan apa pun, jadi tidak ada yang bisa ditangguhkan atau dicabut.

| Status | Sifat | Kapan |
|---|---|---|
| `berlaku` | sementara | Dalam horizon, asumsi materialnya belum berubah |
| `ditangguhkan` | sementara | Aksi ditunda, laporan direvisi, informasi material baru, atau data wajib kedaluwarsa |
| `dicabut` | **akhir** | Aksi dibatalkan, kondisi pembatalan terpenuhi, asumsi terbukti salah, atau red flag struktural terbit |
| `kedaluwarsa` | **akhir** | Horizon terlampaui tanpa penilaian ulang |

**Ditunda bukan dibatalkan.** Penundaan menangguhkan karena dasarnya mungkin masih utuh; pembatalan
mencabut karena dasarnya hilang. Membedakan keduanya adalah inti tabel transisi di
`app/recommendation.py`.

Status akhir tidak dapat dihidupkan kembali — penilaian baru menerbitkan rekomendasi **baru**,
sehingga yang lama tetap dapat dievaluasi apa adanya. Mesin transisinya sudah diterapkan dan diuji
sekarang meski penerbitan BUY masih `planned`: logikanya deterministik dan tidak membutuhkan valuasi,
jadi menundanya hanya berarti menulisnya terburu-buru bersamaan dengan hal yang sulit.

### 3.4 Riwayat yang wajib disimpan

Setiap BUY menyimpan keputusan, waktu terbit, **harga acuan beserta waktunya**, nilai wajar, margin,
horizon, tanggal evaluasi, bukti (`case_id` dan `fact_id`), serta **versi aturan penilaian dan versi
katalog**. Daftar lengkapnya `catalog.RETAINED_FIELDS`.

Riwayat **hanya bertambah**. Setiap perubahan status menambah satu langkah berisi alasan, pemicu,
waktu, dan pelakunya — tidak pernah menimpa yang lama. Tanpa ini, pilot bertanggal Tahap 8 tidak
punya dasar untuk mengukur apa pun.

### 3.5 Dua ketetapan yang tidak boleh dilunakkan

**`belum ada rekomendasi` bukan `hold`.** Ini pernyataan bahwa produk tidak punya dasar yang cukup,
bukan saran menahan posisi. Menyamakan keduanya mengubah ketidaktahuan menjadi nasihat.

**Kekuatan sinyal dan kelengkapan bukti adalah dua sumbu berbeda.** Sinyal kuat di atas bukti tidak
lengkap tetap menghasilkan `belum ada rekomendasi`. Keduanya dilaporkan terpisah, tidak digabung
menjadi satu angka kepercayaan.

Marginnya pun harus dapat dihitung: `(nilai wajar − harga acuan) / nilai wajar ≥ MARGIN_OF_SAFETY`.
Angkanya **sengaja dibiarkan kosong** sampai Tahap 6 mengkalibrasinya; angka yang dikarang sekarang
akan dipakai seolah sudah teruji.

## 4. Rekomendasi umum versus rekomendasi berbasis portofolio

| | Rekomendasi umum | Berbasis portofolio |
|---|---|---|
| Pertanyaan | Bagaimana penilaian atas saham ini? | Apa artinya bagi portofolio ini? |
| Masukan | Fundamental, aksi korporasi, valuasi, harga acuan | Semua di kiri, **ditambah** posisi, harga perolehan, dan konsentrasi |
| Berlaku bagi | Semua pengguna, sama isinya | Satu pengguna |

Keduanya dapat berbeda, dan itu benar, bukan kontradiksi: emiten dengan penilaian umum `buy` dapat
menghasilkan `belum ada rekomendasi` berbasis portofolio ketika batas konsentrasi pengguna sudah
tercapai. Antarmuka wajib menyebut yang mana yang sedang ditampilkan.

v1 menerbitkan **rekomendasi umum saja**. Dengan `sell` dihapus, lapisan portofolio tidak lagi
menjadi prasyarat — ia menjadi penyempurnaan yang dapat menyusul setelah lapisan umum terbukti.

## 5. Katalog sinyal

Tiga belas sinyal terdefinisi di `backend/app/catalog.py`. Setiap entri menjawab empat pertanyaan:
data wajib, aturan, pengecualian, dan alasan penilaian.

| Asal | Jumlah | Dihitung oleh | Bobot |
|---|---|---|---|
| Fakta dari dokumen sumber | 9 | Model mengekstrak, Python memverifikasi kutipan dan memberi bobot | 1–3 |
| Data pasar | 4 | Python sepenuhnya, tanpa model | 1 |

**Status penerapan dibedakan tiga, bukan dua.** Draft pertama menandai seluruhnya "sudah berjalan";
itu klaim berlebihan. Yang benar, dihitung dari 25 kasus tersimpan di `cases/`:

| Status | Jumlah | Arti |
|---|---|---|
| `verified` | 8 | Pernah menyala pada kasus nyata |
| `untested` | 7 | Lulus unit test, belum pernah dijalankan pada data sungguhan |
| `planned` | 1 | Belum ada kodenya |

Setiap entri `untested` dan `planned` wajib menyebut penyebabnya, dan ada test yang menolak klaim
`verified` tanpa bukti di `cases/`.

**Satu sinyal sengaja tidak diterapkan.** `no_standby_buyer` — rights issue tanpa pembeli siaga —
membutuhkan dokumen yang memang akan menyebutkannya bila ada. Dari artikel berita, *"tidak disebut"*
tidak dapat dibedakan dari *"tidak ada"*, dan menyamakannya akan menerbitkan red flag palsu setiap
kali wartawan tidak menyebutkannya. Selain itu ketiadaan pembeli siaga bisa berarti sebaliknya:
pemegang saham lama sudah berkomitmen menyerap seluruhnya. Aturannya tidak boleh sepihak.

**Invarian: tanpa fakta, tidak ada label.** `decide()` menolak melabeli apa pun bila tidak ada satu
pun sinyal yang berasal dari dokumen sumber. Sebelumnya invarian ini hanya bertahan karena kebetulan
aritmetika — seluruh sinyal data pasar berbobot 1 dan jumlahnya kurang dari ambang. Begitu
`controller_exit` berbobot 2 ditambahkan, data pasar saja sudah cukup mencapai ambang merah. Sekarang
syaratnya struktural. Data pasar memberi konteks pada aksi korporasinya; ia tidak menilai sahamnya.

Model tidak pernah menentukan bobot maupun label, dan hanya dapat menurunkan hasil ke
`inconclusive`. Itu berlaku sekarang dan tetap berlaku setelah Tahap 6.

## 6. Batas yang harus diputuskan sebelum Tahap 6

Dicatat di sini karena spesifikasi adalah tempatnya, bukan karena menghalangi pekerjaan.

- **Perizinan.** Menerbitkan `buy` untuk investor ritel Indonesia menyentuh ketentuan
  penasihat investasi. Backlog menempatkan tinjauan perizinan di Tahap 8; kalau produknya akan
  dijual, keputusan itu perlu diambil sebelum Tahap 6 menulis kodenya, bukan sesudahnya.
- **Hak penggunaan data.** Syarat lisensi Sectors API dan konten IDX untuk produk berbayar dan
  redistribusi.
- **Compliance Gate.** Gate sekarang menolak kata transaksi *dan* penilaian nilai. Tahap 6 mengubah
  perannya dari larangan menjadi pemeriksaan kelayakan: menolak rekomendasi yang tidak dapat
  ditelusuri ke fakta dan perhitungan. Sampai itu disepakati, gate tetap menolak — dan ada test yang
  memastikan kosakata `buy`/`sell` masih tertahan hari ini.

## 7. Selesai bila

- [x] Setiap label penyaringan punya definisi, syarat, dan contoh kasus nyata.
- [x] Keputusan rekomendasi tinggal dua (`buy`, `belum ada rekomendasi`); `sell` dihapus.
- [x] Keputusan dan siklus hidup dipisahkan menjadi dua kosakata yang tidak beririsan.
- [x] Alasan penangguhan, pencabutan, dan kedaluwarsa ditetapkan dan diuji terhadap mesin transisi.
- [x] Riwayat BUY menyimpan harga acuan berwaktu, horizon, bukti, dan versi aturan; hanya bertambah.
- [x] Syarat `buy` dapat dihitung, bukan dinilai bebas; ambangnya dibiarkan kosong sampai dikalibrasi.
- [x] Tiga belas sinyal punya data wajib, aturan, pengecualian, dan alasan.
- [x] Status penerapan dibedakan `verified` / `untested` / `planned`, dengan bukti dari `cases/`.
- [x] Keputusan produk deterministik: ambang, bobot, dan syarat ada di kode, bukan di prompt.
- [x] Katalog dan implementasi dikunci sinkron oleh test.
- [ ] **Disepakati tim bertiga.** Yang paling perlu diperdebatkan: daftar alasan pencabutan (§3.3),
      karena itulah yang menentukan kapan sebuah rekomendasi ditarik dari hadapan pengguna.

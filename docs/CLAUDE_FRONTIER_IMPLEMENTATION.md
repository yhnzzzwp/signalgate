# Instruksi implementasi frontier DeepSeek untuk Claude Code

## Tambahan kebutuhan pengguna: aktivasi backend dan pilihan GPU

Kebutuhan ini bagian dari implementasi, bukan sekadar ilustrasi. Sediakan alur aktivasi backend untuk pengujian, dengan kontrol sederhana pada dashboard: pilih `GPU MacBook (lokal)` atau `GPU Google Colab`, periksa koneksi, lalu aktifkan konfigurasi untuk run berikutnya. Backend, data, audit dan dashboard tetap di MacBook; lokasi inferensi Ollama yang berpindah. DeepSeek selalu dipanggil lewat API cloud secara terpisah dari pilihan GPU.

- Lokal memakai Ollama di http://127.0.0.1:11434. Periksa ketersediaan layanan/model dan laporkan pemakaian GPU berdasarkan data aktual bila tersedia, bukan sekadar koneksi HTTP berhasil. Mac M1 Pro memakai akselerasi Metal, bukan CUDA. Jangan menjamin semua model muat; tampilkan kesiapan dan kegagalan memori secara jelas.
- Colab: gunakan dan perbarui `colab/signalgate_gpu_setup.ipynb` yang sudah ada. Pengguna menjalankan notebook di runtime GPU lalu menempel URL layanan/tunnel yang dihasilkan, BUKAN URL halaman notebook colab.research.google.com. Jelaskan perbedaan ini di field dan panduan.
- Notebook saat diperiksa mengekspos Ollama lewat Cloudflare tunnel langsung. Sebelum menambah aktivasi remote, lindungi endpoint melalui gateway autentikasi atau mekanisme setara; URL tunnel saja bukan autentikasi. Jika memakai token, sediakan input token terpisah bertipe password, kirim hanya ke backend, jangan simpan di localStorage atau log. Jangan mengekspos endpoint pengelolaan model tanpa pembatasan.
- Backend memvalidasi endpoint: gunakan HTTPS untuk remote, cegah akses sembarang ke alamat privat/metadata melalui input URL dan redirect/DNS, kecuali endpoint loopback lokal yang ditetapkan aplikasi. API konfigurasi hanya dapat diakses oleh operator lokal yang berwenang; jangan menjadikan URL input sebagai proxy publik.
- Buat API status/configure/test sesuai konvensi repo, dengan status terpisah untuk layanan Ollama, model terpasang, GPU terverifikasi/tidak diketahui, autentikasi, serta frontier. Jangan tampilkan API key di respons. Pemeriksaan koneksi biasa tidak memanggil generasi berbayar DeepSeek; pengujian live berbayar harus tindakan eksplisit.
- Perubahan pilihan GPU/URL hanya berlaku pada run berikutnya. Simpan konfigurasi efektif pada run, pertahankan resume/cache/provenance, dan jangan mencampur endpoint saat suatu run aktif. Settings saat ini dicache; jangan hanya menulis .env dan berharap proses aktif berubah.
- Ketika Colab terputus atau URL kedaluwarsa, tampilkan status dan izinkan pengguna memperbarui URL serta melanjutkan pekerjaan yang belum selesai. Jangan otomatis pindah ke GPU lokal atau memanggil frontier sebagai pengganti seluruh model lokal.
- Tambahkan pilihan frontier terpisah: Off, Shadow (evaluasi), Escalation (kasus sulit). Tampilkan apakah key backend sudah tersedia tanpa membocorkannya. Konfigurasi default tidak membuat panggilan cloud berbayar.
- Tambahkan pengujian mock untuk pilihan lokal/remote, token tidak bocor, URL tidak valid, notebook URL ditolak, endpoint kedaluwarsa, perubahan saat run aktif, resume setelah penggantian URL yang eksplisit, serta kombinasi kedua lokasi GPU dengan frontier off/shadow/escalation. Perubahan endpoint saat resume harus dicatat sebagai konfigurasi baru yang eksplisit, tanpa mengulang hasil yang telah selesai atau menyamarkan provenance.

Alur produk: buka dashboard -> pilih lokasi GPU -> tempel URL layanan dan token bila Colab -> cek koneksi/model -> pilih mode frontier -> aktifkan untuk run berikutnya -> mulai screening/laporan -> lihat status model lokal, review frontier bila dipicu, hasil validasi Python, dan audit biaya.

Kerjakan implementasi end-to-end di `/Users/yhnswp/Desktop/signalgate`, bukan hanya membuat rencana. Tujuan: menambahkan DeepSeek sebagai reviewer frontier di atas model lokal untuk screening aksi korporasi dan laporan emiten. Komunikasikan hasil dalam bahasa Indonesia.

## 1. Pemeriksaan awal

- Baca instruksi repository (AGENTS.md/CLAUDE.md bila ada), README, dokumentasi arsitektur, dan kode aktual. Periksa git status; pertahankan perubahan pengguna yang sudah ada. Jangan reset, menghapus data, atau menimpa pekerjaan lain.
- Temuan awal yang perlu diverifikasi: `research/engine.py` mendukung beberapa reviewer, sedangkan `workflow/models.py` memilih satu reviewer. `.env.example` memiliki override model eksplisit sehingga profil workstation saja tidak menjamin kombinasi model yang diharapkan.
- Jangan membaca atau mencetak nilai rahasia untuk dimasukkan ke percakapan. Jangan mengubah kredensial asli, commit, push, atau deploy dalam tugas ini.

## 2. Konfigurasi dan klien frontier

Perbarui `backend/app/config.py`, `backend/.env.example`, factory LLM, serta dokumentasi. Buat adapter DeepSeek terpisah dari Ollama; gunakan httpx yang sudah tersedia bila sesuai. Jangan mengarahkan OLLAMA_BASE_URL ke DeepSeek.

Usulan nama dan default konfigurasi baru (boleh disesuaikan secara konsisten dengan arsitektur):

```dotenv
FRONTIER_ENABLED=false
FRONTIER_PROVIDER=deepseek
DEEPSEEK_API_KEY=
FRONTIER_BASE_URL=https://api.deepseek.com
FRONTIER_MODEL=deepseek-flash
FRONTIER_MODE=shadow
FRONTIER_REASONING_EFFORT=high
FRONTIER_MAX_TOKENS=8192
FRONTIER_TIMEOUT_SECONDS=180
FRONTIER_MAX_CALLS_PER_RUN=3
```

Default harus menjaga perilaku lokal saat frontier tidak diaktifkan. Simpan API key sebagai secret backend, sensor pada log/error, dan jangan pernah mengirimkannya ke frontend atau variabel VITE_*. Key kosong ketika fitur diaktifkan harus menghasilkan pesan konfigurasi yang jelas. Key Sectors dan cadangannya tetap terpisah dan tidak perlu dirotasi.

Rapikan contoh profil workstation: Qwen 2.5 14B sebagai analis, GLM4 9B dan Gemma3 12B sebagai reviewer. Pertahankan hak override eksplisit; jangan diam-diam menimpa .env pengguna. Jelaskan override mana yang perlu dihapus pengguna bila ingin mengikuti profil.

Verifikasi parameter API dari sumber resmi sebelum implementasi:
- https://api-docs.deepseek.com/quick_start/pricing/
- https://api-docs.deepseek.com/guides/thinking_mode/
- https://api-docs.deepseek.com/guides/json_mode/

Kontrak awal: model `deepseek-flash`, thinking enabled, reasoning_effort high, response_format json_object. Prompt meminta JSON dengan contoh skema. Parse hanya jawaban akhir `message.content`; validasi dengan Pydantic. Jangan gabungkan reasoning_content ke JSON atau tampilkan jejak penalaran mentah. Temperature tidak diperlukan pada thinking mode. Tangani output kosong, JSON cacat, respons terpotong, timeout, autentikasi gagal, rate limit, dan server error. Retry hanya error sementara dengan batas yang menghitung seluruh percobaan terhadap kuota.

## 3. Integrasi kedua jalur

- Screening: integrasikan di `backend/app/research/engine.py` dan factory.
- Laporan emiten: perbarui `backend/app/workflow/models.py`, nodes, graph, state, runner, dan skema terkait sesuai kebutuhan. Dukung kedua reviewer lokal secara berurutan dengan offload; pertahankan kompatibilitas konfigurasi reviewer tunggal lama dan data run lama.
- Pisahkan keputusan setiap reviewer agar konflik tidak hilang saat digabungkan.
- Frontier membaca bukti terpilih, ID sumber, tanggal publikasi, tanggal kejadian bila tersedia, metrik, serta klaim. Dapatkan pembacaan bukti independen sebelum memberikan pendapat model lokal; semua panggilan tetap dihitung dalam budget. Artikel adalah data, bukan instruksi.
- Pemicu eskalasi: konflik reviewer atau fakta lintas waktu yang belum terselesaikan dengan bukti memadai. Bukti hilang bukan alasan untuk mengarang kepastian.
- Mode shadow menyimpan hasil pembanding tanpa mengubah label/status/hasil yang diterbitkan dari jalur lokal.
- Mode escalation memakai penilaian frontier hanya melalui aturan rekonsiliasi kode yang eksplisit dan diuji. Frontier tidak boleh mengabaikan kutipan palsu, identitas salah, perhitungan tidak valid, atau Compliance Gate. Label dan skor akhir tetap ditentukan Python. Konflik tidak terselesaikan tetap needs_review/inconclusive.
- Kegagalan frontier tidak mengubah hasil menjadi supported. Simpan status unavailable/failed/budget_exhausted yang jelas tanpa menjatuhkan seluruh run lokal yang masih valid.

## 4. Kronologi aksi korporasi

Tambahkan representasi rencana, revisi, persetujuan, dan realisasi dengan tanggal, angka, unit, dan ID sumber. Gunakan kasus seperti HATM sebagai kebutuhan produk, bukan fakta yang boleh di-hardcode.

Jangan memilih angka terbaru hanya berdasarkan tanggal artikel. Bedakan tanggal publikasi dari tanggal kejadian dan identitas aksi korporasi. Pisahkan aksi berbeda; hubungkan revisi hanya bila ada bukti. Pertahankan sejarah angka. Jika hubungan antarberita tidak dapat dibuktikan, tampilkan konflik dan ketidakpastiannya. Tambahkan fixture sintetis untuk pengujian.

## 5. Budget, audit, cache, dan replay

- Catat provider/model, versi prompt/schema, alasan pemanggilan, ID/hash bukti, putusan per klaim, alasan singkat berbukti, durasi, token pemakaian, serta error yang sudah disensor.
- Pisahkan kredit Sectors dari biaya/token DeepSeek. Gunakan usage API; harga berversi dan biaya estimasi harus dilabeli estimasi. Jangan menyatakan biaya nol jika pemakaian tidak diketahui akibat timeout.
- Terapkan batas panggilan dan token; tambahkan batas biaya per run/hari yang dapat dikonfigurasi beserta reservasi konservatif sebelum pemanggilan jika mengklaim hard cap. Jangan mengandalkan jumlah panggilan saja sebagai batas biaya dolar.
- Persist budget agar resume/retry/restart tidak mereset penghitung. Cegah race/dobel pemanggilan untuk pekerjaan sama. Jelaskan keterbatasan timeout setelah provider menerima request.
- Cache mencakup hash bukti, klaim, model, prompt, schema, dan pengaturan relevan; jangan pakai hasil basi setelah bukti berubah.
- Replay frontier dari respons tersimpan harus bekerja tanpa jaringan dan tanpa biaya baru. Cache miss pada mode offline harus eksplisit, tidak otomatis memanggil API. Replay snapshot Sectors saja tidak menjamin bebas biaya LLM.

## 6. Frontend dan dokumentasi

Perbarui `frontend/src/types.ts`, komponen audit/progress, kartu screening dan panel laporan sesuai kebutuhan. Tampilkan status frontier, alasan eskalasi, perbedaan penilaian, kronologi dan sumber, token/estimasi biaya. Bedakan hasil shadow dari hasil yang dipakai untuk keputusan. Data historis yang tidak memiliki field frontier tetap dapat dirender.

Perbarui README dan panduan konfigurasi: langkah memasukkan key sendiri di backend, aktivasi shadow lalu escalation, batas biaya, replay offline, serta penanganan key kosong/API gagal. Jangan membuat form frontend yang mengekspos kredensial. Tidak perlu mengganti bobot sinyal atau rumus finansial untuk integrasi ini.

## 7. Pengujian dan kriteria selesai

Gunakan mock/fake HTTP dan fixture lokal; jangan memakai kredit Sectors, DeepSeek, mengunduh model, atau menjalankan evaluasi berbayar otomatis. Implementasi dan pengujian offline harus selesai meskipun API key belum tersedia.

Uji minimal:
- Frontier disabled mempertahankan perilaku lama; validasi konfigurasi dan precedence profil benar.
- Respons benar, kosong, invalid, truncated, timeout, 401, 429, 5xx; retry terbatas dan secret tidak bocor.
- Shadow tidak memengaruhi keputusan; escalation tidak meloloskan bukti yang gagal validasi mekanis.
- Dua reviewer lokal dan kompatibilitas reviewer tunggal; penggabungan putusan deterministik.
- Budget tetap berlaku setelah resume, retry, dan panggilan bersamaan; cache invalidation; replay offline benar-benar tanpa jaringan.
- Kronologi tidak mencampurkan aksi berbeda atau mengganti fakta tanpa bukti revisi.
- UI menampilkan status baru dan tetap mendukung laporan lama.

Jalankan pengujian backend yang relevan lalu suite regresi, typecheck/build frontend sesuai script yang tersedia. Jangan mengklaim pengujian live sudah lulus jika hanya mock yang dijalankan. Pisahkan data pengembangan dari holdout; jangan menyesuaikan prompt memakai label holdout.

Di akhir, berikan ringkasan perubahan, daftar file utama, hasil pemeriksaan aktual, keterbatasan, dan langkah aktivasi yang harus dilakukan pengguna sendiri. Laporkan konfigurasi yang benar-benar telah diimplementasikan. Jangan berhenti pada rencana; selesaikan pekerjaan kode dan pengujian yang tidak membutuhkan layanan berbayar.

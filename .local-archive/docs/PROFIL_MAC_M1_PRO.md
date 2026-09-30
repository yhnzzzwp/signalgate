# SignalGate — profil MacBook Pro M1 Pro 16 GB

Keputusan desain 17 September 2026. Target pengguna: M1 Pro, unified RAM 16 GB, SSD 512 GB.
Ini rancangan konfigurasi dan pembagian kerja; belum instalasi, pengunduhan model atau benchmark.
Mengoreksi asumsi workstation pada rancangan awal orkestrator.

## Stack yang dipilih

| Bagian | Pilihan awal | Alasan |
|---|---|---|
| Runtime | Ollama native macOS | Adapter proyek sudah memakai API Ollama; kurangi perubahan bersamaan |
| Orkestrasi | LangGraph dengan node Python dan node LLM | Dependensi, routing, state dan checkpoint; tidak setiap node membutuhkan agent |
| Model harian | Qwen2.5 7B Instruct Q4_K_M | File katalog sekitar 4,7 GB, lebih banyak ruang untuk konteks dan aplikasi lain |
| Reviewer | Gemma 3 4B quantized, tag dan digest diperiksa saat setup | Pembaca berbeda yang lebih kecil; wajib evaluasi, bukan otoritas kebenaran |
| Analisis mendalam | Qwen2.5 14B Instruct Q4_K_M, opsional | File katalog sekitar 9 GB; kelayakan ditentukan peak memory dan latensi nyata |
| Quantitative | Pandas, NumPy, indikator custom yang diuji | Formula dan handling data jelas; pandas-ta dapat ditambahkan jika memudahkan |
| Model prediktif | XGBoost/LightGBM ditunda | Memerlukan target, data latih, temporal holdout, dan evaluasi leakage yang terpisah |
| Penyimpanan | SQLite + artefak JSON/PDF lokal | Cukup untuk instalasi lokal; PostgreSQL ketika kebutuhan deployment/concurrency muncul |
| Pencarian bukti | Metadata, teks dan SQLite FTS lebih dulu | Vector DB hanya jika retrieval semantik terbukti perlu |
| MLX | Kandidat eksperimen berikutnya | Bandingkan pada workload sama; tidak diasumsikan otomatis lebih cepat |

Chroma/Qdrant dan PostgreSQL tidak diperlukan untuk membuat orkestrasi multi-agent berjalan.
512 GB adalah kapasitas disk, bukan tambahan RAM untuk model. Rencanakan ruang untuk model,
snapshot, PDF dan artefak; kapasitas kosong aktual belum diperiksa. Tidak ada kebutuhan mendownload
checkpoint full precision jika memakai model quantized yang tersedia.

## Quantization, parameter dan build

- Quantization mengurangi presisi representasi bobot. Model 14B tetap memiliki sekitar 14 miliar
  parameter setelah dijadikan 4-bit; ia tidak berubah menjadi model 4B.
- Tag Ollama `qwen2.5:14b` dan `qwen2.5:7b` saat diperiksa sudah Q4_K_M. Tidak perlu di-quantize lagi
  untuk menjalankan agent atau mengubah prompt.
- `num_ctx`, `num_predict`, temperature, system prompt dan struktur LangGraph adalah pengaturan
  inferensi/aplikasi. Mengubahnya tidak memerlukan training atau quantization ulang.
- `ollama create` dengan basis model yang sudah quantized dan parameter baru membuat konfigurasi
  model; itu bukan otomatis proses quantization baru. Import GGUF memakai bobot yang sudah disiapkan.
- Jika kelak melatih/fine-tune lalu menghasilkan bobot baru, alur export/merge/quantization merupakan
  pekerjaan terpisah. Pilih format runtime target; jangan melakukan kompresi ulang berantai pada
  bobot Q4 untuk mengejar akurasi. Untuk format quant lain, mulai dari checkpoint sumber yang sesuai.
- Q4 biasanya menghemat memori dan dapat meningkatkan throughput; kecepatan bergantung runtime,
  perangkat, panjang input/output. Akurasi dapat berubah; tidak ada persentase penurunan universal.
- KV cache menyimpan konteks dan terpisah dari bobot. Bobot Q4 tidak otomatis membuat KV cache Q4.

## Batas memori dan konfigurasi awal

16 GB dipakai bersama macOS, CPU/GPU, aplikasi, bobot, KV cache, dan buffer runtime. Ukuran unduhan
9 GB tidak membuktikan bahwa seluruh run 14B hanya memakai 9 GB. 14B Q4 layak dicoba secara serial,
tetapi belum dapat dijanjikan nyaman dengan browser/IDE dan konteks panjang.

Pilihan awal yang akan diuji:

| Parameter | Default rancangan |
|---|---|
| Model resident | 1 |
| Inferensi bersamaan | 1 |
| Konteks awal | 4096 token; 8192 hanya setelah uji memori dan kualitas |
| Budget output per task | 1024 token awal; pecah hasil panjang menjadi batch klaim |
| Temperature | 0 untuk ekstraksi/validasi terstruktur |
| Perbaikan analisis | Maksimal 1 putaran terarah |
| Keep-alive | Pertahankan model antarperan berurutan; unload sebelum model lain |
| Weight quantization | Q4_K_M untuk Qwen |
| KV cache | Baseline f16; uji q8_0 bila backend mendukung Flash Attention |

Pengaturan server yang relevan: `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`.
Pengaturan request yang relevan: `options.num_ctx`, `options.num_predict`, `options.temperature`.
Pada Ollama macOS app, variabel shell/backend tidak otomatis diteruskan ke proses Ollama; pengaturan
harus diberikan pada proses server yang benar. Belum ada pengaturan sistem yang diubah.

**Integrasi dengan kode sekarang:** `Settings.ollama_num_ctx` default 16384 dan adapter menetapkan
`OUTPUT_TOKEN_BUDGET=2048`. Parameter request dapat mengalahkan default Modelfile. Mengubah
Modelfile saja tidak menerapkan profil ini. Prompt, schema dan kutipan juga harus masuk budget
input setelah menyisakan output; kurangi ukuran batch/chunk, bukan memotong fakta sembarangan.

## Pembagian script dan agent

| Pekerjaan | Pelaksana |
|---|---|
| Fetch API, cache, pagination, retry jaringan, deduplikasi tanggal/berita | Script |
| Perhitungan indikator, return, rasio keuangan dan tabel pembanding | Script |
| Cek unit/periode, schema, ID bukti, kutipan literal, angka hasil formula | Script |
| Grafik, status task, checkpoint, simpan hasil, konfirmasi publikasi | Script |
| Jadwal analisis emiten standar | LangGraph/script |
| Memahami pertanyaan terbuka dan menyusun rencana terbatas | Planner LLM ketika perlu |
| Ekstraksi fakta dari dokumen dan interpretasi berita | Agent news |
| Menjelaskan kondisi bisnis dan valuasi dengan bukti | Agent research, keluaran fundamental/valuation terpisah |
| Menguji apakah sumber mendukung interpretasi dan mencari konflik | Reviewer LLM + pemeriksaan script |
| Menggabungkan temuan empat bidang menjadi laporan | Synthesis LLM |

Technical tetap dimensi wajib. Indikator dan deskripsi standar dapat dibentuk script; synthesis
menjelaskan hubungannya dengan news/fundamental/valuation. Bila pertanyaan technical membutuhkan
interpretasi khusus, orchestrator dapat menambahkan task LLM yang terarah.

Jalur standar sekitar empat panggilan logis LLM: ekstraksi berita → interpretasi bisnis/valuasi →
review → sintesis. Ini bukan hard limit saat dokumen perlu dipecah; jumlah batch, retry dan waktu
total tetap dibatasi dan dicatat. Empat dimensi laporan tidak berarti empat model berbeda.

LangGraph checkpoint menyimpan state, bukan otomatis menjamin side effect exactly-once. Pengambilan
ulang, penulisan kartu dan konfirmasi publikasi tetap memerlukan idempotency key/version. Gunakan
run ID yang konsisten dan satu sumber status kanonik; SSE membaca progres yang tersimpan.

## Gerbang benchmark sebelum menjadikan 14B default

Gunakan snapshot sama untuk 7B Q4 dan 14B Q4, konteks/output sebanding, catat tag/digest serta versi
runtime. Sertakan dokumen Indonesia, tabel keuangan, kutipan salah, aksi berbeda tahun, data hilang,
dan kasus konflik. Pisahkan waktu memuat model dari waktu proses prompt dan generasi.

Ukur memory pressure/peak memory, pertumbuhan swap, waktu per task dan per kasus, validitas JSON,
dukungan kutipan, kesalahan angka/identitas, serta kasus yang perlu review. Evaluasi reviewer 4B
terhadap penilaian manusia: reviewer yang sering menolak fakta benar tidak dianggap otomatis lebih
aman. Jika gagal, klaim terkait tetap ditandai belum terverifikasi sambil memilih konfigurasi lain.

Pilih 14B sebagai default hanya jika memberi perbaikan kualitas yang terukur dengan latensi dan
memori yang dapat diterima. Jangan menjanjikan tokens/detik atau persentase akurasi sebelum uji di
mesin ini. Model yang lebih besar tetap dibatasi RAM meskipun quantized.

## Sumber resmi

- [Qwen2.5 14B Ollama](https://ollama.com/library/qwen2.5:14b): ukuran dan Q4_K_M.
- [Qwen2.5 7B Ollama](https://ollama.com/library/qwen2.5:7b): ukuran dan Q4_K_M.
- [Gemma 3 4B Ollama](https://ollama.com/library/gemma3:4b): model kandidat reviewer.
- [Ollama FAQ](https://docs.ollama.com/faq): konteks, concurrency, keep-alive dan KV cache.
- [Ollama import](https://docs.ollama.com/import): impor GGUF yang sudah disiapkan.
- [Qwen quantization](https://qwen.readthedocs.io/en/v2.5/quantization/llama.cpp.html): format dan batas akurasi quantization.
- [MLX LM](https://github.com/ml-explore/mlx-lm): runtime Apple Silicon dan pengelolaan model/konteks besar.
- [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) dan
  [persistence](https://docs.langchain.com/oss/python/langgraph/persistence).

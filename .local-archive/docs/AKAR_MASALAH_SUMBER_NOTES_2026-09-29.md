# Akar masalah sumber dan notes — 29 September 2026

Audit lokal read-only terhadap database dan artefak tersimpan, disertai pemeriksaan kode working tree. Tidak memverifikasi ulang berita di internet dan tidak menjalankan model/API berbayar. Perubahan kode yang sudah ada sebelumnya dipertahankan. Temuan tanggal 28 September dirujuk, bukan dianggap otomatis masih belum diperbaiki.

## Bukti keadaan hasil

Database berisi 14 kartu screening dan 70 laporan workflow. Laporan terakhir per 12 ticker berisi total 370 klaim: 336 supported, 28 withdrawn, satu unsupported aktif, dan lima pending aktif. Enam klaim aktif belum selesai tersebut berada di HATM. Ini tidak berarti semua panel lain lengkap: PTBA gagal menghasilkan interpretasi, INDR tidak memiliki snapshot berita yang dibutuhkan, dan beberapa panel kekurangan data.

Screening terbaru masih berasal dari 28 September; laporan emiten terbaru 21 September. Pembaruan kode tidak otomatis mengubah payload yang sudah tersimpan.

## Screening: tiga lapisan penyebab

1. **Ekstraksi sumber lama tercemar.** Body EPAC, SRAJ, PART, KETR di database dimulai dengan indeks saham. Audit sebelumnya membuktikan judul terkait PART mencemari kategori SRAJ. Perbaikan ekstraktor sudah ada di working tree dan tes regresinya lulus.
2. **Validasi makna dan kegagalan layanan.** EPAC: daftar penggunaan dana yang tidak lengkap pernah dianggap bukti eksklusif penggunaan untuk utang. SRAJ: pertumbuhan/permodalan terlalu cepat dianggap ekspansi inti. KETR/PART: konflik ekstraksi dan frontier gagal/budget habis. SEMA: kontrak material salah kategori serta pengambilan PDF ditolak; belum ada bukti PDF memerlukan OCR. Perbaikan terkait telah ditambahkan sebelumnya, tetapi efektivitas pada run model baru belum dibuktikan audit ini.
3. **Antrean mempertahankan input lama.** `backend/app/scan.py:242` membentuk event baru, tetapi bila key antrean sama, event dibuang dan entry lama dibaca kembali; hanya tanggal terbit kosong yang diperbarui. Key berasal dari ticker, URL, dan fingerprint. Dengan fingerprint sama, perbaikan ekstraktor tidak memperbarui body/bucket antrean. `needs_review` termasuk SETTLED_RESEARCH_STATUSES (`research/models.py:84`), sehingga scan biasa tidak meriset ulang kandidat tersebut. Retry dapat menjalankan kandidat lama tanpa menyegarkan event. Ini menjelaskan mengapa memperbaiki ekstraktor saja belum cukup.

Tindak lanjut: versi transformasi pada antrean, pembaruan event dari sumber bersih dengan jejak versi, invalidasi hasil yang terdampak, lalu retry selektif. Jangan menghapus kasus asli atau mereset budget.

## Laporan emiten: masalah berbeda dari screening

### Jejak sumber hilang pada jalur presentasi

`snapshot.py` menyimpan URL artikel pada `params.url`. Saat membentuk laporan, `nodes.py:926` hanya menyalin ID, status, waktu, hash, dan beberapa metadata; URL/judul tidak disertakan. `WorkflowPanels.tsx` menampilkan ID sumber sebagai teks dan tabel sumber tanpa tautan artikel. Karena itu pengguna tidak bisa menelusuri klaim ke artikel dengan mudah meskipun snapshot mempunyai URL.

Tindak lanjut: bawa URL dan judul sumber ke schema laporan serta tautkan setiap referensi klaim ke sumbernya. Bedakan sumber tersedia, benar-benar dibaca model, dan dipakai mendukung klaim; status sumber `ok` hanya menyatakan pengambilan berhasil.

### Validator angka berita membandingkan bentuk literal

Reproduksi langsung pada kode saat ini:

```python
unexplained_numbers('Pendapatan naik 11,3%.', [], 'Revenue rose 11.3%.')
# ['11,3']
```

`workflow/evidence.py` mendukung tafsir numerik untuk metrik, tetapi angka dari teks berita masuk sebagai token literal. Perubahan desimal titik ke koma bisa ditolak walaupun nilainya sama. Notes ICBP/MKNT/BMRI memuat kasus angka koma ditolak; perlu pemeriksaan kutipan masing-masing sebelum menyatakan semua kasus itu false rejection.

Tindak lanjut: normalisasi angka sumber dengan satuan, skala, tanda, dan konteks; jangan sekadar mengganti semua tanda baca atau melonggarkan validasi angka global.

### Notes audit bercampur dengan hasil aktif

`WorkflowPanels.tsx:124` merender seluruh panel.claims termasuk withdrawn. Klaim yang sudah ditarik diberi penanda tetapi masih bercampur dalam daftar yang sama. `nodes.py:507,525` menambahkan review_notes, lalu `nodes.py:535` menambahkan limitation kegagalan; putaran pemeriksaan berikutnya dapat mengulang pesan yang sama. HATM memang menyimpan pesan kegagalan Gemma dua kali.

Tindak lanjut: tampilkan klaim aktif sebagai hasil utama; simpan klaim ditarik beserta alasan dalam riwayat pemeriksaan. Kelompokkan notes menjadi kesalahan klaim, data kurang, keterbatasan metodologi, dan gangguan layanan. Deduplicate pesan dengan tetap menyimpan riwayat percobaan.

### Reviewer dapat menolak bukti benar

BBRI: reviewer menolak klaim OCF dan konversi kas karena menggabungkan dua metrik, meskipun alasannya sendiri mengakui nilai ada dalam data. Ini bukti penolakan karena format, bukan kesalahan angka. TLKM justru memiliki klaim arah premi/diskon yang memang salah dan ditarik. Kedua jenis notes tidak boleh diperlakukan sama.

Tindak lanjut: evaluasi reviewer menggunakan contoh berlabel; pemeriksaan format dipisahkan dari dukungan fakta. Jangan menghapus notes atau memaksa semua klaim menjadi supported.

## Pemetaan tindakan per contoh ticker

| Ticker | Penyebab yang terlihat | Tindakan |
|---|---|---|
| SRAJ, EPAC | Teks sumber tercemar dan interpretasi/kategori keliru | Segarkan event antrean dari ekstraksi bersih, lalu ulangi riset terarah |
| SEMA | Salah kategori kontrak; akses PDF ditolak | Kategori berbasis konteks dan alasan akses spesifik |
| KETR, PART | Konflik ekstraksi serta frontier gagal/budget | Periksa bukti/peran pihak dan layanan secara terpisah |
| BBRI | Kutipan berita ditolak; reviewer menolak format; klaim ditarik masih terlihat | Audit kutipan, evaluasi reviewer, pisahkan riwayat |
| TLKM | Arah premi/diskon salah; kutipan tidak cocok; klaim ditarik | Pertahankan penolakan sah, perbaiki generasi dan tampilan |
| MKNT | Klaim sumber/angka ditolak dan tujuh klaim ditarik | Periksa kutipan dan normalisasi angka bersatuan |
| HATM | Lima klaim berita pending karena reviewer gagal; satu tafsiran valuasi unsupported | Pulihkan runtime, ulangi penilaian; jangan menganggap kegagalan koneksi sebagai bantahan |
| PTBA | Runtime Ollama gagal; interpretasi tidak tersedia | Pulihkan runtime sebelum replay |
| INDR | Snapshot replay berita tidak ada | Lengkapi sumber dengan run yang sesuai |
| IDEA | Data semesteran/field sumber kosong; TTM tak dapat dibentuk | Pertahankan keterbatasan data dan sesuaikan metode dengan periode |

## Verifikasi dan batas

`backend/.venv/bin/python -m pytest tests/test_audit_2026_09_28.py -q`: **28 passed**, empat warning deprecation lxml. Ini menguji perbaikan screening sebelumnya pada fixture, bukan validasi run produksi baru atau bukti semua masalah workflow selesai. Tidak ada kode aplikasi, hasil database, atau artefak kasus yang diubah dalam audit ini.

Prioritas: (1) benahi jalur sumber sampai antrean dan tautan UI; (2) perbaiki validasi angka berita secara terukur; (3) bedakan hasil aktif dan riwayat notes; (4) replay selektif setelah runtime siap, membandingkan hasil sebelum/sesudah tanpa menimpa bukti lama.

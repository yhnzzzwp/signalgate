# Runtime & GPU: MacBook atau Colab, dari dashboard

Tab **Runtime & GPU** di dashboard memilih tempat model lokal (Ollama) berjalan dan mode reviewer frontier
untuk **run berikutnya**. Semua dikerjakan backend (`app/runtime.py`, `app/api/runtime_routes.py`); tidak
ada file env yang disalin atau ditimpa.

## Prinsip

- **Satu sumber kredensial.** Key Sectors dan DeepSeek hanya di `backend/.env`. `scripts_local/run_local.sh`
  dan `run_colab.sh` tidak lagi menjalankan `cp .env.local .env` / `cp .env.colab .env`; keduanya hanya
  menyalakan backend. Berganti MacBook/Colab tidak mengubah key, mode frontier, atau batas biaya.
  (`.env.local`/`.env.colab` lama tidak dibaca lagi; pindahkan isinya yang masih perlu ke `.env` sekali.)
- **Precedence.** Pilihan yang pernah diaktifkan dari dashboard (`data/runtime/config.json`) > nilai env.
  Selama belum pernah diaktifkan, target tercatat `env` dan `OLLAMA_BASE_URL` dari `.env` dipakai seperti
  dulu. Nilai eksplisit di `.env` tetap mengalahkan profil model.
- **Berlaku untuk run berikutnya.** Tiap run mengambil salinan konfigurasi sekali saat mulai. Aktivasi
  saat run berjalan tidak mengubah run itu.
- **Operator lokal.** Endpoint pengubah (`/runtime/check`, `/runtime/activate`) hanya menerima request dari
  loopback dan dari origin dashboard.

## Alur MacBook

1. Buka aplikasi Ollama. Tab **Runtime & GPU** → *MacBook (Ollama lokal)* — tidak perlu URL atau token.
2. **Cek kesiapan**: status dipisah menjadi backend hidup, endpoint, Ollama tersambung, model tersedia,
   GPU, dan frontier. Centang *Buktikan GPU* untuk memuat satu model sebentar lalu membaca `/api/ps`
   (`size_vram`); tanpa itu, GPU berstatus "belum diketahui" bila belum ada model dimuat — itu bukan gagal.
3. **Aktifkan untuk run berikutnya.**

## Alur Colab

1. Unggah `colab/signalgate_gpu_setup.ipynb` ke Colab, pilih runtime GPU, lalu **Run all** — notebook hanya
   berisi satu sel kode. Ollama hanya mendengar di `127.0.0.1`;
   tunnel Cloudflare diarahkan ke **gateway** (`colab/ollama_gateway.py`, port 11435 — bukan 8080, yang
   dipakai Jupyter Server Colab) yang mewajibkan
   `Authorization: Bearer <token>` dan hanya meneruskan `GET /api/version|tags|ps`, `POST /api/chat|generate|show`,
   serta `GET /gateway/info` (hasil `nvidia-smi`). Pull/delete/create/copy/push ditolak. Sel itu
   memverifikasi gateway secara lokal lebih dulu tanpa token → 401, dengan token → 200, pull → 403, lalu mencetak URL layanan dan token.
2. Di dashboard: pilih *GPU Colab*, tempel **URL layanan** `https://…trycloudflare.com` (bukan link halaman
   notebook — itu ditolak dengan petunjuk) dan **token**. **Cek kesiapan**, lalu **Aktifkan**.

Validasi URL Colab: wajib HTTPS, tanpa kredensial/path/query, dan setiap alamat hasil DNS harus publik
(bukan privat, loopback, link-local/metadata 169.254.169.254, atau CGNAT). Redirect selalu ditolak.

## Token gateway

- Disimpan backend di `data/runtime/secrets.json` (izin 0600, di-gitignore). Tidak pernah dikembalikan ke
  browser, log, status, laporan, atau audit; yang tampil hanya "terpasang" + sidik jari 10 karakter dan
  host tempat token itu diberikan. Field token di dashboard tidak disimpan di localStorage.
- **Terikat ke endpoint asal.** Token tersimpan hanya dikirim ke host tempat ia diberikan. Bila URL diganti
  dan field token kosong, token lama **tidak** dikirim ke host baru: cek kesiapan menandai
  `token_withheld`, dan aktivasi ditolak sampai kamu menempel token baru atau mencentang persetujuan
  eksplisit memakai ulang token lama untuk host itu. Token dari env (`OLLAMA_AUTH_TOKEN`) hanya dipakai
  untuk target `env`, tidak untuk host Colab yang dipilih dari dashboard.

## Aktivasi dan kesiapan

- Aktivasi ditolak (409, tidak ada yang diubah) bila Ollama tidak tersambung.
- Aktivasi juga ditolak bila **belum siap**: model belum lengkap, atau daftar model tidak terbaca (mis.
  `/api/tags` 503) — gagal membaca tidak pernah dianggap "semua tersedia". Tombol *Simpan tetap walau belum
  siap* menyimpan atas persetujuan eksplisit; konfigurasinya ditandai "tersimpan, belum siap".
- **Pembuktian GPU ditolak selama ada run analisis** (dan memegang kunci run yang sama selama berjalan,
  jadi analisis baru juga menunggu), supaya probe tidak berebut memori atau mengacaukan urutan offload.
  Cek tanpa probe tetap boleh kapan saja.
- URL Colab kedaluwarsa (tunnel 530/putus) → status "terputus". Backend tidak pernah otomatis pindah ke
  MacBook; kamu yang memilih.

## Resume dan provenance

Tiap run laporan menyimpan konfigurasi efektif nonrahasia di `data/workflow/<run_id>/runtime.json`
(target, URL, sidik jari token, analis, pembanding, mode frontier) beserta riwayatnya. Resume dengan
konfigurasi berbeda (mis. URL Colab baru, mode shadow → escalation) ditolak dengan daftar perubahan sampai
disetujui (`accept_config_change=true`; di dashboard tombol *Lanjutkan dengan konfigurasi baru*). Tahap yang
sudah selesai tetap dipakai; riwayat mencatat dari node mana konfigurasi baru berlaku, dan tiap
`node_timings` membawa `config_index`. Job screening mencatat konfigurasinya di audit (`stage=runtime`).

## Keterbatasan

- DNS diresolusi saat validasi; klien HTTP meresolusi ulang saat request, jadi DNS rebinding tidak ditutup
  sepenuhnya (redirect tetap ditolak).
- Token dicetak sekali di output notebook Colab supaya bisa disalin; jangan bagikan output notebook itu.
- "GPU terbukti" di MacBook berarti Ollama melaporkan `size_vram` > 0 untuk model yang dimuat; di Colab
  berarti `nvidia-smi` melihat GPU. Keduanya bukan pengukuran throughput.

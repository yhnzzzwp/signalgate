#!/bin/bash
# Sama dengan run_local.sh: backend dijalankan tanpa menyalin file env. Untuk memakai GPU Colab, jalankan
# notebook colab/signalgate_gpu_setup.ipynb, lalu di dashboard (tab "Runtime & GPU") pilih Colab, tempel
# URL layanan https://...trycloudflare.com dan token gateway yang dicetak notebook, cek kesiapan, aktifkan.
set -e
cd "$(dirname "$0")/.."
echo "Pilih lokasi GPU Colab di dashboard: tab Runtime & GPU -> Colab -> tempel URL + token -> Cek -> Aktifkan." >&2
source .venv/bin/activate
exec uvicorn app.main:app --reload

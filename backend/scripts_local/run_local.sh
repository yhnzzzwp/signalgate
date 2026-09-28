#!/bin/bash
# Menjalankan backend TANPA menyalin file env apa pun: backend/.env adalah satu-satunya sumber kredensial
# (Sectors, DeepSeek, batas biaya), jadi berganti MacBook/Colab tidak pernah menimpa key atau mode frontier.
# Lokasi GPU dipilih di dashboard (tab "Runtime & GPU") dan berlaku untuk run berikutnya.
# Catatan: .env.local/.env.colab lama tidak dibaca lagi; pindahkan isinya ke .env sekali saja.
set -e
cd "$(dirname "$0")/.."
source .venv/bin/activate
exec uvicorn app.main:app --reload

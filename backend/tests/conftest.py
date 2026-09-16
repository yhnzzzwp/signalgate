"""Isolasi lingkungan test sebelum modul aplikasi diimpor.

`app/main.py` membaca konfigurasi dan membangun session factory saat impor, dan `get_settings()`
di-cache. Jadi variabel lingkungan harus disetel di sini: conftest dimuat pytest lebih dulu daripada
modul test mana pun.

Tanpa ini, test yang memanggil `/scan/run` menulis baris nyata ke `backend/signalgate.db` milik
pengembang, sehingga dashboard terisi data palsu dan hasil test bergantung pada urutan jalannya.
"""
import os
import tempfile
from pathlib import Path

_DATABASE = Path(tempfile.mkdtemp(prefix="signalgate-tests-")) / "test.db"

os.environ["SIGNALGATE_DB_PATH"] = str(_DATABASE)
# Test tidak boleh menyentuh Sectors, dan jalur mode hemat itu sendiri yang diuji.
os.environ["SECTORS_API_ENABLED"] = "false"
os.environ["SECTORS_API_KEY"] = ""
# Pemuatan model asli tidak pernah diinginkan di dalam test.
os.environ["LLM_BACKEND"] = "off"

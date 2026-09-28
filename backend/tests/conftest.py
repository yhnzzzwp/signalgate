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
# Frontier berbayar: variabel lingkungan mengalahkan backend/.env, jadi key asli di .env pengembang tidak
# pernah terbaca dan test tidak pernah memanggil DeepSeek. Test frontier menyuntik klien palsu sendiri.
os.environ["FRONTIER_ENABLED"] = "false"
os.environ["DEEPSEEK_API_KEY"] = ""
# Ledger budget, cache frontier, dan pilihan runtime milik pengembang tidak boleh terbaca atau tertulis.
_SANDBOX = _DATABASE.parent
os.environ["FRONTIER_DIRECTORY"] = str(_SANDBOX / "frontier")
os.environ["RUNTIME_DIRECTORY"] = str(_SANDBOX / "runtime")
os.environ["OLLAMA_AUTH_TOKEN"] = ""

import httpx  # noqa: E402 - sengaja setelah env disetel
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Blok jaringan: transport httpx sungguhan menolak setiap request. Test memakai MockTransport,
    TestClient, atau server lokal sendiri, jadi key asli tidak mungkin terpakai walau bocor ke lingkungan."""
    def refuse(self, request):
        raise AssertionError(f"Jaringan sungguhan diblokir dalam test: {request.method} {request.url.host}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)

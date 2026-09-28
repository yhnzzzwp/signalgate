"""Tabel harga berversi dan estimasi biaya frontier.

Semua angka biaya di sini ESTIMASI: dihitung dari `usage` yang dikembalikan API dikali harga pada
tabel di bawah, yang disalin dari halaman harga resmi pada tanggal versinya. Tagihan sebenarnya bisa
berbeda (libur nasional Tiongkok dihitung off-peak oleh provider, harga bisa berubah). Kredit Sectors
dicatat terpisah dan tidak pernah dicampur dengan angka ini.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

PRICE_VERSION = "deepseek-2026-09-28"
PRICE_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing/"
CURRENCY = "USD"

# USD per 1 juta token.
_FLASH = {
    "off_peak": {"input_cache_hit": 0.003, "input_cache_miss": 0.15, "output": 0.60},
    "peak": {"input_cache_hit": 0.006, "input_cache_miss": 0.30, "output": 1.20},
}
PRICES: dict[str, dict[str, dict[str, float]]] = {
    "deepseek-flash": _FLASH,
    # Nama lama: masih diterima, dilayani DeepSeek-V4.1-Flash dan ditagih harga Flash.
    "deepseek-v4-flash": _FLASH,
    "deepseek-v4-pro": {
        "off_peak": {"input_cache_hit": 0.022, "input_cache_miss": 0.66, "output": 1.98},
        "peak": {"input_cache_hit": 0.044, "input_cache_miss": 1.32, "output": 3.96},
    },
}

# Versi model di balik nama API, dari baris "MODEL VERSION" halaman harga pada tanggal PRICE_VERSION.
MODEL_VERSIONS = {"deepseek-flash": "DeepSeek-V4.1-Flash", "deepseek-v4-flash": "DeepSeek-V4.1-Flash",
                  "deepseek-v4-pro": "DeepSeek-V4-Pro-0813"}

# 01:00-04:00 dan 06:00-10:00 UTC, Senin-Jumat. Libur nasional Tiongkok tidak diketahui kode ini,
# jadi hari itu tetap dihitung peak: estimasi bisa lebih tinggi dari tagihan, tidak pernah lebih rendah.
PEAK_WINDOWS_UTC = ((1, 4), (6, 10))

# Batas atas token input untuk RESERVASI, bukan tebakan rasio karakter. Tokenizer DeepSeek adalah BPE
# tingkat byte: setiap token mewakili sedikitnya satu byte UTF-8, jadi jumlah token teks <= jumlah byte-nya
# (berlaku juga untuk Unicode, simbol, dan JSON). Ditambah cadangan tetap untuk template chat (peran, token
# khusus) dan instruksi yang mungkin disisipkan provider untuk JSON mode. Ini asumsi terdokumentasi, bukan
# jaminan nominal absolut: bila usage aktual melampaui reservasi, ledger mencatat overshoot dan
# menghentikan panggilan berikutnya pada run itu (lihat ledger.py).
RESERVE_OVERHEAD_TOKENS = 512


def known_model(model: str) -> bool:
    return model in PRICES


def window(at: datetime) -> str:
    at = at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
    if at.weekday() >= 5:
        return "off_peak"
    return "peak" if any(start <= at.hour < end for start, end in PEAK_WINDOWS_UTC) else "off_peak"


def reserve_prompt_tokens(prompt_bytes: int) -> int:
    return int(prompt_bytes) + RESERVE_OVERHEAD_TOKENS


def reservation(model: str, prompt_bytes: int, max_tokens: int) -> tuple[int, float]:
    """(token, USD) reservasi satu panggilan: token input <= byte UTF-8 + cadangan, semuanya cache miss,
    output penuh `max_tokens` (termasuk penalaran), harga peak. Dibulatkan ke atas."""
    prices = PRICES[model]["peak"]
    prompt_tokens = reserve_prompt_tokens(prompt_bytes)
    usd = (prompt_tokens * prices["input_cache_miss"] + max_tokens * prices["output"]) / 1_000_000
    return prompt_tokens + max_tokens, math.ceil(usd * 1e8) / 1e8


def estimate(model: str, usage: dict | None, at: datetime) -> dict:
    """Estimasi biaya dari usage API. `usage` None = tidak diketahui, dan dilaporkan begitu, bukan nol."""
    if not usage:
        return {"cost_usd_estimate": None, "usage_known": False, "basis": "estimate", "price_version": PRICE_VERSION}
    if model not in PRICES:
        return {"cost_usd_estimate": None, "usage_known": True, "basis": "estimate", "price_version": PRICE_VERSION,
                "note": f"Harga {model} tidak ada di tabel {PRICE_VERSION}."}
    slot = window(at)
    prices = PRICES[model][slot]
    prompt = int(usage.get("prompt_tokens") or 0)
    hit = usage.get("prompt_cache_hit_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    if hit is None or miss is None:
        hit, miss = 0, prompt  # tanpa rincian cache, anggap semua miss (lebih mahal = aman)
    completion = int(usage.get("completion_tokens") or 0)
    usd = (int(hit) * prices["input_cache_hit"] + int(miss) * prices["input_cache_miss"]
           + completion * prices["output"]) / 1_000_000
    return {"cost_usd_estimate": round(usd, 8), "usage_known": True, "basis": "estimate", "window": slot,
            "price_version": PRICE_VERSION, "currency": CURRENCY}

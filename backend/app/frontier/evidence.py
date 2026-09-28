"""Potongan bukti untuk reviewer frontier.

Memotong artikel hanya dari awal membuang konteks yang justru menentukan, misalnya ralat/revisi angka di
paragraf terakhir atau kutipan klaim di tengah artikel. Potongan dipilih berdasarkan prioritas:
kutipan klaim -> kata koreksi/revisi -> pembuka -> tanggal dan angka beraksi -> identitas emiten -> penutup.
Hasilnya selalu menandai `dipotong` dan panjang aslinya, sehingga frontier tidak menganggap potongan
sebagai seluruh sumber.
"""
from __future__ import annotations

import re

from app.research.facts import locate_quote

REVISION = re.compile(r"\b(revisi|direvisi|merevisi|ralat|koreksi|dikoreksi|semula|sebelumnya|diubah|mengubah|"
                      r"perubahan|klarifikasi|meluruskan|pembaruan|correction|corrected|revised|clarifi\w*|updated?)\b",
                      re.I)
MONTHS = ("januari|februari|maret|april|mei|juni|juli|agustus|september|oktober|november|desember|january|"
          "february|march|may|june|july|august|october|december")
DATE = re.compile(rf"\b(\d{{1,2}}\s+({MONTHS})\s+\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}/\d{{4}})\b", re.I)
AMOUNT = re.compile(r"(\bRp\.?\s?\d[\d.,]*|\b\d[\d.,]*\s*(?:triliun|miliar|milyar|juta|persen|%|lembar|saham))", re.I)
SEPARATOR = " […] "


def _flat(text: str) -> str:
    return " ".join((text or "").split())


def _find(haystack: str, needle: str) -> int:
    needle = _flat(needle)
    if len(needle) < 8:
        return -1
    position = haystack.lower().find(needle.lower())
    if position >= 0:
        return position
    span = locate_quote(needle, haystack)
    return haystack.find(span) if span else -1


def excerpt(text: str, *, anchors=(), identity=(), max_chars: int = 2400, window: int = 320) -> dict:
    flat = _flat(text)
    if len(flat) <= max_chars:
        return {"teks": flat, "dipotong": False, "panjang_asli": len(flat)}
    candidates: list[tuple[int, int, int]] = []  # (prioritas, awal, akhir)
    for anchor in anchors:
        position = _find(flat, anchor or "")
        if position >= 0:
            candidates.append((0, position - window, position + len(_flat(anchor)) + window))
    candidates += [(1, match.start() - window, match.end() + window) for match in REVISION.finditer(flat)]
    candidates.append((2, 0, 400))
    candidates += [(3, match.start() - 150, match.end() + 150) for match in DATE.finditer(flat)]
    candidates += [(3, match.start() - 150, match.end() + 150) for match in AMOUNT.finditer(flat)]
    for name in identity:
        position = flat.lower().find((name or "").lower()) if name and len(name) >= 3 else -1
        if position >= 0:
            candidates.append((4, position - 150, position + len(name) + 150))
    candidates.append((5, len(flat) - 300, len(flat)))

    def merge(spans: list[list[int]]) -> list[list[int]]:
        merged: list[list[int]] = []
        for start, end in sorted(spans):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return merged

    def cost(spans: list[list[int]]) -> int:
        return sum(end - start for start, end in spans) + len(SEPARATOR) * max(0, len(spans) - 1)

    chosen: list[list[int]] = []
    for _priority, start, end in sorted(candidates, key=lambda item: (item[0], item[1])):
        start, end = max(0, start), min(len(flat), end)
        candidate = merge([*chosen, [start, end]])
        if cost(candidate) <= max_chars:
            chosen = candidate
            continue
        remaining = max_chars - cost(chosen) - len(SEPARATOR)
        if remaining >= 120:  # potong span ini di sekitar pusatnya, bukan dibuang seluruhnya
            center = (start + end) // 2
            clipped = merge([*chosen, [max(0, center - remaining // 2), min(len(flat), center + remaining // 2)]])
            if cost(clipped) <= max_chars:
                chosen = clipped
        break
    pieces = [flat[start:end].strip() for start, end in chosen]
    text_out = SEPARATOR.join(pieces)
    if chosen and chosen[0][0] > 0:
        text_out = "[…] " + text_out
    if chosen and chosen[-1][1] < len(flat):
        text_out += " […]"
    return {"teks": text_out, "dipotong": True, "panjang_asli": len(flat),
            "catatan": "Potongan di sekitar kutipan, kata koreksi/revisi, tanggal, dan angka; bukan seluruh artikel."}

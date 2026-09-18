"""Pemeriksaan mekanis klaim dan format angka laporan.

Hasil pemeriksaan di sini tidak bisa dibatalkan oleh persetujuan model mana pun: referensi yang tidak
ada, identitas atau tanggal yang salah, kutipan yang tidak ditemukan, dan angka yang tidak berasal
dari metrik yang dirujuk membuat klaim `unsupported`, berapa pun jumlah model yang setuju.
"""
from __future__ import annotations

import re
from datetime import date

from app.pipeline.gate import DENYLIST_TERMS
from app.research.facts import locate_quote

_DENY = [(term, re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)) for term in DENYLIST_TERMS]
NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)*")
# Angka yang menempel pada huruf adalah bagian nama (SMA20, RSI14, Q2), bukan angka yang diklaim.
# Kecualikan awalan mata uang: "Rp4,35 triliun" harus terbaca sebagai satu angka, bukan "35".
CURRENCY_PREFIX = ("rp",)
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def number_tokens(text: str) -> list[str]:
    tokens = []
    for match in NUMBER.finditer(text):
        before = text[:match.start()]
        if before[-1:].isalpha() and not before.lower().endswith(CURRENCY_PREFIX):
            continue
        tokens.append(match.group(0))
    return tokens


def denied_terms(text: str) -> list[str]:
    return sorted({term for term, pattern in _DENY if pattern.search(text or "")})


# ---- Format angka Indonesia ---------------------------------------------------------------------

def _decimal(value: float, digits: int) -> str:
    text = f"{abs(value):,.{digits}f}"
    return ("-" if value < 0 else "") + text.replace(",", "_").replace(".", ",").replace("_", ".")


def format_value(value: float | None, unit: str, signed: bool = False) -> str:
    if value is None:
        return "tidak tersedia"
    sign = "+" if signed and value > 0 else ""
    if unit == "ratio":
        return f"{sign}{_decimal(value * 100, 1)}%"
    if unit == "x":
        return f"{sign}{_decimal(value, 2)}x"
    if unit == "IDR":
        magnitude = abs(value)
        if magnitude >= 1e12:
            return f"Rp{_decimal(value / 1e12, 2)} triliun"
        if magnitude >= 1e9:
            return f"Rp{_decimal(value / 1e9, 1)} miliar"
        return f"Rp{_decimal(value, 0 if magnitude >= 100 else 2)}"
    return f"{sign}{_decimal(value, 1)}"


def display(metric: dict) -> str:
    signed = metric["unit"] == "ratio" and any(word in metric["metric_id"] for word in ("growth", "return", "_vs_"))
    return format_value(metric.get("value"), metric["unit"], signed)


# ---- Pencocokan angka ---------------------------------------------------------------------------

def _candidates(token: str) -> list[tuple[float, int]]:
    """Semua tafsiran wajar sebuah token angka, beserta jumlah digit desimalnya."""
    sign = -1.0 if token.startswith("-") else 1.0
    body = token.lstrip("+-")
    options: list[tuple[str, int]] = []
    if "," in body and "." in body:
        if body.rfind(",") > body.rfind("."):
            whole, frac = body.replace(".", "").split(",", 1) if body.count(",") == 1 else (body.replace(",", ""), "")
        else:
            whole, frac = body.replace(",", "").split(".", 1) if body.count(".") == 1 else (body.replace(".", ""), "")
        options.append((f"{whole}.{frac}" if frac else whole, len(frac)))
    elif "," in body or "." in body:
        separator = "," if "," in body else "."
        parts = body.split(separator)
        if len(parts) == 2:
            options.append((f"{parts[0]}.{parts[1]}", len(parts[1])))
        if all(len(part) == 3 for part in parts[1:]):
            options.append(("".join(parts), 0))
    else:
        options.append((body, 0))
    results = []
    for text, digits in options:
        try:
            results.append((sign * float(text), digits))
        except ValueError:
            continue
    return results


def _targets(metric: dict) -> list[float]:
    value = metric.get("value")
    if value is None or metric.get("status") != "ok":
        return []
    unit = metric.get("unit")
    if unit == "ratio":
        return [value * 100, value]
    if unit == "IDR":
        return [value, value / 1e6, value / 1e9, value / 1e12]
    return [value]


def unexplained_numbers(statement: str, metrics: list[dict], allowed_text: str = "") -> list[str]:
    """Angka dalam kalimat yang tidak berasal dari metrik yang dirujuk.

    Angka yang tertulis literal di nama/periode/formula metrik, di sumber (tanggal, kutipan), atau
    tahun kalender boleh muncul. Sisanya harus sama dengan nilai metrik setelah pembulatan yang
    ditunjukkan oleh jumlah digit desimal di kalimat.
    """
    literal = {token.lstrip("+-") for token in number_tokens(allowed_text)}
    literal |= {token for text in (allowed_text,) for token in ISO_DATE.findall(text)}
    text = ISO_DATE.sub(lambda match: " " if match.group(0) in literal else match.group(0).replace("-", " "), statement)
    targets = [target for metric in metrics for target in _targets(metric)]
    unexplained = []
    for token in number_tokens(text):
        bare = token.lstrip("+-")
        if bare in literal:
            continue
        if re.fullmatch(r"(19|20)\d\d", bare):
            continue
        matched = False
        for candidate, digits in _candidates(token):
            tolerance = 0.5 * 10 ** (-digits) + 1e-9
            if any(abs(abs(candidate) - abs(target)) <= tolerance for target in targets):
                matched = True
                break
        if not matched:
            unexplained.append(token)
    return unexplained


# ---- Pemeriksaan klaim --------------------------------------------------------------------------

def with_input_metrics(metric_ids, metrics: dict[str, dict]) -> list[dict]:
    """Metrik yang dirujuk beserta metrik masukannya.

    Metrik turunan sudah mendeklarasikan `input_metric_ids`, jadi menyebut angka pembilang atau
    pembaginya tetap bisa dilacak ke perhitungan yang sama — bukan angka baru.
    """
    collected = []
    for ref in metric_ids:
        metric = metrics.get(ref)
        if metric is None:
            continue
        for candidate in [metric, *(metrics[parent] for parent in metric.get("input_metric_ids") or []
                                    if parent in metrics)]:
            if candidate not in collected:
                collected.append(candidate)
    return collected


def _available(source: dict) -> date | None:
    value = source.get("available_at")
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def check_claim(claim: dict, metrics: dict[str, dict], sources: dict[str, dict], source_texts: dict[str, str],
                ticker: str, as_of: date) -> list[str]:
    """Daftar masalah mekanis; kosong berarti lolos pemeriksaan kode."""
    issues = []
    metric_refs = claim.get("metric_ids") or []
    source_refs = claim.get("source_ids") or []
    unknown_metrics = [ref for ref in metric_refs if ref not in metrics]
    unknown_sources = [ref for ref in source_refs if ref not in sources]
    if unknown_metrics:
        issues.append(f"Metrik tidak ditemukan: {', '.join(unknown_metrics)}.")
    if unknown_sources:
        issues.append(f"Sumber tidak ditemukan: {', '.join(unknown_sources)}.")
    if not metric_refs and not source_refs:
        issues.append("Klaim tidak merujuk metrik atau sumber apa pun.")
    if claim.get("domain") in {"fundamental", "valuation", "technical"} and claim.get("kind") != "observation" and not metric_refs:
        issues.append("Klaim perhitungan/interpretasi wajib merujuk metrik.")

    referenced_sources = [sources[ref] for ref in source_refs if ref in sources]
    for metric in (metrics[ref] for ref in metric_refs if ref in metrics):
        referenced_sources += [sources[ref] for ref in metric.get("source_ids", []) if ref in sources]
    for source in referenced_sources:
        if source.get("status") not in {"ok", "empty"}:
            issues.append(f"Sumber {source['source_id']} berstatus {source.get('status')}.")
        if source.get("ticker") and source["ticker"] != ticker:
            issues.append(f"Sumber {source['source_id']} milik {source['ticker']}, bukan {ticker}.")
        available = _available(source)
        if available and available > as_of:
            issues.append(f"Sumber {source['source_id']} tersedia {available.isoformat()}, setelah tanggal acuan.")

    quote = (claim.get("quote") or "").strip()
    if quote:
        texts = [source_texts.get(ref, "") for ref in source_refs]
        if not any(locate_quote(quote, text) for text in texts if text):
            issues.append("Kutipan tidak ditemukan pada sumber yang dirujuk.")
    elif claim.get("domain") == "news" and claim.get("attribution") in {"document", "company_statement"}:
        issues.append("Fakta berita wajib menyertakan kutipan dari sumber.")

    referenced_metrics = with_input_metrics(metric_refs, metrics)
    allowed = " ".join([*(f"{m['name']} {m['period']} {m['formula']} {m.get('price_basis') or ''}" for m in referenced_metrics),
                        *(f"{s.get('period') or ''} {s.get('available_at') or ''}" for s in referenced_sources),
                        quote, " ".join(source_texts.get(ref, "") for ref in source_refs)])
    numbers = unexplained_numbers(claim.get("statement", ""), referenced_metrics, allowed)
    if numbers:
        issues.append(f"Angka tanpa dasar metrik/sumber: {', '.join(numbers)}.")
    terms = denied_terms(" ".join([claim.get("statement", ""), *claim.get("limitations", []), *claim.get("assumptions", [])]))
    if terms:
        issues.append(f"Bahasa transaksi/penilaian tidak diizinkan: {', '.join(terms)}.")
    return issues

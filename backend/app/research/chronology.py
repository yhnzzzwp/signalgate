"""Kronologi angka aksi korporasi: rencana, revisi, persetujuan, dan realisasi.

Masalah produk (contoh kasus HATM): beberapa artikel bertanggal berbeda menyebut angka private placement
yang berbeda. Memilih angka dari artikel terbaru itu salah -- artikel baru bisa mengutip rencana lama,
dan dua angka bisa milik dua aksi yang berbeda. Aturan di sini:

- Tanggal publikasi sumber (metadata) dipisah dari tanggal kejadian. Tanggal kejadian hanya dipakai bila
  tertulis di kutipan/teks sumber; tanggal kiriman model yang tidak ditemukan disimpan sebagai klaim saja.
- Aksi dipisah per jenis dan per identitas yang tertulis di bukti (`action_ref`). Identitas berbeda =
  aksi berbeda, tidak pernah digabung. Tanpa identitas, entri sejenis hanya dikelompokkan sebagai
  kandidat aksi yang sama dan hubungannya ditandai belum terbukti.
- Revisi hanya dihubungkan bila buktinya ada: kutipan revisi memuat kata revisi DAN angka lama yang
  direvisinya. Selain itu, angka berbeda untuk metrik yang sama adalah konflik: ditampilkan semua
  beserta sumber dan tanggalnya, nilai berlaku dibiarkan kosong.
- Sejarah angka tidak pernah dibuang.

Modul ini murni Python (tanpa model, tanpa jaringan). Tahap (stage) ditentukan dari kata kunci pada
kutipan -- heuristik yang dicatat sebagai `stage_basis`, bukan fakta.
"""
from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field

STAGES = ("plan", "revision", "approval", "realization", "unknown")
# Urutan penting: kalimat revisi sering juga memuat "rencana".
STAGE_PATTERNS = (
    ("revision", re.compile(r"\b(revisi|direvisi|merevisi|diubah|mengubah|perubahan (?:jumlah|nilai|rencana)|semula|"
                            r"revised?|amended)\b", re.I)),
    ("realization", re.compile(r"\b(telah (?:menerbitkan|menyelesaikan|merealisasikan|melaksanakan|mencatatkan)|"
                               r"realisasi|rampung|hasil pelaksanaan|dicatatkan|completed|realized)\b", re.I)),
    ("approval", re.compile(r"\b(disetujui|menyetujui|persetujuan|rupslb|rups|restu|approved|approval)\b", re.I)),
    ("plan", re.compile(r"\b(rencana|berencana|akan|mengajukan|usulan|menargetkan|plans?|proposed)\b", re.I)),
)
TERM_STAGES = {"plan", "revision", "approval", "unknown"}

AMOUNT = re.compile(
    r"(?P<rp>\bRp\.?\s?|\bIDR\s?)?(?P<num>\d+(?:[.,]\d+)*)\s*"
    r"(?P<scale>triliun|miliar|milyar|juta|ribu|billion|million|trillion)?\s*"
    r"(?P<unit>%|persen|lembar saham|saham|lembar|shares)?"
    r"(?P<per>\s*(?:per|/)\s*(?:lembar\s*)?saham)?",
    re.I)
SCALES = {"ribu": 1e3, "juta": 1e6, "million": 1e6, "miliar": 1e9, "milyar": 1e9, "billion": 1e9,
          "triliun": 1e12, "trillion": 1e12}
UNITS = {"shares": "saham", "value_idr": "IDR", "percentage": "%", "price_idr": "IDR/saham"}
MONTHS_ID = ("januari", "februari", "maret", "april", "mei", "juni", "juli", "agustus", "september", "oktober",
             "november", "desember")
MONTHS_EN = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
             "november", "december")


class TimelineEntry(BaseModel):
    entry_id: str
    action_type: str
    action_ref: str | None = None
    stage: str
    stage_basis: str
    metric: str
    value: float
    unit: str
    value_text: str
    quote: str
    source_id: str
    published_at: str | None = None
    event_date: str | None = None
    event_date_claimed: str | None = None
    claim_id: str | None = None
    origin: str  # local_claim | frontier
    value_basis: str = "kutipan"
    # Angka lama yang disebut di kutipan revisi yang sama ("semula X menjadi Y" -> X).
    previous_values: list[float] = Field(default_factory=list)
    # Sumber yang menurut pembaca frontier direvisi entri ini; dipakai hanya bila teks sumber memuat angka lamanya.
    revises_source_hint: str | None = None
    revises_entry_ids: list[str] = Field(default_factory=list)


def parse_number(text: str) -> float | None:
    """Angka gaya Indonesia atau Inggris. "7,37" -> 7.37, "1.500" -> 1500, "1,500.25" -> 1500.25."""
    body = text.strip()
    if "," in body and "." in body:
        decimal = "," if body.rfind(",") > body.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        body = body.replace(thousands, "").replace(decimal, ".")
    elif "," in body or "." in body:
        separator = "," if "," in body else "."
        parts = body.split(separator)
        if len(parts) > 2 or (len(parts[-1]) == 3 and len(parts) == 2 and separator == "."):
            body = "".join(parts)  # pemisah ribuan
        elif len(parts[-1]) == 3 and separator == "," and len(parts[0]) <= 3 and len(parts) == 2:
            body = "".join(parts)  # "1,500" gaya Inggris
        else:
            body = f"{parts[0]}.{parts[1]}"
    try:
        return float(body)
    except ValueError:
        return None


def amounts(text: str) -> list[dict]:
    """Angka aksi korporasi yang satuannya jelas. Angka tanpa satuan (tahun, nomor) diabaikan."""
    found = []
    for match in AMOUNT.finditer(text or ""):
        number = parse_number(match.group("num"))
        if number is None:
            continue
        scale = SCALES.get((match.group("scale") or "").lower(), 1.0)
        unit = (match.group("unit") or "").lower()
        if unit in {"%", "persen"}:
            metric, value = "percentage", number
            if value > 100 and ("," in match.group("num") or "." in match.group("num")):
                # "7,375%" gaya Indonesia: pemisahnya desimal, bukan ribuan.
                head, _, tail = re.split(r"([.,])", match.group("num"), maxsplit=1)
                value = float(f"{head}.{re.sub(r'[.,]', '', tail)}")
        elif match.group("rp"):
            metric = "price_idr" if match.group("per") else "value_idr"
            value = number * scale
        elif unit in {"saham", "lembar", "lembar saham", "shares"}:
            metric, value = "shares", number * scale
        else:
            continue
        found.append({"metric": metric, "value": value, "value_text": match.group(0).strip()})
    return found


def stage_of(text: str) -> tuple[str, str]:
    for stage, pattern in STAGE_PATTERNS:
        hit = pattern.search(text or "")
        if hit:
            return stage, f"kata kunci '{hit.group(0)}' pada kutipan"
    return "unknown", "tidak ada kata kunci tahap"


def _date_forms(day: date) -> list[str]:
    forms = [day.isoformat(), f"{day.day} {MONTHS_ID[day.month - 1]} {day.year}",
             f"{day.day:02d} {MONTHS_ID[day.month - 1]} {day.year}", f"{day.day} {MONTHS_EN[day.month - 1]} {day.year}",
             f"{MONTHS_EN[day.month - 1]} {day.day}, {day.year}", f"{day.day}/{day.month}/{day.year}",
             f"{day.day:02d}/{day.month:02d}/{day.year}"]
    return [form.lower() for form in forms]


def verified_event_date(claimed: str | None, *texts: str) -> str | None:
    """Tanggal kejadian dipakai hanya bila tertulis di kutipan atau teks sumber."""
    if not claimed:
        return None
    try:
        day = date.fromisoformat(claimed[:10])
    except ValueError:
        return None
    haystack = " ".join(" ".join((text or "").split()) for text in texts).lower()
    return day.isoformat() if any(form in haystack for form in _date_forms(day)) else None


def _order_key(entry: TimelineEntry) -> tuple[str, str]:
    return (entry.event_date or (entry.published_at or "")[:10] or "9999-12-31", entry.entry_id)


def _same_value(first: float, second: float) -> bool:
    return abs(first - second) <= max(1e-9, abs(first) * 1e-6)


def link_revisions(entries: list[TimelineEntry], source_texts: dict[str, str]) -> None:
    """Hubungkan revisi hanya dengan bukti: entri revisi yang kutipannya (atau teks sumbernya, bila
    model menunjuk sumber yang direvisi) memuat angka lama untuk metrik yang sama."""
    by_id = {entry.entry_id: entry for entry in entries}
    for entry in entries:
        if entry.stage != "revision":
            continue
        hinted_values = []
        if entry.revises_source_hint:
            hinted_values = [item["value"] for item in amounts(source_texts.get(entry.source_id, ""))
                             if item["metric"] == entry.metric]
        for other in entries:
            if other is entry or other.metric != entry.metric or _same_value(other.value, entry.value):
                continue
            in_quote = any(_same_value(value, other.value) for value in entry.previous_values)
            in_hinted_source = (other.source_id == entry.revises_source_hint
                                and any(_same_value(value, other.value) for value in hinted_values))
            if (in_quote or in_hinted_source) and other.entry_id not in entry.revises_entry_ids:
                entry.revises_entry_ids.append(other.entry_id)
    # Rujukan yang tidak ada dibuang: tautan hanya ke entri yang benar-benar ada.
    for entry in entries:
        entry.revises_entry_ids = [ref for ref in entry.revises_entry_ids if ref in by_id]


def _group_key(entry: TimelineEntry) -> tuple[str, str]:
    return entry.action_type, (entry.action_ref or "").strip().lower()


def _resolve(values: list[TimelineEntry]) -> tuple[float | None, list[str], str]:
    """(nilai berlaku, id konflik, penjelasan) untuk satu metrik pada satu kelas tahap."""
    distinct = []
    for entry in values:
        if not any(_same_value(entry.value, item.value) for item in distinct):
            distinct.append(entry)
    if len(distinct) <= 1:
        return (distinct[0].value if distinct else None), [], "semua sumber menyebut angka yang sama"
    if len({(entry.source_id, entry.quote) for entry in values}) == 1:
        # Beberapa angka dalam SATU kutipan (mis. sebelum/sesudah): bukan pertentangan antarsumber.
        return None, [], "beberapa angka dalam satu kutipan; nilai berlaku tidak dipilih otomatis"
    revised = {ref for entry in values for ref in entry.revises_entry_ids}
    heads = [entry for entry in values if entry.entry_id not in revised]
    head_values = []
    for entry in heads:
        if not any(_same_value(entry.value, value) for value in head_values):
            head_values.append(entry.value)
    if len(head_values) == 1:
        return head_values[0], [], "angka lama direvisi dengan bukti revisi di kutipan"
    return None, [entry.entry_id for entry in values], ("angka berbeda antarsumber tanpa bukti revisi; "
                                                        "angka terbaru tidak dipilih otomatis")


def build_chronology(entries: list[TimelineEntry], source_texts: dict[str, str] | None = None) -> list[dict]:
    link_revisions(entries, source_texts or {})
    groups: dict[tuple[str, str], list[TimelineEntry]] = {}
    for entry in entries:
        groups.setdefault(_group_key(entry), []).append(entry)
    actions = []
    for (action_type, ref), members in sorted(groups.items()):
        members = sorted(members, key=_order_key)
        metrics, conflicts = {}, []
        for metric in sorted({entry.metric for entry in members}):
            rows = [entry for entry in members if entry.metric == metric]
            terms = [entry for entry in rows if entry.stage in TERM_STAGES]
            realized = [entry for entry in rows if entry.stage == "realization"]
            term_value, term_conflict, term_note = _resolve(terms)
            real_value, real_conflict, real_note = _resolve(realized)
            metrics[metric] = {"unit": UNITS.get(metric, metric), "terms_value": term_value, "terms_note": term_note,
                               "realized_value": real_value, "realized_note": real_note,
                               "history": [entry.entry_id for entry in rows]}
            for kind, ids, note in (("ketentuan", term_conflict, term_note), ("realisasi", real_conflict, real_note)):
                if ids:
                    conflicts.append({"metric": metric, "stage_class": kind, "entry_ids": ids, "note": note})
        sources = sorted({entry.source_id for entry in members})
        identity = "action_ref" if ref else ("single_source" if len(sources) == 1 else "unproven")
        actions.append({
            "action_key": f"{action_type}:{ref or '-'}", "action_type": action_type, "action_ref": ref or None,
            "identity": identity,
            "identity_note": ("identitas aksi tertulis di bukti" if ref else
                              "satu sumber" if len(sources) == 1 else
                              "beberapa sumber sejenis tanpa identitas aksi yang sama di bukti; bisa jadi aksi berbeda"),
            "entries": [entry.model_dump() for entry in members], "metrics": metrics, "conflicts": conflicts,
            "claim_ids": sorted({entry.claim_id for entry in members if entry.claim_id}),
            "source_ids": sources,
        })
    return actions


def conflict_claim_ids(actions: list[dict]) -> set[str]:
    """Klaim yang angka-nya terlibat konflik lintas waktu yang belum terselesaikan."""
    claim_ids = set()
    for action in actions:
        by_entry = {entry["entry_id"]: entry for entry in action["entries"]}
        for conflict in action["conflicts"]:
            claim_ids |= {by_entry[item]["claim_id"] for item in conflict["entry_ids"]
                          if item in by_entry and by_entry[item].get("claim_id")}
    return claim_ids


def conflict_messages(actions: list[dict]) -> list[str]:
    messages = []
    for action in actions:
        by_entry = {entry["entry_id"]: entry for entry in action["entries"]}
        for conflict in action["conflicts"]:
            parts = []
            for entry_id in conflict["entry_ids"]:
                entry = by_entry.get(entry_id)
                if entry:
                    when = entry.get("event_date") or f"terbit {(entry.get('published_at') or '?')[:10]}"
                    parts.append(f"{entry['value_text']} [{entry['source_id']}, {when}, tahap {entry['stage']}]")
            messages.append(f"Kronologi {action['action_type'].replace('_', ' ')} ({conflict['metric']}, "
                            f"{conflict['stage_class']}): {'; '.join(parts)} — {conflict['note']}.")
    return messages


def _flat(text: str) -> str:
    return " ".join((text or "").split()).lower()


def assign_refs(entries: list[TimelineEntry], refs: list[str], source_texts: dict[str, str]) -> None:
    """Identitas aksi hanya dari bukti: entri diberi `action_ref` bila teks sumbernya memuat tepat satu
    identitas terverifikasi (mis. nomor surat). Tanpa itu identitasnya kosong (belum terbukti)."""
    verified = [ref for ref in dict.fromkeys(ref.strip() for ref in refs if ref and len(ref.strip()) >= 4)]
    for entry in entries:
        text = _flat(source_texts.get(entry.source_id, ""))
        matches = [ref for ref in verified if _flat(ref) in text]
        entry.action_ref = matches[0] if len(matches) == 1 else None


def entries_from_text(*, prefix: str, action_type: str, action_ref: str | None, quote: str, statement: str,
                      source_id: str, source_text: str, published_at: str | None, event_date_claimed: str | None,
                      claim_id: str | None, origin: str, stage_hint: str | None = None,
                      revises_source_hint: str | None = None) -> list[TimelineEntry]:
    """Entri dari satu klaim/temuan yang sudah lolos pemeriksaan kutipan.

    Angka diambil dari kutipan (kata demi kata di sumber). Bila kutipan tidak memuat angka, angka pada
    kalimat analis dipakai HANYA bila tulisan angka itu ditemukan apa adanya di teks sumber. Pada kutipan
    revisi ("semula X menjadi Y"), angka terakhir per metrik adalah angka baru; angka sebelumnya dicatat
    sebagai angka lama yang direvisi, bukan entri tersendiri.
    """
    normalized_source = " ".join((source_text or "").split()).lower()
    found = [dict(item, basis="kutipan") for item in amounts(quote)]
    if not found:
        found = [dict(item, basis="kalimat analis; tulisan angka ditemukan di teks sumber")
                 for item in amounts(statement) if " ".join(item["value_text"].split()).lower() in normalized_source]
    stage, basis = stage_of(quote or statement)
    if stage_hint in STAGES and stage == "unknown":
        stage, basis = stage_hint, "tahap dari pembaca frontier; kata kunci tidak ditemukan"
    previous: dict[str, list[float]] = {}
    if stage == "revision":
        latest: dict[str, dict] = {}
        for item in found:
            if item["metric"] in latest:
                previous.setdefault(item["metric"], []).append(latest[item["metric"]]["value"])
            latest[item["metric"]] = item
        found = list(latest.values())
    event_date = verified_event_date(event_date_claimed, quote, source_text)
    entries = []
    for index, item in enumerate(found, start=1):
        entries.append(TimelineEntry(
            entry_id=f"{prefix}:{index}", action_type=action_type, action_ref=action_ref or None, stage=stage,
            stage_basis=basis, metric=item["metric"], value=item["value"], unit=UNITS[item["metric"]],
            value_text=item["value_text"], quote=quote or statement, source_id=source_id, published_at=published_at,
            event_date=event_date, event_date_claimed=None if event_date else (event_date_claimed or None),
            claim_id=claim_id, origin=origin, value_basis=item["basis"],
            previous_values=previous.get(item["metric"], []), revises_source_hint=revises_source_hint or None))
    return entries

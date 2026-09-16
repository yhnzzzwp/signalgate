from __future__ import annotations

import re

from app.research.evidence import EvidenceStore
from app.research.models import Evidence, Extraction, Fact, Quoted, Validation

MIN_QUOTE_CHARS = 12
FLAG_TOPICS = ("business_change", "old_business_divested", "asset_injection")
ENTITY_RELATIONS = {"existing_shareholder", "affiliate", "new_party", "creditor"}
LEGAL_TOKENS = {"pt", "tbk", "persero"}
GENERIC_PARTY_WORDS = {
    "pemegang", "saham", "lama", "baru", "utama", "pengendali", "investor", "strategis", "publik", "masyarakat",
    "kreditur", "kreditor", "sindikasi", "asing", "luar", "negeri", "perseroan", "perusahaan", "emiten", "pihak",
    "terafiliasi", "afiliasi", "entitas", "anak", "usaha", "bank", "existing", "shareholder", "shareholders",
    "affiliate", "new", "party", "creditor", "creditors", "public", "unknown",
}


def flatten(text: str) -> str:
    return " ".join(text.split())


def simplify(text: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return " ".join(token for token in tokens if token not in LEGAL_TOKENS)


def locate_quote(quote: str, source: str) -> str | None:
    needle = flatten(quote).lower()
    if len(needle) < MIN_QUOTE_CHARS:
        return None
    original = flatten(source)
    haystack = original.lower() if len(original.lower()) == len(original) else original
    position = haystack.find(needle)
    if position >= 0:
        return original[position:position + len(needle)]
    # Permit punctuation differences only. Fuzzy substrings can drop "tidak" or
    # change a number while retaining 85% of a sentence and reversing its meaning.
    tokens = list(re.finditer(r"\w+", original.lower()))
    wanted = re.findall(r"\w+", needle)
    for start in range(len(tokens) - len(wanted) + 1):
        if wanted and [token.group() for token in tokens[start:start + len(wanted)]] == wanted:
            return original[tokens[start].start():tokens[start + len(wanted) - 1].end()]
    return None


def evidence_item(store: EvidenceStore, evidence_id: str) -> Evidence | None:
    item = next((entry for entry in store.items if entry.id == evidence_id), None)
    return None if item is None or item.kind == "input" else item


def party_name_issue(name: str, relation: str, source: str, issuer_names=()) -> str | None:
    if relation not in ENTITY_RELATIONS:
        return None
    simplified = simplify(name)
    if re.match(r"^(pemegang saham|pengendali|investor|kreditur|kreditor|perseroan)\b", simplified):
        return f"nama pihak '{name}' terlalu umum, memakai deskripsi peran"
    if not simplified or all(token in GENERIC_PARTY_WORDS for token in simplified.split()):
        return f"nama pihak '{name}' terlalu umum, bukan badan atau orang spesifik"
    if simplified not in simplify(source):
        return f"nama pihak '{name}' tidak ada di sumber"
    if simplified in {simplify(issuer) for issuer in issuer_names if issuer}:
        return f"nama pihak '{name}' adalah emiten sendiri, bukan penerima eksternal"
    return None


def asserted_facts(extraction: Extraction) -> list[tuple[str, str, str, Quoted]]:
    asserted: list[tuple[str, str, str, Quoted]] = [
        ("counterparty", party.relation, party.name, party)
        for party in extraction.counterparties
        if party.relation != "unknown"
    ]
    asserted += [
        ("use_of_funds", use.category, "", use)
        for use in extraction.use_of_funds
        if use.category != "unknown"
    ]
    asserted += [
        (topic, "present", "", getattr(extraction, topic))
        for topic in FLAG_TOPICS
        if getattr(extraction, topic).present
    ]
    return asserted


def verified_facts(extraction: Extraction, store: EvidenceStore, issuer_names=()) -> tuple[list[Fact], list[str]]:
    facts: list[Fact] = []
    issues: list[str] = []
    for topic, value, claim, source in asserted_facts(extraction):
        item = evidence_item(store, source.evidence_id)
        span = locate_quote(source.quote, item.text) if item else None
        evidence_id = source.evidence_id
        if span is None:
            # Long PDF contexts: a verbatim quote is sometimes cited to the wrong page. Re-home it to the page
            # that contains it; the original id stays in the stored extraction for audit.
            for other in store.items:
                if other.kind != "input" and other.id != source.evidence_id:
                    span = locate_quote(source.quote, other.text)
                    if span is not None:
                        evidence_id = other.id
                        break
        if span is None:
            issues.append(f"{topic}={value} diabaikan: kutipan tidak ditemukan di {source.evidence_id or 'bukti'}.")
            continue
        if topic == "counterparty":
            problem = party_name_issue(claim, value, span, issuer_names)
            if problem:
                issues.append(f"{topic}={value} diabaikan: {problem}.")
                continue
        facts.append(Fact(id=f"F{len(facts) + 1:02}", topic=topic, value=value, claim=claim,
                          quote=span, evidence_id=evidence_id))
    return facts, issues


def apply_validation(facts: list[Fact], validation: Validation, strict: bool) -> tuple[list[Fact], list[str]]:
    ids = [check.fact_id for check in validation.checks]
    if sorted(ids) != sorted(fact.id for fact in facts):
        return [], ["Validator tidak memeriksa setiap fakta tepat satu kali."]
    checks = {check.fact_id: check for check in validation.checks}
    kept: list[Fact] = []
    issues: list[str] = []
    for fact in facts:
        check = checks.get(fact.id)
        status = check.status if check else "not_supported"
        reason = check.reason if check else "tidak diperiksa validator"
        if status == "supported":
            kept.append(fact.model_copy(update={"validator_status": "supported"}))
        elif status == "contradicted" or strict:
            issues.append(f"{fact.id} dibuang ({status}): {reason}")
        else:
            kept.append(fact.model_copy(update={"validator_status": "not_supported"}))
            issues.append(f"{fact.id} belum didukung validator: {reason}")
    return kept, issues + list(validation.issues)


def party_key(name: str) -> str:
    # "PT Multi Sarana Nasional (MSN)" and "PT Multi Sarana Nasional" name the same party.
    return simplify(re.sub(r"\([^)]*\)", " ", name))


def quotes_overlap(first: str, second: str) -> bool:
    first, second = flatten(first).lower(), flatten(second).lower()
    return first in second or second in first


def compare_independent_facts(facts: list[Fact], independent: list[Fact]) -> Validation:
    """The second model never sees the first model's labels, facts or proposed values."""
    checks = []
    for fact in facts:
        # A party is identified by name across pages; uses of funds and flags stay tied to their page.
        same_subject = [other for other in independent
                        if other.topic == fact.topic
                        and (party_key(other.claim) == party_key(fact.claim) if fact.topic == "counterparty" else
                             other.evidence_id == fact.evidence_id and
                             (quotes_overlap(other.quote, fact.quote) if fact.topic == "use_of_funds" else True))]
        matches = [other for other in same_subject if other.value == fact.value]
        status = "supported" if matches else "not_supported"
        # Different classifications for the same quote are a concrete disagreement, unless the reader
        # also split that sentence into this same classification.
        if not matches and any(other.value != fact.value and quotes_overlap(other.quote, fact.quote)
                               for other in same_subject):
            status = "contradicted"
        checks.append({"fact_id": fact.id, "status": status,
                       "reason": "Perbandingan ekstraksi independen dari sumber yang sama."})
    return Validation(checks=checks, agrees_with_label=True, issues=[])

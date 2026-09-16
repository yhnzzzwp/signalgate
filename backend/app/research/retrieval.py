"""Deterministic retrieval over saved evidence; no embedding API or model needed."""
TOPICS = ('penggunaan dana', 'rencana penggunaan', 'kegiatan usaha', 'risiko', 'pemegang saham',
          'pembeli siaga', 'dilusi', 'inbreng', 'pengendali', 'penilaian', 'modal kerja')


def retrieve(items, terms, per_item=3000, total_chars=16000):
    chunks = []
    for item in items:
        # Chunk pages rather than dropping the end of a long prospectus.
        text = item.text
        for start in range(0, len(text), max(300, per_item - 200)):
            part = text[start:start + per_item]
            lower = part.lower()
            score = sum(3 for term in TOPICS if term in lower) + sum(1 for term in terms if term.lower() in lower)
            if item.kind == 'pdf':
                score += 2
            if item.kind == 'input':
                score = -1
            chunks.append((score, item, start, part))
    ranked = sorted(chunks, key=lambda chunk: (-chunk[0], chunk[1].id, chunk[2]))
    # Include topic diversity when one document repeats the same phrase on many pages.
    # Reserve company context before document chunks when production uses Sectors.
    chosen = [c for c in ranked if c[1].kind == 'sectors_api'][:1]
    keys = {(c[1].id, c[2]) for c in chosen}
    for topic in TOPICS:
        candidate = next((c for c in ranked if topic in c[3].lower() and (c[1].id, c[2]) not in keys), None)
        if candidate:
            chosen.append(candidate)
            keys.add((candidate[1].id, candidate[2]))
    chosen += [c for c in ranked if (c[1].id, c[2]) not in keys]
    result, used = [], 0
    for _, item, start, text in chosen:
        remaining = total_chars - used
        if remaining < 200:
            break
        text = text[:remaining]
        result.append({'id':item.id, 'kind':item.kind, 'url':item.url, 'title':item.title,
                       'page_number':item.page_number, 'offset':start, 'text':text})
        used += len(text)
    return result

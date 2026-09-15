from __future__ import annotations

from app.pipeline.schema import ActionBucket

CONTROL_CHANGE_KEYWORDS = [
    "perubahan pengendali", "perubahan pengendalian", "perubahan bidang usaha",
    "pengambilalihan", "mengambil alih", "akuisisi", "diakuisisi",
    "merger", "konsolidasi", "acquisition", "takeover", "change of control",
]

NON_PREEMPTIVE_KEYWORDS = [
    "pmthmetd", "tanpa hak memesan efek terlebih dahulu",
    "private placement", "penempatan terbatas", "penambahan modal tanpa",
]

RIGHTS_ISSUE_KEYWORDS = [
    "pmhmetd", "hak memesan efek terlebih dahulu", "hmetd",
    "rights issue", "right issue",
]

GENERAL_ACTION_KEYWORDS = [
    "ekspansi", "perluasan usaha", "pabrik baru", "investasi baru",
    "kerja sama strategis", "kerjasama strategis", "joint venture",
    "buyback", "buy back", "pembelian kembali saham", "tender offer",
    "divestasi", "kontrak baru", "penjualan aset", "restrukturisasi utang",
    "stock split", "dividen",
]

_BUCKET_KEYWORDS: list[tuple[ActionBucket, list[str]]] = [
    (ActionBucket.control_change, CONTROL_CHANGE_KEYWORDS),
    (ActionBucket.non_preemptive_capital, NON_PREEMPTIVE_KEYWORDS),
    (ActionBucket.rights_issue, RIGHTS_ISSUE_KEYWORDS),
    (ActionBucket.general_action, GENERAL_ACTION_KEYWORDS),
]


def classify(title: str, body: str) -> tuple[ActionBucket | None, list[str]]:
    text = f"{title or ''} {body or ''}".lower()
    matched: list[str] = []
    top_bucket: ActionBucket | None = None
    for bucket, keywords in _BUCKET_KEYWORDS:
        hits = [keyword for keyword in keywords if keyword in text]
        if hits:
            matched.extend(hits)
            if top_bucket is None:
                top_bucket = bucket
    return top_bucket, matched

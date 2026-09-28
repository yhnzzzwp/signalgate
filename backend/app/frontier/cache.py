"""Cache respons frontier untuk replay tanpa jaringan dan tanpa biaya baru.

Kunci cache mencakup semua yang mengubah jawaban: provider, model, versi prompt dan schema, pengaturan
thinking/effort/max_tokens, hash bukti, klaim, pendapat lokal (langkah kedua), dan hash prompt utuh.
Bukti berubah = kunci berubah, jadi hasil basi tidak pernah dipakai ulang. Yang disimpan hanya jawaban
akhir (`content`) beserta usage; jejak penalaran tidak pernah disimpan.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


class FrontierCache:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory) / "cache"

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None  # cache rusak dianggap miss, tidak pernah menghentikan run
        return stored if isinstance(stored, dict) and stored.get("key") == key else None

    def put(self, key: str, record: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({**record, "key": key}, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(path)

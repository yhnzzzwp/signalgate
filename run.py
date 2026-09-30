#!/usr/bin/env python3
"""Start SignalGate from any working directory, without activating a shell environment."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def interpreter(root: Path = ROOT, windows: bool | None = None) -> Path:
    windows = os.name == "nt" if windows is None else windows
    return root / "backend" / ".venv" / ("Scripts/python.exe" if windows else "bin/python")


def main() -> int:
    python = interpreter()
    if not python.is_file():
        print("Environment belum tersedia. Jalankan setup: python scripts/setup.py", file=sys.stderr)
        return 1
    try:
        return subprocess.run([str(python), "-m", "app.local", *sys.argv[1:]], cwd=ROOT / "backend").returncode
    except KeyboardInterrupt:
        return 130
    except OSError as error:
        print(f"Gagal menjalankan environment: {error}. Ulangi setup --recreate-venv.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

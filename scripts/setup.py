#!/usr/bin/env python3
r"""Siapkan SignalGate di mesin baru: venv, dependensi, model Ollama, dan .env mode hemat.

    macOS/Linux :  python3 scripts/setup.py --profile workstation
    Windows     :  scripts\setup.cmd --profile workstation

Perintah `python3` tidak ada di Windows; yang ada `py` dan `python`. Wrapper setup.cmd memilihkannya.

Dijalankan dengan Python sistem (bukan dari dalam venv) dan hanya memakai pustaka standar, supaya
bisa jalan di macOS, Linux, dan Windows tanpa dipasang apa pun lebih dulu.

Mode hemat: .env yang dibuat menyetel SECTORS_API_ENABLED=false dan membiarkan SECTORS_API_KEY
kosong, jadi tidak ada satu pun kredit Sectors terpakai sampai kamu sendiri yang mengisinya.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
VENV = BACKEND / ".venv"
MIN_PYTHON = (3, 11)  # app/pipeline/schema.py memakai StrEnum

OK, WARN, FAIL = "  ok  ", " catat", " gagal"


def say(tag: str, message: str) -> None:
    print(f"[{tag}] {message}", flush=True)


def die(message: str, hint: str = "") -> None:
    say(FAIL, message)
    if hint:
        print(f"\n{hint}\n")
    sys.exit(1)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(command: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, check=check, text=True)


def capture(command: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else ""


# --------------------------------------------------------------------------- langkah


def step_python() -> None:
    if sys.version_info < MIN_PYTHON:
        die(f"Python {'.'.join(map(str, MIN_PYTHON))}+ dibutuhkan, terpasang {sys.version.split()[0]}.",
            "Pasang dari https://www.python.org/downloads/ lalu ulangi.")
    say(OK, f"Python {sys.version.split()[0]}")


def step_venv(recreate: bool) -> None:
    if recreate and VENV.exists():
        say(WARN, "Menghapus .venv lama atas permintaan --recreate-venv")
        shutil.rmtree(VENV)
    if venv_python().exists():
        say(OK, f".venv sudah ada di {VENV.relative_to(REPO_ROOT)}")
        return
    say(OK, "Membuat .venv ...")
    venv.EnvBuilder(with_pip=True, upgrade_deps=True).create(VENV)


def step_dependencies() -> None:
    say(OK, "Memasang dependensi backend (butuh beberapa menit pada pemasangan pertama) ...")
    run([str(venv_python()), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    run([str(venv_python()), "-m", "pip", "install", "--quiet", "-r", str(BACKEND / "requirements.txt")])
    # Scrapling dipakai lewat `Fetcher` (HTTP biasa) di app/research/evidence.py, bukan lewat
    # StealthyFetcher/PlayWright. Jadi `scrapling install` dan unduhan browsernya tidak diperlukan.
    version = capture([str(venv_python()), "-c",
                       "import scrapling; print(getattr(scrapling, '__version__', 'terpasang'))"])
    say(OK, f"Scrapling {version or 'terpasang'} (fetcher HTTP; tanpa unduhan browser)")


def step_env(profile: str, overwrite: bool) -> Path:
    target, example = BACKEND / ".env", BACKEND / ".env.example"
    if target.exists() and not overwrite:
        say(WARN, ".env sudah ada dan tidak disentuh. Pakai --overwrite-env kalau memang mau ditimpa.")
        return target
    if not example.exists():
        die(f"{example} tidak ditemukan.")

    lines: list[str] = []
    for line in example.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0].strip()
        if key == "SECTORS_API_KEY":
            lines.append("# Isi kalau sudah siap memakai kredit, lalu set SECTORS_API_ENABLED=true.")
            lines.append("SECTORS_API_KEY=")
        elif key == "SECTORS_API_ENABLED":
            lines.append("# Mode hemat: tidak ada panggilan Sectors, jadi tidak ada kredit terpakai.")
            lines.append("SECTORS_API_ENABLED=false")
        elif key == "SIGNALGATE_PROFILE":
            lines.append(f"SIGNALGATE_PROFILE={profile}")
        elif key in {"OLLAMA_MODEL", "OLLAMA_REVIEWER_MODELS"}:
            # Dibiarkan komentar supaya profil yang menentukan. Nilai eksplisit selalu mengalahkan
            # profil, jadi membiarkannya aktif akan diam-diam membatalkan pilihan model profil.
            lines.append(f"# {line}   <- aktifkan hanya kalau mau menimpa pilihan profil")
        else:
            lines.append(line)

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    say(OK, f".env ditulis (profil {profile}, mode hemat API aktif)")
    return target


def effective_models() -> list[str]:
    """Tanya aplikasinya sendiri model apa yang akan dipakai, supaya skrip ini tidak pernah basi."""
    script = (
        "import json;from app.config import get_settings;s=get_settings();"
        "print(json.dumps({'backend':s.llm_backend,'models':[m for m in "
        "[s.ollama_model,s.ollama_validator_model,*s.ollama_reviewer_models] if m]}))"
    )
    raw = capture([str(venv_python()), "-c", script], cwd=BACKEND)
    if not raw:
        die("Konfigurasi tidak bisa dibaca.",
            f"Coba manual:\n  cd {BACKEND}\n  {venv_python()} -c \"from app.config import get_settings; get_settings()\"")
    payload = json.loads(raw)
    if payload["backend"] != "ollama":
        say(WARN, f"LLM_BACKEND={payload['backend']}, jadi tidak ada model yang perlu diunduh.")
        return []
    return list(dict.fromkeys(payload["models"]))


def step_models(models: list[str], skip: bool) -> None:
    if skip or not models:
        say(WARN, "Unduhan model dilewati.")
        return
    if shutil.which("ollama") is None:
        die("Perintah `ollama` tidak ditemukan.",
            "Pasang dari https://ollama.com/download lalu jalankan skrip ini lagi.\n"
            "Di Windows, pakai versi terbaru: kartu RDNA4 (RX 9000) baru dikenali Ollama versi baru.")
    if not capture(["ollama", "list"]):
        die("Ollama terpasang tapi tidak merespons.", "Buka aplikasi Ollama (atau `ollama serve`) lalu ulangi.")

    installed = capture(["ollama", "list"])
    for model in models:
        if model in installed:
            say(OK, f"{model} sudah ada")
            continue
        say(OK, f"Mengunduh {model} ...")
        run(["ollama", "pull", model])


def step_tests(skip: bool) -> None:
    if skip:
        say(WARN, "Test dilewati.")
        return
    say(OK, "Menjalankan test (tanpa jaringan, tanpa Ollama) ...")
    result = run([str(venv_python()), "-m", "pytest", "-q"], cwd=BACKEND, check=False)
    if result.returncode != 0:
        die("Ada test yang gagal; jangan lanjut sebelum ini bersih.")


def report(profile: str, models: list[str]) -> None:
    # Windows PowerShell 5.1 rejects `&&` ("not a valid statement separator"), and cmd.exe rejects
    # PowerShell syntax. One command per line is the only form both shells accept, so Windows never
    # gets a chained command here.
    windows = os.name == "nt"
    activate = ".venv\\Scripts\\Activate.ps1" if windows else "source .venv/bin/activate"
    copy_env = "copy .env.example .env" if windows else "cp .env.example .env"
    join = "\n    " if windows else " && "
    shell_note = ("\nDi PowerShell aktifkan venv dengan Activate.ps1 seperti di atas; di cmd.exe pakai\n"
                  ".venv\\Scripts\\activate. Kalau prompt sudah diawali (.venv), venv-nya sudah aktif\n"
                  "dan dua baris pertama tidak perlu diulang.\n") if windows else ""
    print("\n" + "=" * 72)
    say(OK, f"Siap. Profil {profile}" + (f", model: {' -> '.join(models)}" if models else ""))
    print("=" * 72)
    print(f"""
Uji tanpa memakai satu pun kredit Sectors:

    cd backend{join}{activate}
    python -m app.scrapling_check --limit 1

Menjalankan dashboard:

    cd backend{join}{activate}{join}uvicorn app.main:app --reload
    cd frontend{join}npm install{join}{copy_env}{join}npm run dev
{shell_note}
Kalau nanti mau memakai data Sectors, isi SECTORS_API_KEY di backend/.env lalu ubah
SECTORS_API_ENABLED menjadi true. Sebelum itu, /pipeline/run memang menolak jalan; itu disengaja.
""")
    if models and shutil.which("ollama"):
        print("Pastikan model benar-benar di GPU, bukan CPU:\n\n    ollama ps\n")
        print("Kolom PROCESSOR harus '100% GPU'. Kalau '100% CPU' di Windows dengan kartu AMD RDNA4,")
        print("set HSA_OVERRIDE_GFX_VERSION=12.0.0 lalu restart Ollama dari tray.\n")


def main() -> None:
    # A legacy Windows console is cp1252 and raises UnicodeEncodeError on the first non-ASCII byte.
    # Every message here is ASCII, but a model name or pip's output need not be.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=["laptop", "workstation"], default="laptop",
                        help="laptop: qwen2.5:7b. workstation: qwen2.5:14b + 2 pembanding, untuk GPU 16 GB.")
    parser.add_argument("--overwrite-env", action="store_true", help="timpa backend/.env yang sudah ada")
    parser.add_argument("--recreate-venv", action="store_true", help="hapus dan buat ulang .venv")
    parser.add_argument("--skip-models", action="store_true", help="jangan unduh model Ollama")
    parser.add_argument("--skip-tests", action="store_true", help="jangan jalankan pytest")
    args = parser.parse_args()

    print(f"SignalGate setup - profil {args.profile}\n")
    step_python()
    step_venv(args.recreate_venv)
    step_dependencies()
    step_env(args.profile, args.overwrite_env)
    models = effective_models()
    step_models(models, args.skip_models)
    step_tests(args.skip_tests)
    report(args.profile, models)


if __name__ == "__main__":
    main()

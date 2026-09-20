"""Label manusia untuk klaim model di laporan empat panel.

Yang diukur: seberapa sering klaim yang tampil sebagai terverifikasi memang benar menurut manusia, dan
berapa klaim benar yang ikut tertolak. Lembar label sengaja buta: status sistem, catatan pembanding,
dan alasan penolakan tidak ikut diekspor; semuanya baru digabungkan saat skor dihitung.

    .venv/bin/python -m app.workflow.labels export --ticker IDEA --split dev --output ../data/labels/dev-01.csv
    .venv/bin/python -m app.workflow.labels score --labels ../data/labels/*.csv

Panduan menilai: docs/PANDUAN_LABEL.md.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from math import sqrt
from pathlib import Path

from app.workflow.evidence import display, with_input_metrics
from app.workflow.state import DOMAINS

VERDICTS = ("benar", "berlebihan", "salah_tafsir", "salah_fakta", "tak_bisa_dinilai")
WRONG = {"salah_tafsir", "salah_fakta"}
CONSENSUS = "konsensus"
COLUMNS = ["kunci", "split", "run_id", "ticker", "as_of", "panel", "jenis", "atribusi", "pernyataan", "bukti",
           "kutipan", "sumber", "putusan", "catatan", "pelabel"]


def _squash(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def claim_key(report: dict, claim: dict) -> str:
    """Sama hanya bila kalimat DAN bukti yang dirujuk sama, jadi label bisa dipakai ulang di run replay."""
    metrics = report.get("metrics") or {}
    payload = [report["ticker"], report["as_of"], _squash(claim.get("statement")), _squash(claim.get("quote")),
               sorted((ref, (metrics.get(ref) or {}).get("value")) for ref in claim.get("metric_ids") or []),
               sorted(claim.get("source_ids") or [])]
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()[:12]


def model_claims(report: dict) -> list[dict]:
    return [claim for domain in DOMAINS for claim in ((report.get("panels") or {}).get(domain) or {}).get("claims", [])
            if claim.get("author", "code") != "code"]


def load_reports(directory: Path) -> dict[str, dict]:
    """Versi laporan tertinggi per run."""
    reports = {}
    for run_dir in sorted(Path(directory).glob("*/")):
        versions = sorted(run_dir.glob("report-v*.json"), key=lambda path: int(path.stem.split("-v")[-1]))
        if versions:
            reports[run_dir.name] = json.loads(versions[-1].read_text(encoding="utf-8"))
    return reports


def models_of(report: dict) -> tuple[str, str]:
    runs = report.get("model_runs") or []
    analyst = next((run["model"] for run in runs if run.get("role") == "analyst"), "-")
    reviewer = (report.get("validation") or {}).get("reviewer_model") or next(
        (run["model"] for run in runs if run.get("role") == "reviewer"), "-")
    return analyst.removeprefix("ollama:"), reviewer.removeprefix("ollama:")


def latest_for(reports: dict[str, dict], ticker: str) -> str:
    matches = sorted((report.get("generated_at") or "", run_id) for run_id, report in reports.items()
                     if report.get("ticker") == ticker.upper())
    if not matches:
        raise SystemExit(f"Belum ada laporan untuk {ticker.upper()} di direktori workflow.")
    return matches[-1][1]


def _source_line(source_id: str, run_dir: Path) -> str:
    path = run_dir / "sources" / ("".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in source_id) + ".json")
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return source_id
    source, payload = stored.get("source") or {}, stored.get("payload")
    parts = [source_id, str(source.get("available_at") or source.get("period") or "")[:10]]
    if source.get("kind") == "sectors_news" and isinstance(payload, dict):
        parts += [_squash(payload.get("title"))[:160], (source.get("params") or {}).get("url") or ""]
    parts.append(f"{run_dir.name}/sources/{path.name}")
    return " | ".join(part for part in parts if part)


def evidence_text(report: dict, claim: dict) -> str:
    """Metrik yang dirujuk beserta metrik masukannya, dalam bentuk yang juga dilihat analis."""
    lines = []
    for metric in with_input_metrics(claim.get("metric_ids") or [], report.get("metrics") or {}):
        line = f"{metric['metric_id']}: {metric['name']} ({metric['period']}) = {display(metric)}; {metric['formula']}"
        if metric.get("status") != "ok":
            line += f" [{metric['status']}]"
        lines.append(line)
    unknown = [ref for ref in claim.get("metric_ids") or [] if ref not in (report.get("metrics") or {})]
    lines += [f"{ref}: (tidak ada di laporan)" for ref in unknown]
    return "\n".join(lines)


def export_rows(reports: dict[str, dict], run_ids: list[str], directory: Path, split: str,
                skip: set[str] = frozenset()) -> list[dict]:
    rows, seen = [], set(skip)
    for run_id in run_ids:
        report = reports[run_id]
        for claim in model_claims(report):
            key = claim_key(report, claim)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"kunci": key, "split": split, "run_id": run_id, "ticker": report["ticker"],
                         "as_of": report["as_of"], "panel": claim["domain"], "jenis": claim.get("kind", ""),
                         "atribusi": claim.get("attribution", ""), "pernyataan": claim["statement"],
                         "bukti": evidence_text(report, claim), "kutipan": claim.get("quote") or "",
                         "sumber": "\n".join(_source_line(ref, directory / run_id) for ref in claim.get("source_ids") or []),
                         "putusan": "", "catatan": "", "pelabel": ""})
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def read_labels(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        text = Path(path).read_text(encoding="utf-8-sig")
        delimiter = ";" if text.split("\n", 1)[0].count(";") > text.split("\n", 1)[0].count(",") else ","
        for line, row in enumerate(csv.DictReader(text.splitlines(keepends=True), delimiter=delimiter), start=2):
            rows.append({**{key: (value or "").strip() for key, value in row.items() if key}, "_file": str(path),
                         "_line": line})
    return rows


def resolve_labels(rows: list[dict]) -> tuple[dict[str, dict], dict]:
    """Satu putusan per kunci. Label `konsensus` menang; dua pelabel yang berbeda menjadi konflik."""
    by_key, invalid = defaultdict(list), []
    for row in rows:
        verdict = row.get("putusan", "").lower()
        if not verdict:
            continue
        if verdict not in VERDICTS:
            invalid.append(f"{row['_file']}:{row['_line']} putusan '{row['putusan']}' tidak dikenal")
            continue
        by_key[row["kunci"]].append({**row, "putusan": verdict})
    resolved, conflicts, pairs = {}, [], []
    for key, labels in by_key.items():
        final = [label for label in labels if label.get("pelabel", "").lower() == CONSENSUS]
        people = {}
        for label in labels:
            if label.get("pelabel", "").lower() != CONSENSUS:
                people.setdefault(label.get("pelabel") or label["_file"], label["putusan"])
        if len(people) >= 2:
            first, second = list(people.values())[:2]
            pairs.append((first, second))
        if final:
            resolved[key] = final[-1]
        elif len(set(people.values())) == 1:
            resolved[key] = labels[0]
        else:
            conflicts.append({"kunci": key, "putusan": people, "pernyataan": labels[0].get("pernyataan", "")})
    agreement = None
    if pairs:
        agreement = {"klaim_dilabel_dua_orang": len(pairs),
                     "persen_sepakat": round(sum(a == b for a, b in pairs) / len(pairs), 3),
                     "cohen_kappa": cohen_kappa(pairs)}
    return resolved, {"tidak_valid": invalid, "konflik": conflicts, "kesepakatan": agreement}


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    left, right = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(left[label] * right[label] for label in left) / (n * n)
    return None if expected == 1 else round((observed - expected) / (1 - expected), 3)


def wilson(hits: int, n: int, z: float = 1.96) -> list[float] | None:
    """Interval 95%. Dengan puluhan klaim, rentangnya lebar; laporkan apa adanya."""
    if not n:
        return None
    p, denominator = hits / n, 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def system_outcome(claim: dict) -> tuple[str, str | None]:
    """(tampil|ditolak|tertunda, siapa yang menolak)."""
    status = claim.get("validation_status")
    if status == "supported":
        return "tampil", None
    if claim.get("mechanical_issues"):
        return "ditolak", "kode"
    if status in {"unsupported", "contradicted"}:
        return "ditolak", "analis_menarik" if claim.get("withdrawn") else "pembanding"
    return "tertunda", None


def select_runs(reports: dict[str, dict], keys: set[str], explicit: list[str]) -> list[str]:
    """Default: run terbaru per (ticker, tanggal acuan, analis, pembanding) yang memuat klaim di lembar."""
    if explicit:
        missing = [run_id for run_id in explicit if run_id not in reports]
        if missing:
            raise SystemExit(f"Run tidak ditemukan: {', '.join(missing)}")
        return explicit
    latest = {}
    for run_id, report in reports.items():
        if not any(claim_key(report, claim) in keys for claim in model_claims(report)):
            continue
        group = (report["ticker"], report["as_of"], *models_of(report))
        if group not in latest or (report.get("generated_at") or "") > (reports[latest[group]].get("generated_at") or ""):
            latest[group] = run_id
    return sorted(latest.values())


def _metrics(records: list[dict]) -> dict:
    judged = [row for row in records if row["putusan"] and row["putusan"] != "tak_bisa_dinilai"]
    shown = [row for row in judged if row["sistem"] == "tampil"]
    rejected = [row for row in judged if row["sistem"] == "ditolak"]
    wrong = [row for row in judged if row["putusan"] in WRONG]
    correct = sum(row["putusan"] == "benar" for row in shown)
    lenient = sum(row["putusan"] in {"benar", "berlebihan"} for row in shown)
    caught = sum(row["sistem"] == "ditolak" for row in wrong)
    return {
        "klaim": len(records), "belum_dilabel": sum(not row["putusan"] for row in records),
        "tak_bisa_dinilai": sum(row["putusan"] == "tak_bisa_dinilai" for row in records),
        "tertunda": sum(row["sistem"] == "tertunda" for row in judged),
        "tampil": len(shown), "tampil_putusan": dict(Counter(row["putusan"] for row in shown)),
        "presisi_ketat": round(correct / len(shown), 3) if shown else None,
        "presisi_ketat_ci95": wilson(correct, len(shown)),
        "presisi_longgar": round(lenient / len(shown), 3) if shown else None,
        "presisi_longgar_ci95": wilson(lenient, len(shown)),
        "ditolak": len(rejected),
        "ditolak_padahal_benar": sum(row["putusan"] == "benar" for row in rejected),
        "ditolak_oleh": dict(Counter(row["oleh"] for row in rejected)),
        "klaim_salah": len(wrong), "klaim_salah_tertangkap": caught,
        "tingkat_tangkap": round(caught / len(wrong), 3) if wrong else None,
    }


def score(reports: dict[str, dict], label_rows: list[dict], explicit_runs: list[str] = ()) -> dict:
    labels, problems = resolve_labels(label_rows)
    splits = {row["kunci"]: row.get("split") or "dev" for row in label_rows}
    run_ids = select_runs(reports, set(splits), list(explicit_runs))
    records = []
    for run_id in run_ids:
        report = reports[run_id]
        analyst, reviewer = models_of(report)
        for claim in model_claims(report):
            key = claim_key(report, claim)
            outcome, by = system_outcome(claim)
            label = labels.get(key) or {}
            records.append({"kunci": key, "run_id": run_id, "ticker": report["ticker"], "panel": claim["domain"],
                            "jenis": claim.get("kind"), "konfigurasi": f"{analyst} -> {reviewer}",
                            "split": splits.get(key, "belum_dilabel"), "sistem": outcome, "oleh": by,
                            "putusan": label.get("putusan", ""), "catatan": label.get("catatan", ""),
                            "pernyataan": claim["statement"], "catatan_sistem": claim.get("validation_notes") or []})
    groups = defaultdict(list)
    for row in records:
        groups[(row["konfigurasi"], "semua")].append(row)
        groups[(row["konfigurasi"], f"split={row['split']}")].append(row)
        groups[(row["konfigurasi"], f"panel={row['panel']}")].append(row)
    shown_bad = [row for row in records if row["sistem"] == "tampil" and row["putusan"] in WRONG | {"berlebihan"}]
    lost = [row for row in records if row["sistem"] == "ditolak" and row["putusan"] == "benar"]
    keep = ("run_id", "panel", "putusan", "oleh", "pernyataan", "catatan", "catatan_sistem")
    return {
        "runs": run_ids,
        "ringkasan": {f"{config} | {scope}": _metrics(rows) for (config, scope), rows
                      in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1] != "semua", item[0][1]))},
        "lolos_padahal_bermasalah": [{key: row[key] for key in keep} for row in shown_bad],
        "ditolak_padahal_benar": [{key: row[key] for key in keep} for row in lost],
        "label": problems,
        "catatan": "Presisi ketat: hanya 'benar'. Longgar: 'benar' + 'berlebihan'. Satu klaim dihitung sekali "
                   "per run; run replay dengan konfigurasi sama tidak ikut dihitung kecuali disebut lewat --run.",
    }


def _percent(value) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def print_summary(result: dict) -> None:
    print(f"Run dinilai: {', '.join(result['runs']) or '(tidak ada run yang memuat klaim berlabel)'}")
    for name, item in result["ringkasan"].items():
        ci = item["presisi_ketat_ci95"]
        print(f"\n{name}")
        print(f"  klaim {item['klaim']}, belum dilabel {item['belum_dilabel']}, tak bisa dinilai {item['tak_bisa_dinilai']}")
        print(f"  tampil {item['tampil']}: presisi ketat {_percent(item['presisi_ketat'])}"
              + (f" (CI95 {_percent(ci[0])}–{_percent(ci[1])})" if ci else "")
              + f", longgar {_percent(item['presisi_longgar'])} | {item['tampil_putusan']}")
        print(f"  ditolak {item['ditolak']} {item['ditolak_oleh']}, padahal benar {item['ditolak_padahal_benar']}")
        print(f"  klaim salah {item['klaim_salah']}, tertangkap {item['klaim_salah_tertangkap']} "
              f"({_percent(item['tingkat_tangkap'])})")
    for title, key in (("LOLOS PADAHAL BERMASALAH", "lolos_padahal_bermasalah"),
                       ("DITOLAK PADAHAL BENAR", "ditolak_padahal_benar")):
        if result[key]:
            print(f"\n{title}:")
            for row in result[key]:
                print(f"  [{row['putusan']}{'/' + row['oleh'] if row['oleh'] else ''}] {row['pernyataan'][:140]}")
                if row["catatan"]:
                    print(f"      manusia: {row['catatan'][:140]}")
    problems = result["label"]
    for issue in problems["tidak_valid"]:
        print(f"PERINGATAN: {issue}")
    for conflict in problems["konflik"]:
        print(f"KONFLIK {conflict['kunci']}: {conflict['putusan']} — tambahkan baris pelabel=konsensus")
    if problems["kesepakatan"]:
        print(f"\nKesepakatan antar-pelabel: {problems['kesepakatan']}")


def main() -> None:
    from app.config import get_settings

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workflow-dir", type=Path, default=None, help="default: WORKFLOW_DIRECTORY di .env")
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export", help="lembar label buta dari laporan yang sudah ada (0 kredit, tanpa model)")
    exp.add_argument("--run", action="append", default=[], help="run_id; boleh diulang")
    exp.add_argument("--ticker", action="append", default=[], help="run terbaru ticker ini; boleh diulang")
    exp.add_argument("--split", choices=("dev", "holdout"), default="dev")
    exp.add_argument("--skip-labeled", type=Path, nargs="*", default=[], help="lembar lama; kuncinya tidak diekspor ulang")
    exp.add_argument("--output", type=Path, required=True)
    sc = sub.add_parser("score", help="gabungkan label manusia dengan putusan sistem")
    sc.add_argument("--labels", type=Path, nargs="+", required=True)
    sc.add_argument("--run", action="append", default=[], help="default: run terbaru per konfigurasi model")
    sc.add_argument("--output", type=Path, default=None, help="tulis hasil lengkap sebagai JSON")
    args = parser.parse_args()

    directory = args.workflow_dir or get_settings().workflow_directory
    reports = load_reports(directory)
    if args.command == "export":
        if args.output.exists():
            raise SystemExit("File label sudah ada; label yang sudah diisi tidak ditimpa. Pakai nama baru.")
        run_ids = list(dict.fromkeys([*args.run, *(latest_for(reports, ticker) for ticker in args.ticker)]))
        if not run_ids:
            parser.error("Sebutkan --run atau --ticker.")
        unknown = [run_id for run_id in run_ids if run_id not in reports]
        if unknown:
            raise SystemExit(f"Run tanpa laporan: {', '.join(unknown)}")
        skip = {row["kunci"] for row in read_labels(args.skip_labeled)}
        rows = export_rows(reports, run_ids, Path(directory), args.split, skip)
        write_csv(args.output, rows)
        print(f"{len(rows)} klaim dari {len(run_ids)} run -> {args.output}")
        return
    result = score(reports, read_labels(args.labels), args.run)
    print_summary(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

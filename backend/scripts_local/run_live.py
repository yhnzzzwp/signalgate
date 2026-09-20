"""Run laporan empat panel dengan Sectors API sungguhan dan model lokal.

Memakai kredit Sectors (perkiraan 13). Menulis ke database dan direktori snapshot aplikasi, jadi
hasilnya langsung terlihat di tab "Laporan emiten". `.env` tidak diubah selain profil: mode hemat API
hanya dilonggarkan untuk proses ini.

Analis dan pembanding memakai model dari SIGNALGATE_PROFILE di .env (`ollama_model` dan reviewer
pertama di `ollama_reviewer_models`) kecuali argumen kedua diisi eksplisit. Tidak ada model <8B lagi
per keputusan 2026-09-19 (docs/PERBAIKAN.md): gemma3:4b terbukti salah baca tanda metrik sebagai
pembanding pada evaluasi label manusia.

    .venv/bin/python scripts_local/run_live.py IDEA                           # live, model dari profil
    .venv/bin/python scripts_local/run_live.py IDEA gemma3:12b                # live, pembanding lain
    .venv/bin/python scripts_local/run_live.py IDEA glm4:9b <run_id_lama>     # replay, 0 kredit
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db.session import build_session_factory
from app.sectors.client import SectorsClient
from app.workflow.models import ModelPool, resolve_models
from app.workflow.runner import WorkflowRunner
from app.workflow.snapshot import today_wib

TICKER = sys.argv[1] if len(sys.argv) > 1 else "IDEA"
REVIEWER = sys.argv[2] if len(sys.argv) > 2 else None
REPLAY = sys.argv[3] if len(sys.argv) > 3 else None

overrides = {"sectors_api_enabled": True, "workflow_num_ctx": 8192, "workflow_num_predict": 1024}
if REVIEWER:
    overrides["workflow_reviewer_model"] = REVIEWER
settings = get_settings().model_copy(update=overrides)
print("analis/pembanding:", resolve_models(settings), "| as_of", today_wib(), "| ticker", TICKER, flush=True)

session_factory = build_session_factory(settings)
runner = WorkflowRunner(settings, session_factory,
                        client_factory=lambda: SectorsClient(api_key=settings.sectors_api_keys),
                        progress=lambda phase, node, detail: print(f"  [{phase}] {node}", flush=True)
                        if phase == "start" else None,
                        model_pool_factory=ModelPool)
run = runner.create(TICKER, "medium", None, replay_of=REPLAY)
print("run_id", run["run_id"], flush=True)
started = time.monotonic()
try:
    result = runner.execute(run["run_id"])
finally:
    elapsed = time.monotonic() - started
report = runner.report(run["run_id"]) or {}

print(f"\n=== {result['status']} dalam {elapsed / 60:.1f} menit | kredit {report.get('credits_used')} ===")
print("\nSUMBER:")
for source in report.get("sources", []):
    print(f"  {source['source_id']:42} {source['status']:8} kredit={source['credits']} "
          f"tersedia={source['available_at']}" + (f" | {source['error'][:90]}" if source.get("error") else ""))

print("\nMETRIK per panel:")
metrics = report.get("metrics", {})
for domain in ("fundamental", "valuation", "technical", "news"):
    rows = [m for m in metrics.values() if m["domain"] == domain]
    ok = [m for m in rows if m["status"] == "ok"]
    print(f"  {domain:12} {len(ok)}/{len(rows)} terisi")
    for metric in rows:
        if metric["status"] != "ok":
            print(f"      kosong: {metric['name']} ({metric['status']}) {metric.get('note') or ''}")

print("\nPANEL:")
for domain, panel in report.get("panels", {}).items():
    print(f"  == {domain} [{panel['status']}] {panel['headline'][:90]}")
    for claim in panel["claims"]:
        mark = {"supported": "OK ", "unsupported": "X  ", "contradicted": "!! ", "pending": "?  "}.get(
            claim["validation_status"], "?  ")
        who = "kode" if claim["author"] == "code" else claim["author"].replace("model:ollama:", "")
        print(f"     {mark}[{who}] {claim['statement'][:100]}")
        for note in claim["validation_notes"][:2]:
            print(f"          - {note[:110]}")
    for item in panel["missing_data"][:3]:
        print(f"     kurang: {item[:110]}")
    for item in panel["limitations"][:4]:
        print(f"     batas : {item[:110]}")
    for item in panel["conflicts"]:
        print(f"     konflik: {item[:110]}")

print("\nRINGKASAN:", report.get("synthesis", {}).get("author"))
for section in report.get("synthesis", {}).get("sections", []):
    print("  -", section["text"][:200])
for dropped in report.get("synthesis", {}).get("dropped", []):
    print("  ditahan:", dropped["text"][:90], "|", dropped["issues"][0][:90])

print("\nMODEL:")
for item in report.get("model_runs", []):
    label = "offload" if item.get("offload") else f"{item['prompt_chars']} char"
    print(f"  {item['role']:9} {item['model']:18} {item['seconds']:6.1f}s ok={item['ok']} {label}"
          + (f" | {str(item.get('error'))[:70]}" if item.get("error") else ""))
print("\ngate:", report.get("gate"), "| seri:", json.dumps(report.get("series", {}).get("quality", {}))[:200])

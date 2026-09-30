"""Contoh LangGraph offline. Semua output adalah fixture, bukan analisis saham.

Install dependencies in a separate venv; see docs/GUIDELINE_WORKFLOW.md.
Supports checkpoint resume after a simulated failure at the publish node.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph


DOMAINS = ("fundamental", "valuation", "technical", "news")


class ResearchState(TypedDict):
    run_id: str
    ticker: str
    as_of: str
    mode: str
    leave_unresolved: bool
    sources: dict
    domains: dict
    validation: dict
    repair_count: int
    report: dict
    published_path: str


def plan(state: ResearchState) -> dict:
    return {"mode": "OFFLINE_FIXTURE", "repair_count": 0, "domains": {}}


def fetch_snapshot(state: ResearchState) -> dict:
    # Replace with a snapshot repository and real Sectors adapters.
    # Availability must be <= as_of for historical evaluations.
    return {"sources": {"fixture:company": {"kind": "synthetic", "ticker": state["ticker"],
                                           "available_at": state["as_of"]}}}


def build_domain(domain: str):
    def node(state: ResearchState) -> dict:
        # Production: deterministic calculations + schema-validated LLM output.
        # Deliberately seed a broken news source ref to demonstrate targeted repair.
        source_id = "missing-source" if domain == "news" else "fixture:company"
        result = {"status": "completed", "claims": [
            {"claim_id": f"{domain}:1", "statement": f"Contoh keluaran {domain}; data sintetis.",
             "source_ids": [source_id], "validation_status": "pending"}
        ]}
        return {"domains": {**state["domains"], domain: result}}
    return node


def validate(state: ResearchState) -> dict:
    unresolved = []
    domains = json.loads(json.dumps(state["domains"]))
    for domain in DOMAINS:
        result = domains[domain]
        for claim in result["claims"]:
            known = bool(claim["source_ids"]) and all(ref in state["sources"] for ref in claim["source_ids"])
            claim["validation_status"] = "supported" if known else "unsupported"
            if not known:
                unresolved.append({"domain": domain, "claim_id": claim["claim_id"], "reason": "Sumber tidak ditemukan"})
        result["status"] = "needs_review" if any(x["domain"] == domain for x in unresolved) else "completed"
    # Reference validation only. Production also checks quotes/numbers/identity and semantics.
    return {"domains": domains, "validation": {"scope": "fixture_reference_only", "unresolved": unresolved}}


def route_validation(state: ResearchState) -> str:
    if state["validation"]["unresolved"] and state["repair_count"] < 1:
        return "repair"
    return "synthesis"


def repair(state: ResearchState) -> dict:
    domains = json.loads(json.dumps(state["domains"]))
    if state.get("leave_unresolved", False):
        return {"repair_count": state["repair_count"] + 1}
    for problem in state["validation"]["unresolved"]:
        for claim in domains[problem["domain"]]["claims"]:
            if claim["claim_id"] == problem["claim_id"]:
                # Demo fixture correction only. Never relabel a real source without checking it.
                claim["source_ids"] = ["fixture:company"]
    return {"domains": domains, "repair_count": state["repair_count"] + 1}


def synthesis(state: ResearchState) -> dict:
    unresolved = state["validation"]["unresolved"]
    return {"report": {"run_id": state["run_id"], "ticker": state["ticker"], "as_of": state["as_of"],
                       "mode": "OFFLINE_FIXTURE", "status": "needs_review" if unresolved else "fixture_completed",
                       "notice": "Data dan analisis sintetis. Belum memvalidasi makna klaim atau kualitas model.",
                       "domains": state["domains"], "validation": state["validation"],
                       "repair_count": state["repair_count"]}}


def build_graph(checkpointer, output_dir: Path, fail_publish: bool):
    def publish(state: ResearchState) -> dict:
        if fail_publish:
            raise RuntimeError("Simulasi gagal publikasi. Ulangi dengan --resume tanpa --fail-publish.")
        # Same run/version overwrites the same path: replay does not create a second report.
        path = output_dir / f"{state['run_id']}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state["report"], ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return {"published_path": str(path)}

    builder = StateGraph(ResearchState)
    nodes = {"plan": plan, "fetch": fetch_snapshot,
             **{domain: build_domain(domain) for domain in DOMAINS},
             "validate": validate, "repair": repair, "synthesis": synthesis, "publish": publish}
    for name, node in nodes.items():
        builder.add_node(name, node)
    sequence = ["plan", "fetch", *DOMAINS, "validate"]
    builder.add_edge(START, sequence[0])
    for left, right in zip(sequence, sequence[1:]):
        builder.add_edge(left, right)
    builder.add_conditional_edges("validate", route_validation, {"repair": "repair", "synthesis": "synthesis"})
    builder.add_edge("repair", "validate")
    builder.add_edge("synthesis", "publish")
    builder.add_edge("publish", END)
    return builder.compile(checkpointer=checkpointer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ticker", default="TEST")
    parser.add_argument("--as-of", default=datetime.now(timezone.utc).isoformat())
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--fail-publish", action="store_true")
    parser.add_argument("--leave-unresolved", action="store_true", help="Fixture repair tidak menyelesaikan masalah.")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.run_id):
        parser.error("run-id hanya boleh huruf, angka, underscore dan tanda minus (1–64 karakter).")
    try:
        moment = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    except ValueError:
        parser.error("as-of harus berupa tanggal ISO 8601 dengan zona waktu.")
    if moment.tzinfo is None:
        parser.error("as-of harus menyertakan zona waktu, misalnya +07:00 atau Z.")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    config = {"configurable": {"thread_id": args.run_id}, "recursion_limit": 30}
    with SqliteSaver.from_conn_string(str(output_dir / "checkpoints.sqlite")) as saver:
        graph = build_graph(saver, output_dir, args.fail_publish)
        snapshot = graph.get_state(config)
        if args.resume:
            if not snapshot.values:
                parser.error("Checkpoint run ini tidak ditemukan pada output-dir tersebut.")
            if not snapshot.next:
                print(json.dumps({"status": "already_completed", "published_path": snapshot.values.get("published_path")}, ensure_ascii=False))
                return
            initial = None
        else:
            if snapshot.values:
                parser.error("run-id sudah ada. Gunakan --resume atau run-id baru.")
            initial = {"run_id": args.run_id, "ticker": args.ticker.upper(),
                       "as_of": moment.astimezone(timezone.utc).isoformat(), "leave_unresolved": args.leave_unresolved}
        result = graph.invoke(initial, config=config)
        print(json.dumps({"status": result["report"]["status"], "repair_count": result["repair_count"],
                          "published_path": result["published_path"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

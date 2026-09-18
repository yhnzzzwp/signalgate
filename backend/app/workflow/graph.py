"""Susunan graph: dependensi, routing perbaikan, progres, timeout, dan pembatalan.

Graph yang menegakkan urutan dan batas, bukan model yang memutuskan kapan laporan boleh terbit.
Empat dimensi wajib selalu dilewati; node yang kekurangan data menulis alasannya, bukan menghilang.
"""
from __future__ import annotations

import time

from langgraph.graph import END, START, StateGraph

from app.workflow import nodes
from app.workflow.snapshot import now_iso
from app.workflow.state import GraphState

SEQUENCE = ("plan", "snapshot", "calculate", "research", "news", "validate", "review")
NODE_FUNCTIONS = {
    "plan": "plan_node", "snapshot": "snapshot_node", "calculate": "calculate_node", "research": "research_node",
    "news": "news_node", "validate": "mechanical_node", "review": "review_node", "repair": "repair_node",
    "synthesis": "synthesis_node", "report": "report_node", "publish": "publish_node",
}
NODE_LABELS = {
    "plan": "Menyusun rencana", "snapshot": "Mengambil snapshot Sectors", "calculate": "Menghitung metrik",
    "research": "Analis membaca fundamental dan valuasi", "news": "Analis membaca berita",
    "validate": "Pemeriksaan kode", "review": "Pembanding independen", "repair": "Perbaikan terarah",
    "synthesis": "Menyusun laporan", "report": "Gerbang akhir", "publish": "Menyimpan laporan",
}


class WorkflowCancelled(RuntimeError):
    pass


class WorkflowTimeout(RuntimeError):
    pass


def build_graph(context: nodes.Context, checkpointer, *, progress=None, cancel=None, deadline=None):
    """`progress(phase, node, state)` dipanggil sebelum dan sesudah tiap node, jadi task tercatat
    sebelum operasinya dimulai. `cancel` event dan `deadline` (time.monotonic) diperiksa antarnode."""

    def wrap(name, function):
        def node(state: GraphState) -> dict:
            if cancel is not None and cancel.is_set():
                raise WorkflowCancelled(f"Run dibatalkan sebelum node {name}.")
            if deadline is not None and time.monotonic() > deadline:
                raise WorkflowTimeout(f"Batas waktu run terlewati sebelum node {name}.")
            if progress is not None:
                progress("start", name, state)
            started = time.monotonic()
            update = function(state, context) or {}
            timing = {"node": name, "seconds": round(time.monotonic() - started, 2), "at": now_iso()}
            if progress is not None:
                progress("end", name, {**state, **update})
            return {**update, "node_timings": [*(state.get("node_timings") or []), timing]}

        return node

    builder = StateGraph(GraphState)
    for name, attribute in NODE_FUNCTIONS.items():
        builder.add_node(name, wrap(name, getattr(nodes, attribute)))
    builder.add_edge(START, SEQUENCE[0])
    for left, right in zip(SEQUENCE, SEQUENCE[1:]):
        builder.add_edge(left, right)
    builder.add_conditional_edges("review", nodes.route_after_review,
                                  {"repair": "repair", "synthesis": "synthesis"})
    # Klaim yang diperbaiki wajib melewati pemeriksaan kode lagi sebelum dinilai ulang.
    builder.add_edge("repair", "validate")
    builder.add_edge("synthesis", "report")
    builder.add_edge("report", "publish")
    builder.add_edge("publish", END)
    return builder.compile(checkpointer=checkpointer)

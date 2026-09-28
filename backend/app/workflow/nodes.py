"""Node graph laporan empat panel. Tiap node menerima state dan mengembalikan potongan state.

Pembagian kerjanya tetap: kode mengambil data, menghitung, memeriksa, dan memutuskan status; model
menafsirkan dan menyusun bahasa. Kegagalan model menjadi keterbatasan yang tercatat, bukan alasan
menerbitkan klaim yang belum terverifikasi.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.models import ScreenedEventRecord, WorkflowReportRecord, WorkflowRunRecord
from app.frontier import evidence as frontier_evidence
from app.frontier.reconcile import merge_local, reconcile, reviewer_conflict
from app.research import chronology
from app.research.facts import locate_quote
from app.workflow import calculations as calc
from app.workflow import prompts, templates
from app.workflow.evidence import (check_claim, comparison_issues, denied_terms, unexplained_numbers,
                                   with_input_metrics)
from app.research.agents import CHARS_PER_TOKEN
from app.workflow.models import ANALYST, REVIEWER, ModelPool
from app.workflow.snapshot import SnapshotStore, fetch_snapshot, now_iso, today_wib
from app.workflow.state import DOMAINS, MAX_REPAIRS, SCHEMA_VERSION

# Jenis aksi modal yang angka-nya disusun sebagai kronologi (rencana -> revisi -> persetujuan -> realisasi).
CHRONOLOGY_ACTION_TYPES = {"rights_issue", "private_placement", "acquisition", "divestment", "control_change",
                           "debt_conversion"}
CHRONOLOGY_PREFIX = "Kronologi "
# Ruang prompt frontier di luar artikel: aturan, instruksi, contoh JSON, metrik, klaim, dan pendapat lokal.
FRONTIER_PROMPT_RESERVE = 9_000

ANNUAL_LAG_DAYS, QUARTER_LAG_DAYS = 90, 60
PROMPT_HEADROOM = 800


@dataclass
class Context:
    settings: object
    store: SnapshotStore
    gateway: object
    run_dir: Path
    session_factory: object | None = None
    models: ModelPool | None = None
    # Reviewer frontier opsional (app.frontier.service.FrontierService). None = FRONTIER_ENABLED=false.
    frontier: object | None = None
    # True = hanya respons frontier tersimpan yang boleh dipakai (mis. run replay); API tidak dipanggil.
    frontier_offline: bool = False
    # Konfigurasi efektif nonrahasia yang terikat pada run ini (app/runtime.py) dan riwayat resume-nya.
    runtime: dict | None = None
    # Indeks entri riwayat konfigurasi yang sedang berlaku; dicatat di setiap node_timings.
    config_index: int | None = None


def _reviewer_roles(ctx: Context) -> list[str]:
    """Peran pembanding berurutan. Pool lama/palsu tanpa `reviewer_roles` tetap satu pembanding."""
    roles = getattr(ctx.models, "reviewer_roles", None) if ctx.models is not None else None
    return list(roles()) if callable(roles) else [REVIEWER]


def _as_of(state) -> date:
    return date.fromisoformat(state["request"]["as_of"])


def _panel(domain, claims=(), metrics=(), missing=(), limitations=(), conflicts=(), headline=""):
    return {"domain": domain, "status": "needs_review", "headline": headline, "metrics": list(metrics),
            "claims": list(claims), "missing_data": list(missing), "limitations": list(limitations),
            "conflicts": list(conflicts), "model_gap": None}


def _claims(state):
    for panel in state.get("panels", {}).values():
        yield from panel["claims"]


def _ok_sources(state, kind=None):
    return {key: value for key, value in state.get("sources", {}).items()
            if value.get("status") == "ok" and (kind is None or value.get("kind") == kind)}


def plan_node(state, ctx: Context) -> dict:
    """Rencana dibentuk kode: empat dimensi wajib, tidak ada yang boleh dihapus model."""
    as_of = _as_of(state)
    replay_mode = getattr(ctx.gateway, "snapshot_mode", None) if ctx.gateway.mode == "replay" else None
    live = replay_mode == "live" if replay_mode else as_of >= today_wib()
    return {
        "mode": "live" if live else "historical",
        "run_dir": str(ctx.run_dir),
        "repair_count": 0,
        "repaired_claim_ids": [],
        "model_runs": [],
        "credits_used": 0,
        "plan": {
            "domains": list(DOMAINS),
            "as_of": state["request"]["as_of"],
            "horizon": state["request"]["horizon"],
            "data_mode": ctx.gateway.mode,
            "schema_version": SCHEMA_VERSION,
            "prompt_version": prompts.PROMPT_VERSION,
            "calc_version": calc.CALC_VERSION,
            "analyst_model": (ctx.models.name(ANALYST) if ctx.models else None),
            "reviewer_model": _reviewer_label(ctx),
            "reviewer_models": [ctx.models.name(role) for role in _reviewer_roles(ctx)] if ctx.models else [],
            "max_repairs": MAX_REPAIRS,
            "frontier": ({key: value for key, value in ctx.settings.public_frontier().items()
                          if key in {"enabled", "provider", "model", "mode", "cache_mode"}}
                         | {"offline": ctx.frontier_offline} if ctx.frontier is not None else {"enabled": False}),
        },
    }


def _reviewer_label(ctx: Context) -> str | None:
    """Nama pembanding untuk tampilan/label: satu nama (seperti dulu) atau digabung "+" bila berurutan."""
    if not ctx.models:
        return None
    names = [ctx.models.name(role) for role in _reviewer_roles(ctx)]
    names = [name for name in names if name]
    return "+".join(names) if names else None


def _screening_source(ctx: Context, ticker: str, as_of: date):
    if ctx.session_factory is None:
        return None, None
    cutoff = datetime.combine(as_of, datetime.max.time(), tzinfo=timezone.utc)
    with ctx.session_factory() as session:
        record = session.scalars(
            select(ScreenedEventRecord).where(ScreenedEventRecord.ticker == ticker)
            .order_by(ScreenedEventRecord.created_at.desc())
        ).first()
    if record is None or record.created_at is None:
        return None, None
    created = record.created_at if record.created_at.tzinfo else record.created_at.replace(tzinfo=timezone.utc)
    if created > cutoff:
        return None, None
    source_id = f"signalgate:screening:{record.id}"
    source = {"source_id": source_id, "kind": "signalgate_screening", "endpoint": "db:screened_events",
              "params": {"id": record.id}, "ticker": ticker, "fetched_at": now_iso(),
              "available_at": created.isoformat(), "period": created.date().isoformat(), "status": "ok",
              "credits": 0, "mode": "live", "path": None, "sha256": None, "error": None}
    screening = {"source_id": source_id, "label": record.label, "gate_status": record.gate_status,
                 "date": created.date().isoformat(), "headline": record.headline}
    return source, screening


def snapshot_node(state, ctx: Context) -> dict:
    request, as_of = state["request"], _as_of(state)
    settings = ctx.settings
    sources = fetch_snapshot(ctx.gateway, ctx.store, request["ticker"], as_of, state["mode"] == "live",
                             quarters=settings.workflow_quarters, price_days=settings.workflow_price_calendar_days,
                             news_days=settings.workflow_news_days, news_limit=settings.workflow_news_limit)
    source, screening = _screening_source(ctx, request["ticker"], as_of)
    if source:
        sources[source["source_id"]] = source
    return {"sources": sources, "screening": screening,
            "credits_used": sum(int(item.get("credits") or 0) for item in sources.values())}


def _available_quarters(rows, as_of: date, historical: bool):
    """Di mode historis, kuartal yang belum melewati batas lapor tidak dianggap tersedia."""
    if not historical:
        return list(rows or []), None
    kept, skipped = [], 0
    for row in rows or []:
        end = calc._day((row or {}).get("date"))
        if end is None:
            continue
        lag = ANNUAL_LAG_DAYS if end.month == 12 else QUARTER_LAG_DAYS
        if end + timedelta(days=lag) <= as_of:
            kept.append(row)
        else:
            skipped += 1
    note = (f"{skipped} kuartal terbaru diabaikan karena batas lapor konservatif ({QUARTER_LAG_DAYS} hari, "
            f"{ANNUAL_LAG_DAYS} hari untuk kuartal Desember) belum terlewati pada tanggal acuan; Sectors tidak "
            "menyertakan tanggal publikasi.") if skipped else None
    return kept, note


def calculate_node(state, ctx: Context) -> dict:
    sources, store, as_of = state["sources"], ctx.store, _as_of(state)
    historical = state["mode"] != "live"
    ticker = state["request"]["ticker"]

    report_source = sources.get("sectors:company_report") or {}
    report = store.payload(report_source) if report_source.get("status") == "ok" else None
    report_id = report_source["source_id"] if report else None
    quarterly_source = sources.get("sectors:quarterly") or {}
    quarterly_id = quarterly_source["source_id"] if quarterly_source.get("status") == "ok" else None
    rows = store.payload(quarterly_source) if quarterly_id else []
    rows, availability_note = _available_quarters(rows if isinstance(rows, list) else [], as_of, historical)

    fundamental, f_missing, f_limits = calc.fundamental_metrics(report, rows, report_id, quarterly_id)
    if availability_note:
        f_limits.append(availability_note)
    valuation, v_missing, v_limits, v_conflicts = calc.valuation_metrics(report, fundamental, report_id, quarterly_id,
                                                                        ticker, not historical)
    if report_source.get("status") not in {"ok", "excluded"}:
        f_missing.append(f"Company report tidak terpakai: {report_source.get('error') or report_source.get('status')}.")
    if quarterly_source.get("status") not in {"ok"}:
        f_missing.append(f"Laporan kuartalan tidak terpakai: {quarterly_source.get('error') or quarterly_source.get('status')}.")

    daily_sources = [source for key, source in sorted(sources.items()) if source.get("kind") == "sectors_daily"]
    daily_rows, daily_ids = [], []
    for source in daily_sources:
        if source.get("status") == "ok":
            payload = store.payload(source)
            daily_rows += payload if isinstance(payload, list) else []
            daily_ids.append(source["source_id"])
    bars, quality = calc.clean_daily(daily_rows, as_of)
    action_source = sources.get("sectors:corporate_actions") or {}
    action_id = action_source["source_id"] if action_source.get("status") == "ok" else None
    actions = calc.corporate_action_breaks(store.payload(action_source) if action_id else None)
    actions = [item for item in actions if item["date"] <= as_of.isoformat()]
    breaks = sorted(actions + calc.suspected_breaks(bars), key=lambda item: item["date"])
    technical, t_missing, t_limits, series_info = calc.technical_metrics(bars, quality, breaks, daily_ids, action_id)
    failed_daily = [source for source in daily_sources if source.get("status") == "error"]
    if failed_daily:
        t_missing.append(f"{len(failed_daily)} permintaan harga harian gagal: {failed_daily[0].get('error')}")

    articles, duplicates = _unique_articles(state, store)
    news_source = sources.get("sectors:news") or {}
    news_id = news_source["source_id"] if news_source.get("status") in {"ok", "empty"} else None
    news_metrics = [
        calc._metric("news:unique_articles", "news", "Artikel unik", float(len(articles)), "artikel",
                     "jumlah artikel setelah salinan digabung", news_source.get("period") or "", [news_id] if news_id else []),
        calc._metric("news:duplicate_articles", "news", "Salinan artikel", float(duplicates), "artikel",
                     "artikel dengan judul sama yang tidak dihitung ulang", news_source.get("period") or "",
                     [news_id] if news_id else []),
    ]

    metrics = {metric["metric_id"]: metric for metric in fundamental + valuation + technical + news_metrics}
    technical_claims, technical_headline = templates.technical_claims(metrics)
    news_missing, news_limits = [], []
    if not articles:
        news_missing.append("Tidak ada artikel berita Sectors untuk emiten ini pada rentang yang diambil.")
    if news_source.get("status") == "error":
        news_missing.append(f"Permintaan berita gagal: {news_source.get('error')}.")
    if action_source.get("status") == "error":
        news_missing.append(f"Permintaan corporate actions gagal: {action_source.get('error')}.")
    news_limits.append("Jumlah artikel bukan jumlah konfirmasi independen; salinan digabungkan berdasarkan judul.")

    panels = {
        "fundamental": _panel("fundamental", templates.calculation_claims("fundamental", metrics),
                              [m for m in metrics if m.startswith("fundamental:")], f_missing, f_limits),
        "valuation": _panel("valuation", templates.calculation_claims("valuation", metrics),
                            [m for m in metrics if m.startswith("valuation:")], v_missing, v_limits, v_conflicts),
        "technical": _panel("technical", technical_claims, [m for m in metrics if m.startswith("technical:")],
                            t_missing, t_limits, headline=technical_headline),
        "news": _panel("news", templates.news_code_claims(actions, action_id, state.get("screening"), len(articles),
                                                           duplicates, news_id),
                       [m for m in metrics if m.startswith("news:")], news_missing, news_limits),
    }
    return {"metrics": metrics, "panels": panels, "series": {"quality": quality, **series_info},
            "corporate_actions": actions}


def _normalise_title(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _unique_articles(state, store: SnapshotStore) -> tuple[list[tuple[dict, str]], int]:
    """Artikel salinan digabung: jumlah artikel bukan jumlah konfirmasi independen."""
    seen, unique, duplicates = set(), [], 0
    for source in sorted(_ok_sources(state, "sectors_news").values(), key=lambda item: item.get("available_at") or ""):
        if source["source_id"] == "sectors:news":
            continue
        text = store.text(source)
        key = _normalise_title(text.split("\n", 1)[0])
        if key and key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique.append((source, text))
    return unique, duplicates


def _company_view(state, ctx: Context) -> dict:
    report = ctx.store.payload(state["sources"].get("sectors:company_report") or {}) or {}
    overview = report.get("overview") or {}
    return {"ticker": state["request"]["ticker"], "nama": report.get("company_name"), "sektor": overview.get("sector"),
            "subsektor": overview.get("sub_sector"), "tanggal_acuan": state["request"]["as_of"],
            "horizon": state["request"]["horizon"]}


def _strip_parenthetical(text: str) -> str:
    """Lepas anotasi dalam kurung, mis. "(Persero)", yang berita/model sering hilangkan saat parafrase."""
    return re.sub(r"\s{2,}", " ", re.sub(r"\s*\([^)]*\)", "", text)).strip()


def _name_variants(name: str) -> list[str]:
    """Variasi nama emiten yang lazim di berita: dengan/tanpa "PT"/"Tbk", dan tanpa anotasi kurung
    seperti "(Persero)" (mis. qwen2.5:14b menulis "PT Telkom Indonesia Tbk" tanpa "(Persero)")."""
    if not name:
        return []
    short = re.sub(r"^\s*PT\.?\s+|\s+Tbk\.?\s*$", "", name, flags=re.IGNORECASE).strip()
    return [name, short, _strip_parenthetical(name), _strip_parenthetical(short)]


def _identity(state, ctx: Context) -> list[str]:
    """Ticker dan variasi nama emiten yang lazim di berita (lihat `_name_variants`).

    Kosong bila nama tidak diketahui -- company report tidak diambil di mode historis, sengaja, karena
    isinya keadaan terkini (lihat snapshot.py). Ticker mentah saja BUKAN pengganti yang aman: artikel
    berita hampir selalu menyebut nama emiten, jarang kode tickernya apa adanya, jadi mengetatkan ke
    ticker saja menolak klaim yang benar (identity_issue tidak memeriksa apa pun bila identity kosong).
    """
    name = _company_view(state, ctx).get("nama") or ""
    if not name:
        return []
    candidates = [state["request"]["ticker"], *_name_variants(name)]
    return [item for item in dict.fromkeys(candidates) if item]


def _budget(ctx: Context, role: str, instruction: str) -> int:
    total = ctx.models.budget(role) if ctx.models else 0
    return max(0, total - len(prompts.RULES) - len(instruction) - PROMPT_HEADROOM)


def _model_claim(domain, index, draft, author, kind="interpretation", **extra):
    claim = {"claim_id": f"{domain}:model:{index}", "domain": domain, "kind": kind,
             "statement": draft.statement.strip(), "attribution": "data",
             "metric_ids": list(getattr(draft, "metric_ids", []) or []),
             "source_ids": [], "quote": None, "period": None, "supports_claim_ids": [], "assumptions": [],
             "limitations": list(getattr(draft, "limitations", []) or []), "validation_status": "pending",
             "validation_notes": [], "mechanical_issues": [], "review_notes": [], "version": 1, "author": author}
    claim.update(extra)
    return claim


def research_node(state, ctx: Context) -> dict:
    """Satu panggilan analis untuk fundamental dan valuation; keluarannya dua panel terpisah."""
    panels = {key: dict(value) for key, value in state["panels"].items()}
    usable = [metric for metric in state["metrics"].values()
              if metric["domain"] in {"fundamental", "valuation"} and metric["status"] != "insufficient_data"]
    if not usable:
        for domain in ("fundamental", "valuation"):
            panels[domain] = {**panels[domain],
                              "limitations": [*panels[domain]["limitations"],
                                              "Tidak ada metrik yang bisa ditafsirkan, jadi analis tidak dipanggil."]}
        return {"panels": panels}
    budget = _budget(ctx, ANALYST, prompts.RESEARCH_INSTRUCTION)
    limitations = panels["fundamental"]["limitations"] + panels["valuation"]["limitations"]
    packet, dropped = prompts.research_packet(_company_view(state, ctx), usable, limitations, budget)
    result, error = (None, ctx.models.reason(ANALYST) or "Model analis tidak tersedia.") if not budget else \
        ctx.models.call(ANALYST, prompts.prompt(prompts.RESEARCH_INSTRUCTION, packet), prompts.ResearchOutput)
    author = f"model:{ctx.models.name(ANALYST)}" if ctx.models else "model"
    for domain in ("fundamental", "valuation"):
        panel = {**panels[domain], "claims": list(panels[domain]["claims"]),
                 "limitations": list(panels[domain]["limitations"])}
        if dropped:
            panel["limitations"].append(f"{dropped} metrik tidak dikirim ke analis karena batas konteks.")
        if error:
            panel["limitations"].append(f"Interpretasi analis tidak tersedia: {error}")
            panel["model_gap"] = error
        else:
            for index, draft in enumerate(getattr(result, domain), start=1):
                panel["claims"].append(_model_claim(domain, index, draft, author))
        panels[domain] = panel
    return {"panels": panels, "model_runs": list(ctx.models.runs) if ctx.models else []}


def news_node(state, ctx: Context) -> dict:
    panel = {**state["panels"]["news"], "claims": list(state["panels"]["news"]["claims"]),
             "limitations": list(state["panels"]["news"]["limitations"])}
    articles, _duplicates = _unique_articles(state, ctx.store)
    if not articles:
        panel["limitations"].append("Tidak ada artikel untuk dibaca model.")
        return {"panels": {**state["panels"], "news": panel}, "events": []}
    budget = _budget(ctx, ANALYST, prompts.NEWS_INSTRUCTION)
    packet, dropped = prompts.news_packet(_company_view(state, ctx), articles, state.get("corporate_actions") or [],
                                          budget)
    result, error = (None, ctx.models.reason(ANALYST) or "Model analis tidak tersedia.") if not budget else \
        ctx.models.call(ANALYST, prompts.prompt(prompts.NEWS_INSTRUCTION, packet), prompts.NewsOutput)
    if dropped:
        panel["limitations"].append(f"{dropped} artikel tidak dikirim ke model karena batas konteks; "
                                    "cakupan bacaan model lebih sempit dari daftar artikel.")
    events = []
    if error:
        panel["limitations"].append(f"Pembacaan berita oleh model tidak tersedia: {error}")
        panel["model_gap"] = error
    else:
        author = f"model:{ctx.models.name(ANALYST)}"
        for index, event in enumerate(result.events, start=1):
            kind = "observation" if event.attribution in {"document", "company_statement"} else "interpretation"
            claim = _model_claim("news", index, event, author, kind=kind,
                                 source_ids=list(event.source_ids), quote=event.quote.strip() or None,
                                 period=event.event_date or None, attribution=event.attribution)
            claim["metric_ids"] = []
            panel["claims"].append(claim)
            events.append({"event_type": event.event_type, "event_date": event.event_date,
                           "claim_id": claim["claim_id"], "source_ids": list(event.source_ids)})
    return {"panels": {**state["panels"], "news": panel}, "events": events,
            "model_runs": list(ctx.models.runs) if ctx.models else []}


def mechanical_node(state, ctx: Context) -> dict:
    """Pemeriksaan kode. Lolos di sini belum berarti terverifikasi; gagal di sini tidak bisa dianulir model."""
    metrics, sources = state["metrics"], state["sources"]
    texts = {key: ctx.store.text(source) for key, source in sources.items()
             if source.get("kind") in {"sectors_news", "sectors_corporate_actions"} and source.get("path")}
    repaired = set(state.get("repaired_claim_ids") or [])
    identity = _identity(state, ctx)
    panels, failures = {}, {}
    for domain, panel in state["panels"].items():
        claims = []
        for claim in panel["claims"]:
            issues = check_claim(claim, metrics, sources, texts, state["request"]["ticker"], _as_of(state), identity)
            reviewed = list(claim.get("review_notes") or [])
            updated = {**claim, "mechanical_issues": list(issues), "review_notes": reviewed,
                       "validation_notes": [*issues, *reviewed]}
            if issues:
                updated["validation_status"] = "unsupported"
                failures[claim["claim_id"]] = issues
            elif claim["author"] == "code":
                updated["validation_status"] = "supported"
            elif claim["validation_status"] in {"supported", "unsupported", "contradicted"} and claim["claim_id"] not in repaired:
                updated["validation_status"] = claim["validation_status"]
            else:
                updated["validation_status"] = "pending"
            claims.append(updated)
        panels[domain] = {**panel, "claims": claims}
    validation = {**(state.get("validation") or {}), "scope": "kode: referensi, identitas, waktu, kutipan, angka, gate",
                  "mechanical_failures": failures}
    return {"panels": panels, "validation": validation}


def _review_scope(state):
    """Klaim model yang menunggu putusan, dipisah per paket bukti."""
    metrics_claims, news_claims = [], []
    for claim in _claims(state):
        if claim["validation_status"] != "pending" or claim["author"] == "code":
            continue
        (news_claims if claim["domain"] == "news" else metrics_claims).append(claim)
    return metrics_claims, news_claims


def review_node(state, ctx: Context) -> dict:
    """Pembanding lokal berurutan (satu model di VRAM pada satu waktu). Putusan tiap pembanding disimpan
    terpisah di `reviewer_verdicts`, lalu digabung pesimistis: satu bantahan menang, dan status supported
    butuh persetujuan SEMUA pembanding. Konflik antarpembanding ditandai `reviewer_conflict`."""
    metrics_claims, news_claims = _review_scope(state)
    if not metrics_claims and not news_claims:
        return {}
    roles = _reviewer_roles(ctx)
    multi = len(roles) > 1
    notes_store = {}
    verdicts = {role: {} for role in roles}
    problems_by_role = {role: [] for role in roles}
    company = _company_view(state, ctx)
    articles, _duplicates = _unique_articles(state, ctx.store)
    usable = [metric for metric in state["metrics"].values() if metric["status"] != "insufficient_data"]
    packets = []
    if metrics_claims:
        packets.append(("metrik", lambda budget: prompts.research_packet(company, usable, [], budget), metrics_claims))
    if news_claims:
        packets.append(("berita", lambda budget: prompts.news_packet(company, articles,
                                                                    state.get("corporate_actions") or [], budget),
                        news_claims))
    notes_reserve = ctx.settings.workflow_num_predict * CHARS_PER_TOKEN
    for position, role in enumerate(roles, start=1):
        who = f" (pembanding {position})" if multi else ""
        for label, build, claims in packets:
            verdict_budget = _budget(ctx, role, prompts.REVIEW_VERDICT_INSTRUCTION)
            packet_budget = min(_budget(ctx, role, prompts.REVIEW_NOTES_INSTRUCTION),
                                max(0, verdict_budget - notes_reserve))
            packet, _dropped = build(packet_budget)
            notes, error = ctx.models.call(role, prompts.prompt(prompts.REVIEW_NOTES_INSTRUCTION, packet),
                                           prompts.ReviewNotes) if ctx.models else (None, "Pembanding tidak tersedia.")
            if error:
                problems_by_role[role].append(f"Pembacaan independen {label}{who} gagal: {error}")
                continue
            notes_key = f"{role}:{label}" if multi else label
            notes_store[notes_key] = notes.model_dump(mode="json")
            base_packet = {**packet, "catatan_independen_anda": notes_store[notes_key]}
            claims_budget = max(0, verdict_budget - len(json.dumps(base_packet, ensure_ascii=False)))
            batch_error = None
            for batch in prompts.claim_batches(claims, claims_budget):
                verdict_packet = {**base_packet, "klaim_analis": batch}
                result, error = ctx.models.call(role, prompts.prompt(prompts.REVIEW_VERDICT_INSTRUCTION, verdict_packet),
                                                prompts.ReviewVerdicts)
                if error:
                    batch_error = error
                    continue
                known = {view["claim_id"] for view in batch}
                for verdict in result.verdicts:
                    if verdict.claim_id in known:
                        verdicts[role][verdict.claim_id] = (verdict.status, verdict.reason.strip())
            if batch_error:
                problems_by_role[role].append(f"Putusan pembanding {label}{who} gagal: {batch_error}")
    problems = [problem for role in roles for problem in problems_by_role[role]]
    names = {role: (ctx.models.name(role) if ctx.models else None) for role in roles}

    panels = {}
    for domain, panel in state["panels"].items():
        claims = []
        for claim in panel["claims"]:
            if claim["validation_status"] != "pending" or claim["author"] == "code":
                claims.append(claim)
                continue
            decisions = [verdicts[role].get(claim["claim_id"]) for role in roles]
            if not any(decisions):
                note = problems[0] if problems else "Pembanding tidak memberi putusan untuk klaim ini."
                reviewed = [*(claim.get("review_notes") or []), note]
                claims.append({**claim, "review_notes": reviewed,
                               "validation_notes": [*(claim.get("mechanical_issues") or []), *reviewed]})
                continue
            statuses = [decision[0] if decision else None for decision in decisions]
            per_reviewer = [{"role": role, "model": names[role], "status": decision[0] if decision else None,
                             "reason": (decision[1] if decision else
                                        (problems_by_role[role][0] if problems_by_role[role] else "tidak memberi putusan"))}
                            for role, decision in zip(roles, decisions)]
            if multi:
                notes = [f"Pembanding {position} ({item['model']}): "
                         + (f"{item['status']} — {item['reason']}" if item["status"] else item["reason"])
                         for position, item in enumerate(per_reviewer, start=1)]
            else:
                notes = [f"Pembanding: {decisions[0][1]}"] if decisions[0][1] else []
            conflict = reviewer_conflict(statuses)
            if conflict:
                notes.append("Pembanding lokal berbeda pendapat; status digabung pesimistis dan konflik dicatat.")
            reviewed = [*(claim.get("review_notes") or []), *notes]
            status = merge_local(statuses, len(roles))
            claims.append({**claim, "validation_status": status, "review_notes": reviewed,
                           "validation_notes": [*(claim.get("mechanical_issues") or []), *reviewed],
                           "reviewer_verdicts": per_reviewer, "reviewer_conflict": conflict})
        limitations = list(panel["limitations"])
        if problems and any(claim["validation_status"] == "pending" for claim in claims):
            limitations.append(problems[0])
        panels[domain] = {**panel, "claims": claims, "limitations": limitations}
    validation = {**(state.get("validation") or {}), "reviewer_problems": problems,
                  "reviewer_model": _reviewer_label(ctx),
                  "reviewer_models": [names[role] for role in roles]}
    return {"panels": panels, "reviewer_notes": notes_store, "validation": validation,
            "model_runs": list(ctx.models.runs) if ctx.models else []}


def _failing_model_claims(state):
    return [claim for claim in _claims(state)
            if claim["author"] != "code" and claim["validation_status"] in {"unsupported", "contradicted"}]


def route_after_review(state) -> str:
    return "repair" if _failing_model_claims(state) and state.get("repair_count", 0) < MAX_REPAIRS else "synthesis"


def repair_node(state, ctx: Context) -> dict:
    """Satu putaran, hanya klaim bermasalah, beserta alasan gagalnya."""
    failing = _failing_model_claims(state)
    company = _company_view(state, ctx)
    usable = [metric for metric in state["metrics"].values() if metric["status"] != "insufficient_data"]
    articles, _duplicates = _unique_articles(state, ctx.store)
    packet = {"emiten": company,
              "klaim_bermasalah": [{**prompts.claim_view(claim), "alasan_gagal": claim["validation_notes"][:3]}
                                   for claim in failing],
              "metrik": [prompts.metric_view(metric) for metric in usable][:20],
              "artikel": [prompts.article_view(source, text, 400) for source, text in articles][:6]}
    result, error = ctx.models.call(ANALYST, prompts.prompt(prompts.REPAIR_INSTRUCTION, packet),
                                    prompts.RepairOutput) if ctx.models else (None, "Analis tidak tersedia.")
    repaired, dropped = {}, {}
    if not error:
        allowed = {claim["claim_id"] for claim in failing}
        for item in result.claims:
            if item.claim_id not in allowed:
                continue
            if item.drop or not item.statement.strip():
                dropped[item.claim_id] = "Analis menarik klaim ini setelah pemeriksaan."
            else:
                repaired[item.claim_id] = item
    panels = {}
    for domain, panel in state["panels"].items():
        claims = []
        for claim in panel["claims"]:
            item = repaired.get(claim["claim_id"])
            if item is not None:
                claims.append({**claim, "statement": item.statement.strip(), "metric_ids": list(item.metric_ids),
                               "source_ids": list(item.source_ids) or claim["source_ids"],
                               "quote": item.quote.strip() or claim["quote"], "version": claim["version"] + 1,
                               "validation_status": "pending", "validation_notes": [], "mechanical_issues": [],
                               "review_notes": [], "reviewer_verdicts": [], "reviewer_conflict": False})
            elif claim["claim_id"] in dropped:
                reviewed = [*(claim.get("review_notes") or []), dropped[claim["claim_id"]]]
                claims.append({**claim, "validation_status": "unsupported", "review_notes": reviewed,
                               "withdrawn": True,
                               "validation_notes": [*(claim.get("mechanical_issues") or []), *reviewed]})
            else:
                claims.append(claim)
        limitations = list(panel["limitations"])
        if error and any(claim["claim_id"] in {item["claim_id"] for item in failing} for claim in panel["claims"]):
            limitations.append(f"Perbaikan klaim tidak dapat dijalankan: {error}")
        panels[domain] = {**panel, "claims": claims, "limitations": limitations}
    return {"panels": panels, "repair_count": state.get("repair_count", 0) + 1,
            "repaired_claim_ids": sorted(repaired), "model_runs": list(ctx.models.runs) if ctx.models else []}


def _news_texts(state, ctx: Context) -> dict[str, str]:
    return {key: ctx.store.text(source) for key, source in state["sources"].items()
            if source.get("kind") == "sectors_news" and source.get("path") and source.get("status") == "ok"}


def _local_timeline(state, texts: dict[str, str]) -> list[chronology.TimelineEntry]:
    """Entri kronologi dari klaim berita model yang lolos pemeriksaan mekanis (kutipan terverifikasi)."""
    events = {event["claim_id"]: event for event in state.get("events") or []}
    entries = []
    for claim in state["panels"]["news"]["claims"]:
        event = events.get(claim["claim_id"])
        if (claim["author"] == "code" or claim.get("withdrawn") or claim.get("mechanical_issues")
                or not event or event.get("event_type") not in CHRONOLOGY_ACTION_TYPES):
            continue
        quote = claim.get("quote") or ""
        source_id = next((ref for ref in claim.get("source_ids") or []
                          if ref in texts and (not quote or locate_quote(quote, texts[ref]))), None)
        if source_id is None:
            continue
        entries += chronology.entries_from_text(
            prefix=f"{claim['claim_id']}:v{claim.get('version', 1)}", action_type=event["event_type"], action_ref=None,
            quote=quote, statement=claim["statement"], source_id=source_id, source_text=texts[source_id],
            published_at=state["sources"][source_id].get("available_at"),
            event_date_claimed=event.get("event_date") or None, claim_id=claim["claim_id"], origin="local_claim")
    return entries


def _with_chronology_conflicts(panel: dict, actions: list[dict]) -> dict:
    kept = [item for item in panel["conflicts"] if not item.startswith(CHRONOLOGY_PREFIX)]
    return {**panel, "conflicts": [*kept, *chronology.conflict_messages(actions)]}


def chronology_node(state, ctx: Context) -> dict:
    """Kronologi angka aksi modal dari klaim berita. Angka berbeda tanpa bukti revisi = konflik panel,
    bukan angka terbaru yang dipilih diam-diam."""
    texts = _news_texts(state, ctx)
    actions = chronology.build_chronology(_local_timeline(state, texts), texts)
    panels = dict(state["panels"])
    panels["news"] = _with_chronology_conflicts(panels["news"], actions)
    return {"chronology": actions, "panels": panels}


def _frontier_packet(state, ctx: Context, escalated: list[dict], conflict_actions: list[dict]) -> tuple[dict, set[str]]:
    """Bukti terpilih untuk frontier: artikel, metrik, dan sumber yang dirujuk klaim yang dieskalasi."""
    metrics, sources = state["metrics"], state["sources"]
    metric_ids = []
    for claim in escalated:
        for metric in with_input_metrics(claim.get("metric_ids") or [], metrics):
            if metric["metric_id"] not in metric_ids:
                metric_ids.append(metric["metric_id"])
    wanted = {ref for claim in escalated for ref in claim.get("source_ids") or []}
    wanted |= {ref for action in conflict_actions for ref in action["source_ids"]}
    for metric_id in metric_ids:
        wanted |= set(metrics[metric_id].get("source_ids") or [])
    # Kutipan klaim dan angka kronologi per sumber: potongan artikel dipusatkan di sekitarnya (plus kata
    # koreksi/revisi, tanggal, angka), bukan sekadar karakter awal artikel.
    anchors: dict[str, list[str]] = {}
    for claim in escalated:
        for ref in claim.get("source_ids") or []:
            anchors.setdefault(ref, []).append(claim.get("quote") or "")
    for action in conflict_actions:
        for entry in action["entries"]:
            anchors.setdefault(entry["source_id"], []).append(entry["quote"])
    identity = _identity(state, ctx)
    news = [(source_id, sources[source_id]) for source_id in sorted(wanted)
            if sources.get(source_id, {}).get("status") in {"ok", "empty"}
            and sources[source_id].get("kind") == "sectors_news"]
    descriptors = [{"id": source_id, "jenis": sources[source_id].get("kind"), "periode": sources[source_id].get("period"),
                    "tersedia_sejak": sources[source_id].get("available_at")}
                   for source_id in sorted(wanted) if sources.get(source_id, {}).get("status") in {"ok", "empty"}
                   and sources[source_id].get("kind") != "sectors_news"]
    # Sisa ruang prompt dibagi rata antarartikel; langkah kedua butuh ruang tambahan untuk pendapat lokal.
    available = max(2_000, ctx.settings.frontier_max_input_chars - FRONTIER_PROMPT_RESERVE)
    per_article = min(4_000, max(500, available // max(1, len(news))))

    def build_articles(limit: int) -> list[dict]:
        rows = []
        for source_id, source in news:
            title, _, body = ctx.store.text(source).partition("\n")
            cut = frontier_evidence.excerpt(body, anchors=anchors.get(source_id, []), identity=identity,
                                            max_chars=limit)
            rows.append({"id": source_id, "tanggal_terbit": source.get("available_at"), "judul": title.strip(),
                         "isi": cut["teks"], "dipotong": cut["dipotong"], "panjang_asli": cut["panjang_asli"]})
        return rows

    articles = build_articles(per_article)
    while per_article > 500 and len(json.dumps(articles, ensure_ascii=False)) > available:
        per_article = max(500, int(per_article * 0.7))
        articles = build_articles(per_article)
    packet = {
        "emiten": _company_view(state, ctx), "tanggal_acuan": state["request"]["as_of"],
        "bukti": {"artikel": articles,
                  "metrik": [{**prompts.metric_view(metrics[metric_id]), "formula": metrics[metric_id]["formula"],
                              "sumber": metrics[metric_id].get("source_ids") or []} for metric_id in metric_ids],
                  "sumber_data": descriptors,
                  "aksi_korporasi_sectors": [{"jenis": action["type"], "tanggal": action["date"]}
                                             for action in (state.get("corporate_actions") or [])[:8]]},
        "catatan_bukti": "Artikel bertanda dipotong=true hanya berisi potongan; jangan anggap sebagai seluruh sumber.",
        "angka_aksi_dari_kutipan": [
            {"sumber": entry["source_id"], "angka": entry["value_text"], "tanggal_terbit": entry["published_at"],
             "tanggal_kejadian_tertulis": entry["event_date"], "kutipan": entry["quote"][:300]}
            for action in conflict_actions for entry in action["entries"]],
    }
    valid = set(metric_ids) | {item["id"] for item in articles} | {item["id"] for item in descriptors}
    return packet, valid


def _verified_frontier_timeline(raw_entries: list[dict], state, texts: dict[str, str]):
    """Timeline frontier dipakai hanya bila kutipannya ditemukan di artikel dan angkanya ada di kutipan."""
    entries, refs = [], []
    for index, raw in enumerate(raw_entries, start=1):
        text = texts.get(raw.get("source_id") or "")
        span = locate_quote(raw.get("quote") or "", text) if text else None
        value_text = " ".join((raw.get("value_text") or "").split()).lower()
        if span is None or not value_text or value_text not in " ".join(span.split()).lower():
            continue
        refs.append(raw.get("action_ref") or "")
        entries += [entry for entry in chronology.entries_from_text(
            prefix=f"frontier:{index}", action_type=raw.get("action_type") or "other", action_ref=None, quote=span,
            statement="", source_id=raw["source_id"], source_text=text,
            published_at=state["sources"][raw["source_id"]].get("available_at"),
            event_date_claimed=raw.get("event_date") or None, claim_id=None, origin="frontier",
            stage_hint=raw.get("stage"), revises_source_hint=raw.get("revises_source_id") or None)
            if entry.metric == raw.get("metric")]
    return entries, refs


def frontier_node(state, ctx: Context) -> dict:
    """Eskalasi ke reviewer frontier. Pemicu: konflik antarpembanding lokal atau konflik angka lintas waktu.

    Shadow: hasil disimpan di `frontier` dan anotasi klaim, status apa pun tidak berubah. Escalation:
    status berubah hanya lewat `reconcile()` (klaim yang gagal cek mekanis tidak pernah dieskalasi), dan
    kronologi hanya berubah oleh entri frontier yang kutipannya terverifikasi. Kegagalan frontier tidak
    pernah menjatuhkan run: statusnya tercatat dan hasil lokal tetap dipakai.
    """
    service = ctx.frontier
    if service is None:
        return {"frontier": None}
    roles = _reviewer_roles(ctx)
    actions = state.get("chronology") or []
    conflict_ids = chronology.conflict_claim_ids(actions)
    triggers, escalated = [], []
    for claim in _claims(state):
        if claim["author"] == "code" or claim.get("withdrawn") or claim.get("mechanical_issues"):
            continue
        reasons = (["reviewer_conflict"] if claim.get("reviewer_conflict") else []) + \
                  (["timeline_conflict"] if claim["claim_id"] in conflict_ids else [])
        if reasons:
            escalated.append(claim)
            triggers += [{"claim_id": claim["claim_id"], "reason": reason} for reason in reasons]
    conflict_actions = [action for action in actions if action["conflicts"]]
    packet, valid_ids = _frontier_packet(state, ctx, escalated, conflict_actions)
    claims = [{**prompts.claim_view(claim), "periode": claim.get("period")} for claim in escalated]
    opinions = {claim["claim_id"]: [{"reviewer": item["role"], "model": item["model"], "status": item["status"],
                                     "reason": (item.get("reason") or "")[:200]}
                                    for item in claim.get("reviewer_verdicts") or [] if item.get("status")]
                for claim in escalated}
    try:
        record = service.review(run_key=state["request"]["run_id"], scope="workflow", packet=packet, claims=claims,
                                valid_ids=valid_ids, local_opinions=opinions, triggers=triggers,
                                offline=ctx.frontier_offline)
    except Exception as error:  # noqa: BLE001 - frontier tidak boleh menjatuhkan run lokal yang valid
        record = {"enabled": True, "mode": ctx.settings.frontier_mode, "status": "failed", "applied": False,
                  "message": f"Frontier gagal di luar dugaan: {type(error).__name__}", "triggers": triggers,
                  "calls": [], "verdicts": []}
    verdicts = {item["claim_id"]: item for item in record.get("verdicts") or []}

    texts = _news_texts(state, ctx)
    frontier_entries, refs = _verified_frontier_timeline(record.get("timeline_raw") or [], state, texts)
    augmented = actions
    if frontier_entries:
        entries = _local_timeline(state, texts) + frontier_entries
        chronology.assign_refs(entries, refs, texts)
        augmented = chronology.build_chronology(entries, texts)
    augmented_ids = chronology.conflict_claim_ids(augmented)
    record["chronology"] = augmented
    record["verified_timeline_entries"] = len(frontier_entries)
    record["timeline_conflicts"] = {"local": sorted(conflict_ids), "with_frontier": sorted(augmented_ids),
                                    "local_messages": chronology.conflict_messages(actions),
                                    "with_frontier_messages": chronology.conflict_messages(augmented)}

    escalate = ctx.settings.frontier_mode == "escalation"
    decisions = {}
    for claim in escalated:
        local = [item["status"] for item in claim.get("reviewer_verdicts") or []] or [claim["validation_status"]]
        decisions[claim["claim_id"]] = reconcile(local_statuses=local, expected_reviewers=len(roles),
                                                 verdict=verdicts.get(claim["claim_id"]),
                                                 mechanical_ok=not claim.get("mechanical_issues"))
    record["reconciliation"] = [{"claim_id": claim_id, **decision} for claim_id, decision in decisions.items()]
    record["differences"] = [item for item in record["reconciliation"]
                             if item["frontier_status"] and item["frontier_status"] not in item["local_statuses"]]
    record["decision_changes"] = [item["claim_id"] for item in record["reconciliation"] if item["changed"]]

    changed = False
    panels = {}
    for domain, panel in state["panels"].items():
        claims_out = []
        for claim in panel["claims"]:
            decision = decisions.get(claim["claim_id"])
            if decision is None:
                claims_out.append(claim)
                continue
            verdict = verdicts.get(claim["claim_id"]) or {}
            annotation = {"mode": ctx.settings.frontier_mode, "applied": False, "rule": decision["rule"],
                          "frontier_status": decision["frontier_status"], "final_status": decision["final_status"],
                          "note": decision["note"], "independent": verdict.get("independent"),
                          "second_look": verdict.get("second_look"),
                          "second_look_needed": verdict.get("second_look_needed", False),
                          "triggers": [item["reason"] for item in triggers if item["claim_id"] == claim["claim_id"]]}
            if escalate and decision["changed"]:
                changed = True
                note = f"Rekonsiliasi frontier ({decision['rule']}): {decision['note']}"
                reviewed = [*(claim.get("review_notes") or []), note]
                claims_out.append({**claim, "validation_status": decision["final_status"], "review_notes": reviewed,
                                   "validation_notes": [*(claim.get("mechanical_issues") or []), *reviewed],
                                   "frontier": {**annotation, "applied": True}})
            else:
                claims_out.append({**claim, "frontier": annotation})
        panels[domain] = {**panel, "claims": claims_out}
    update = {"frontier": record, "panels": panels}
    if escalate and frontier_entries and chronology.conflict_messages(augmented) != chronology.conflict_messages(actions):
        panels["news"] = _with_chronology_conflicts(panels["news"], augmented)
        update["chronology"] = augmented
        changed = True
    record["applied"] = escalate and changed
    return update


def _supported(state):
    return [claim for claim in _claims(state) if claim["validation_status"] == "supported"]


def _section_issues(section, supported_by_id, metrics):
    unknown = [claim_id for claim_id in section["claim_ids"] if claim_id not in supported_by_id]
    if unknown:
        return [f"Merujuk klaim yang tidak terverifikasi: {', '.join(unknown)}."]
    if not section["claim_ids"]:
        return ["Tidak merujuk klaim apa pun."]
    referenced = [supported_by_id[claim_id] for claim_id in section["claim_ids"]]
    allowed_metrics = with_input_metrics([ref for claim in referenced for ref in claim["metric_ids"]], metrics)
    allowed_text = " ".join([claim["statement"] for claim in referenced]
                            + [f"{metric['name']} {metric['period']} {metric['formula']}" for metric in allowed_metrics]
                            + [claim.get("quote") or "" for claim in referenced])
    issues = []
    numbers = unexplained_numbers(section["text"], allowed_metrics, allowed_text)
    if numbers:
        issues.append(f"Angka baru tanpa klaim pendukung: {', '.join(numbers)}.")
    issues += comparison_issues(section["text"], allowed_metrics)
    terms = denied_terms(section["text"])
    if terms:
        issues.append(f"Bahasa transaksi/penilaian: {', '.join(terms)}.")
    return issues


def synthesis_node(state, ctx: Context) -> dict:
    supported = _supported(state)
    by_id = {claim["claim_id"]: claim for claim in supported}
    conflicts = [item for panel in state["panels"].values() for item in panel["conflicts"]]
    limitations = [item for panel in state["panels"].values() for item in panel["limitations"]]
    if not supported:
        return {"synthesis": {"author": "code", "sections": [], "dropped": [],
                              "error": "Tidak ada klaim terverifikasi untuk diringkas."}}
    packet = {"panel": {domain: {"klaim": [{"claim_id": claim["claim_id"], "pernyataan": claim["statement"]}
                                           for claim in panel["claims"] if claim["validation_status"] == "supported"],
                                 "kekurangan_data": panel["missing_data"][:3]}
                        for domain, panel in state["panels"].items()},
              "konflik": conflicts[:4], "keterbatasan": limitations[:6]}
    result, error = ctx.models.call(ANALYST, prompts.prompt(prompts.SYNTHESIS_INSTRUCTION, packet),
                                    prompts.SynthesisOutput) if ctx.models else (None, "Analis tidak tersedia.")
    sections, dropped = [], []
    if not error:
        for section in (item.model_dump(mode="json") for item in result.sections):
            issues = _section_issues(section, by_id, state["metrics"])
            if issues:
                dropped.append({"text": section["text"], "issues": issues})
            else:
                sections.append({**section, "author": f"model:{ctx.models.name(ANALYST)}"})
    if not sections:
        for domain in DOMAINS:
            picked = [claim for claim in state["panels"][domain]["claims"]
                      if claim["validation_status"] == "supported"][:2]
            if picked:
                sections.append({"text": " ".join(claim["statement"] for claim in picked),
                                 "claim_ids": [claim["claim_id"] for claim in picked], "author": "code"})
    return {"synthesis": {"author": sections[0]["author"] if sections else "code", "sections": sections,
                          "dropped": dropped, "error": error},
            "model_runs": list(ctx.models.runs) if ctx.models else []}


def _panel_status(domain, panel, metrics):
    has_metrics = any(metrics[key]["status"] == "ok" for key in panel["metrics"] if key in metrics)
    claims = panel["claims"]
    if not has_metrics and not claims:
        return "failed" if panel["missing_data"] else "insufficient_data"
    if domain == "news":
        if not claims:
            return "insufficient_data"
    elif not has_metrics:
        return "insufficient_data"
    if any(claim["validation_status"] != "supported" and not claim.get("withdrawn") for claim in claims) \
            or panel["conflicts"]:
        return "needs_review"
    return "needs_review" if panel.get("model_gap") else "completed"


def report_node(state, ctx: Context) -> dict:
    """Gerbang terakhir: status per panel, pemeriksaan bahasa, dan laporan yang siap disimpan."""
    panels, metrics = {}, state["metrics"]
    for domain, panel in state["panels"].items():
        status = _panel_status(domain, panel, metrics)
        headline = panel["headline"] or next((claim["statement"] for claim in panel["claims"]
                                              if claim["validation_status"] == "supported"), "")
        panels[domain] = {**panel, "status": status, "headline": headline}
    synthesis = state.get("synthesis") or {"sections": [], "dropped": [], "author": "code", "error": None}
    prose = [section["text"] for section in synthesis["sections"]]
    prose += [panel["headline"] for panel in panels.values()]
    prose += [claim["statement"] for claim in _claims(state) if claim["validation_status"] == "supported"]
    rejected = denied_terms(" ".join(prose))
    statuses = {panel["status"] for panel in panels.values()}
    unresolved = [claim["claim_id"] for claim in _claims(state)
                  if claim["validation_status"] != "supported" and not claim.get("withdrawn")]
    if rejected or statuses & {"needs_review"} or synthesis["dropped"] or synthesis.get("error"):
        status = "needs_review"
    elif statuses & {"failed", "insufficient_data"}:
        status = "partial"
    else:
        status = "completed"
    sources = [{key: source.get(key) for key in ("source_id", "kind", "endpoint", "status", "fetched_at",
                                                 "available_at", "period", "sha256", "credits", "mode", "error")}
               for source in sorted(state["sources"].values(), key=lambda item: item["source_id"])]
    report = {
        "run_id": state["request"]["run_id"], "ticker": state["request"]["ticker"], "as_of": state["request"]["as_of"],
        "horizon": state["request"]["horizon"], "status": status, "report_version": 1,
        "generated_at": now_iso(), "data_mode": state["plan"]["data_mode"], "mode": state["mode"],
        "plan": state["plan"], "panels": panels, "metrics": metrics, "sources": sources,
        "synthesis": synthesis, "events": state.get("events") or [], "series": state.get("series") or {},
        "validation": {**(state.get("validation") or {}), "unresolved_claim_ids": unresolved,
                       "repair_count": state.get("repair_count", 0),
                       "reviewer_notes": state.get("reviewer_notes") or {}},
        "gate": {"status": "needs_review" if rejected else "passed", "rejected_terms": rejected},
        # Kredit Sectors saja. Token/biaya frontier dicatat terpisah di `frontier.totals` (estimasi USD).
        "credits_used": state.get("credits_used", 0), "model_runs": state.get("model_runs") or [],
        "chronology": state.get("chronology") or [],
        "frontier": state.get("frontier"),
        # Konfigurasi yang benar-benar dipakai (tanpa rahasia) dan riwayat resume-nya.
        "runtime": ctx.runtime,
        "node_timings": state.get("node_timings") or [],
        "disclaimer": "Screening dan analisis berbukti, bukan rekomendasi beli atau jual.",
    }
    return {"panels": panels, "report": report, "gate": report["gate"]}


def publish_node(state, ctx: Context) -> dict:
    """Commit dulu, baru tandai terbit. Publikasi ulang versi yang sama memperbarui baris itu."""
    report = state["report"]
    if ctx.runtime is not None:
        # Resume bisa terjadi setelah laporan tersusun (mis. publish gagal); riwayat konfigurasi yang terbit
        # harus mencakup resume itu, bukan salinan saat node report berjalan.
        report = {**report, "runtime": ctx.runtime}
    path = ctx.run_dir / f"report-v{report['report_version']}.json"
    if ctx.session_factory is None:
        return {"published": {"report_version": report["report_version"], "path": str(path), "at": now_iso()}}
    with ctx.session_factory() as session:
        existing = session.scalars(
            select(WorkflowReportRecord).where(WorkflowReportRecord.run_id == report["run_id"],
                                               WorkflowReportRecord.report_version == report["report_version"])
        ).first()
        if existing is None:
            existing = WorkflowReportRecord(run_id=report["run_id"], report_version=report["report_version"],
                                            ticker=report["ticker"], as_of=report["as_of"], status=report["status"],
                                            payload=report)
            session.add(existing)
        else:
            existing.status, existing.payload = report["status"], report
            existing.updated_at = datetime.now(timezone.utc)
        run = session.get(WorkflowRunRecord, report["run_id"])
        if run is not None:
            run.status, run.report_version = report["status"], report["report_version"]
            run.updated_at = datetime.now(timezone.utc)
        session.commit()
        report_id = existing.id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"published": {"report_version": report["report_version"], "report_id": report_id, "path": str(path),
                          "at": now_iso()}}

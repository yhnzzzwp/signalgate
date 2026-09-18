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
from app.workflow import calculations as calc
from app.workflow import prompts, templates
from app.workflow.evidence import check_claim, denied_terms, unexplained_numbers, with_input_metrics
from app.workflow.models import ANALYST, REVIEWER, ModelPool
from app.workflow.snapshot import SnapshotStore, fetch_snapshot, now_iso, today_wib
from app.workflow.state import DOMAINS, MAX_REPAIRS, SCHEMA_VERSION

# Batas lapor OJK dipakai konservatif: laporan kuartal belum tentu tersedia pada hari kuartal berakhir.
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


# ---------------------------------------------------------------------------------------------------

def plan_node(state, ctx: Context) -> dict:
    """Rencana dibentuk kode: empat dimensi wajib, tidak ada yang boleh dihapus model."""
    as_of = _as_of(state)
    live = as_of >= today_wib()
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
            "reviewer_model": (ctx.models.name(REVIEWER) if ctx.models else None),
            "max_repairs": MAX_REPAIRS,
        },
    }


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
        return {"panels": {**state["panels"], "news": panel}, "events": []}  # bukan model_gap: memang tidak ada bahan
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
    panels, failures = {}, {}
    for domain, panel in state["panels"].items():
        claims = []
        for claim in panel["claims"]:
            issues = check_claim(claim, metrics, sources, texts, state["request"]["ticker"], _as_of(state))
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
    metrics_claims, news_claims = _review_scope(state)
    if not metrics_claims and not news_claims:
        return {}
    notes_store, verdicts = {}, {}
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
    problems = []
    for label, build, claims in packets:
        packet, _dropped = build(_budget(ctx, REVIEWER, prompts.REVIEW_NOTES_INSTRUCTION))
        notes, error = ctx.models.call(REVIEWER, prompts.prompt(prompts.REVIEW_NOTES_INSTRUCTION, packet),
                                       prompts.ReviewNotes) if ctx.models else (None, "Pembanding tidak tersedia.")
        if error:
            problems.append(f"Pembacaan independen {label} gagal: {error}")
            continue
        notes_store[label] = notes.model_dump(mode="json")
        verdict_packet = {**packet, "catatan_independen_anda": notes_store[label],
                          "klaim_analis": [prompts.claim_view(claim) for claim in claims]}
        result, error = ctx.models.call(REVIEWER, prompts.prompt(prompts.REVIEW_VERDICT_INSTRUCTION, verdict_packet),
                                        prompts.ReviewVerdicts)
        if error:
            problems.append(f"Putusan pembanding {label} gagal: {error}")
            continue
        known = {claim["claim_id"] for claim in claims}
        for verdict in result.verdicts:
            if verdict.claim_id in known:
                verdicts[verdict.claim_id] = (verdict.status, verdict.reason.strip())

    panels = {}
    for domain, panel in state["panels"].items():
        claims = []
        for claim in panel["claims"]:
            decision = verdicts.get(claim["claim_id"])
            if claim["validation_status"] == "pending" and decision:
                status, reason = decision
                reviewed = [*(claim.get("review_notes") or []), f"Pembanding: {reason}"] if reason else \
                    list(claim.get("review_notes") or [])
                claims.append({**claim, "validation_status": status, "review_notes": reviewed,
                               "validation_notes": [*(claim.get("mechanical_issues") or []), *reviewed]})
            elif claim["validation_status"] == "pending":
                note = problems[0] if problems else "Pembanding tidak memberi putusan untuk klaim ini."
                reviewed = [*(claim.get("review_notes") or []), note]
                claims.append({**claim, "review_notes": reviewed,
                               "validation_notes": [*(claim.get("mechanical_issues") or []), *reviewed]})
            else:
                claims.append(claim)
        limitations = list(panel["limitations"])
        if problems and any(claim["validation_status"] == "pending" for claim in claims):
            limitations.append(problems[0])
        panels[domain] = {**panel, "claims": claims, "limitations": limitations}
    validation = {**(state.get("validation") or {}), "reviewer_problems": problems,
                  "reviewer_model": ctx.models.name(REVIEWER) if ctx.models else None}
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
                               "review_notes": []})
            elif claim["claim_id"] in dropped:
                reviewed = [*(claim.get("review_notes") or []), dropped[claim["claim_id"]]]
                claims.append({**claim, "validation_status": "unsupported", "review_notes": reviewed,
                               "validation_notes": [*(claim.get("mechanical_issues") or []), *reviewed]})
            else:
                claims.append(claim)
        limitations = list(panel["limitations"])
        if error and any(claim["claim_id"] in {item["claim_id"] for item in failing} for claim in panel["claims"]):
            limitations.append(f"Perbaikan klaim tidak dapat dijalankan: {error}")
        panels[domain] = {**panel, "claims": claims, "limitations": limitations}
    return {"panels": panels, "repair_count": state.get("repair_count", 0) + 1,
            "repaired_claim_ids": sorted(repaired), "model_runs": list(ctx.models.runs) if ctx.models else []}


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
        # Cadangan deterministik: kalimat disusun dari klaim yang sudah terverifikasi, tanpa angka baru.
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
    if any(claim["validation_status"] != "supported" for claim in claims) or panel["conflicts"]:
        return "needs_review"
    # Angka lengkap tetapi tidak ada yang menafsirkannya: panelnya belum utuh, jadi jangan disebut selesai.
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
    unresolved = [claim["claim_id"] for claim in _claims(state) if claim["validation_status"] != "supported"]
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
        "credits_used": state.get("credits_used", 0), "model_runs": state.get("model_runs") or [],
        "disclaimer": "Screening dan analisis berbukti, bukan rekomendasi beli atau jual.",
    }
    return {"panels": panels, "report": report, "gate": report["gate"]}


def publish_node(state, ctx: Context) -> dict:
    """Commit dulu, baru tandai terbit. Publikasi ulang versi yang sama memperbarui baris itu."""
    report = state["report"]
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

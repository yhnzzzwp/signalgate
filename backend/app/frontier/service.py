"""Orkestrasi satu tinjauan frontier: cache -> reservasi budget -> panggilan -> validasi -> audit.

Layanan ini tidak pernah mengubah hasil apa pun. Ia mengembalikan catatan audit dan putusan per klaim;
pemanggil (engine screening atau graph laporan) yang menerapkan `reconcile.py` bila mode escalation.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from pydantic import ValidationError

from app.config import Settings, frontier_config_issue
from app.frontier import pricing
from app.frontier.cache import FrontierCache, digest
from app.frontier.client import DeepSeekClient, FrontierCallError, parse_json_object, redact
from app.frontier.ledger import BudgetLedger, Denied
from app.frontier.prompts import (INDEPENDENT_INSTRUCTION, PROMPT_VERSION, SCHEMA_VERSION, SECOND_LOOK_INSTRUCTION,
                                  SYSTEM, FrontierReview, user_message)
from app.frontier.reconcile import normalize

log = logging.getLogger("signalgate.frontier")

# Status catatan frontier (dipakai UI).
DISABLED, NOT_TRIGGERED, COMPLETED, PARTIAL = "disabled", "not_triggered", "completed", "partial"
UNAVAILABLE, FAILED, BUDGET_EXHAUSTED, OFFLINE_MISS, IN_FLIGHT = (
    "unavailable", "failed", "budget_exhausted", "offline_cache_miss", "in_flight")


def disabled_record() -> dict:
    return {"enabled": False, "status": DISABLED, "applied": False}


def build_frontier(settings: Settings) -> "FrontierService | None":
    """None saat FRONTIER_ENABLED=false: tidak ada objek, file, atau koneksi frontier sama sekali."""
    if not settings.frontier_enabled:
        return None
    issue = frontier_config_issue(settings)
    if issue:
        log.warning("%s", issue)
    return FrontierService(settings)


# Setelah semua percobaan satu langkah gagal di fase koneksi, langkah berikutnya dalam jendela ini
# langsung dilewati: satu batch kandidat tidak perlu menunggu 2x15 detik per kandidat ke host yang mati.
UNREACHABLE_COOLDOWN_SECONDS = 120


class FrontierService:
    def __init__(self, settings: Settings, *, client=None, ledger: BudgetLedger | None = None,
                 cache: FrontierCache | None = None, now=None, sleep=time.sleep) -> None:
        self.settings = settings
        self.mode = settings.frontier_mode
        self.model = settings.frontier_model
        self.config_issue = frontier_config_issue(settings)
        self._client = client
        attempts = 1 + settings.frontier_max_retries
        self.ledger = ledger or BudgetLedger(
            settings.frontier_directory / "ledger.sqlite",
            max_calls_per_run=settings.frontier_max_calls_per_run,
            max_tokens_per_run=settings.frontier_max_tokens_per_run,
            max_cost_usd_per_run=settings.frontier_max_cost_usd_per_run,
            max_cost_usd_per_day=settings.frontier_max_cost_usd_per_day,
            max_cost_usd_total=settings.frontier_max_cost_usd_total,
            stale_after_seconds=settings.frontier_timeout_seconds * attempts + 120)
        self.cache = cache or FrontierCache(settings.frontier_directory)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        self.monotonic = time.monotonic
        self._unreachable: tuple[float, str] | None = None  # (sampai kapan, pesan terakhir)

    # -- infrastruktur ----------------------------------------------------------------------------

    def client(self):
        if self._client is None:
            self._client = DeepSeekClient(
                self.settings.deepseek_api_key.get_secret_value(), base_url=self.settings.frontier_base_url,
                model=self.model, reasoning_effort=self.settings.frontier_reasoning_effort,
                max_tokens=self.settings.frontier_max_tokens, timeout=self.settings.frontier_timeout_seconds)
        return self._client

    def _secrets(self) -> tuple[str, ...]:
        return (self.settings.deepseek_api_key.get_secret_value(),)

    def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            self._client.close()

    def _record(self, status: str, **extra) -> dict:
        record = {
            "enabled": True, "provider": self.settings.frontier_provider, "model": self.model,
            "model_version": pricing.MODEL_VERSIONS.get(self.model), "mode": self.mode, "applied": False,
            "status": status, "prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION,
            "price_version": pricing.PRICE_VERSION, "reasoning_effort": self.settings.frontier_reasoning_effort,
            "max_tokens": self.settings.frontier_max_tokens, "triggers": [], "calls": [], "verdicts": [],
            "evidence_reading": [], "limitations": [], "timeline_raw": [], "reconciliation": [], "message": None,
        }
        record.update(extra)
        return record

    # -- satu langkah ------------------------------------------------------------------------------

    def _parse(self, content: str) -> FrontierReview:
        return FrontierReview.model_validate(parse_json_object(content))

    def _step(self, *, step: str, run_key: str, reason: str, instruction: str, payload: dict, evidence_hash: str,
              claims_hash: str, opinions_hash: str | None, offline: bool, progress=None) -> tuple[FrontierReview | None, dict]:
        user = user_message(instruction, payload)
        key = digest([self.settings.frontier_provider, self.model, PROMPT_VERSION, SCHEMA_VERSION,
                      self.settings.frontier_reasoning_effort, self.settings.frontier_max_tokens, "thinking:enabled",
                      "response_format:json_object", step, evidence_hash, claims_hash, opinions_hash,
                      digest([SYSTEM, user])])
        # `usage` = jumlah SEMUA percobaan baru pada langkah ini (termasuk jawaban invalid yang tetap ditagih);
        # usage asli dari cache disimpan terpisah di `cached_usage` dan tidak pernah dihitung sebagai token baru.
        call = {"step": step, "reason": reason, "cache_key": key, "evidence_hash": evidence_hash, "status": None,
                "cached": False, "attempts": 0, "seconds": 0.0, "usage": None, "usage_known": True,
                "usage_partial": False, "cost_usd_estimate": 0.0, "cost_basis": "estimate",
                "price_version": pricing.PRICE_VERSION, "error": None, "request_id": None, "response_model": None,
                "prompt_chars": len(SYSTEM) + len(user), "overshoot": False, "attempt_log": []}

        def from_cache() -> FrontierReview | None:
            stored = self.cache.get(key)
            if not stored:
                return None
            try:
                review = self._parse(stored["response"]["content"])
            except (ValueError, KeyError, TypeError, ValidationError):
                call["attempt_log"].append({"cache": "rusak, diabaikan"})
                return None
            original = stored.get("cost") or {}
            call.update(status="cache_hit", cached=True, usage=None, cached_usage=stored["response"].get("usage"),
                        request_id=stored["response"].get("request_id"),
                        response_model=stored["response"].get("model"), cost_usd_estimate=0.0,
                        original_cost_usd_estimate=original.get("cost_usd_estimate"),
                        cached_at=stored.get("created_at"))
            return review

        # Offline selalu membaca cache lebih dulu, apa pun FRONTIER_CACHE_MODE: `refresh` hanya berarti
        # "panggil API walau ada cache" dan tidak berlaku bila jaringan memang dilarang.
        if offline or self.settings.frontier_cache_mode != "refresh":
            review = from_cache()
            if review is not None:
                return review, call
        if offline:
            call.update(status=OFFLINE_MISS,
                        error="Mode offline: respons tersimpan untuk input ini tidak ada; API tidak dipanggil.")
            return None, call
        if call["prompt_chars"] > self.settings.frontier_max_input_chars:
            call.update(status=FAILED, error=(f"input_too_large: prompt {call['prompt_chars']} karakter melebihi "
                                              f"FRONTIER_MAX_INPUT_CHARS={self.settings.frontier_max_input_chars}; "
                                              "tidak dikirim."))
            return None, call

        if self._unreachable and self.monotonic() < self._unreachable[0]:
            call.update(status=FAILED, error=("unreachable: API frontier tidak terjangkau pada percobaan terakhir "
                                              f"({self._unreachable[1]}); langkah ini dilewati tanpa panggilan dan "
                                              "tanpa biaya."))
            return None, call
        max_attempts = 1 + self.settings.frontier_max_retries
        prompt_bytes = len(SYSTEM.encode("utf-8")) + len(user.encode("utf-8"))
        unsent = 0
        total_cost = 0.0

        def add_usage(usage: dict | None) -> None:
            if not usage:
                return
            merged = dict(call["usage"] or {})
            for field, value in usage.items():
                if isinstance(value, (int, float)):
                    merged[field] = (merged.get(field) or 0) + value
                elif field not in merged:
                    merged[field] = value
            call["usage"] = merged

        def mark_unknown() -> None:
            call["usage_known"] = False
            call["usage_partial"] = call["usage"] is not None

        while call["attempts"] < max_attempts:
            tokens, usd = pricing.reservation(self.model, prompt_bytes, self.settings.frontier_max_tokens)
            reservation = self.ledger.reserve(run_key, key, tokens, usd)
            if isinstance(reservation, Denied):
                call.update(status=IN_FLIGHT if reservation.reason == "in_flight" else BUDGET_EXHAUSTED,
                            error=reservation.message, budget_limit=reservation.reason)
                break
            # Pekerjaan yang sama mungkin baru selesai di thread/proses lain sebelum reservasi ini.
            if self.settings.frontier_cache_mode != "refresh":
                review = from_cache()
                if review is not None:
                    self.ledger.release(reservation, "cache terisi oleh pekerjaan lain; tidak dikirim")
                    call["usage"] = None  # hanya cache: tidak ada token baru dari langkah ini
                    return review, call
            call["attempts"] += 1
            at = self.now()
            started = time.monotonic()
            if progress:
                progress("start", step)
            try:
                raw = self.client().complete(SYSTEM, user)
            except FrontierCallError as error:
                seconds = round(time.monotonic() - started, 2)
                call["seconds"] = round(call["seconds"] + seconds, 2)
                message = redact(str(error), self._secrets())
                cost = pricing.estimate(self.model, error.usage, at) if error.usage else None
                overshoot = False
                if not error.sent:
                    unsent += 1
                if error.billed == "no":
                    self.ledger.release(reservation, message, sent=error.sent)
                elif cost and cost.get("cost_usd_estimate") is not None:
                    overshoot = self.ledger.settle(reservation, (error.usage or {}).get("total_tokens"),
                                                   cost["cost_usd_estimate"], message)
                    total_cost += cost["cost_usd_estimate"]
                    add_usage(error.usage)
                else:
                    self.ledger.mark_unknown(reservation, message)
                    mark_unknown()
                call["overshoot"] = call["overshoot"] or overshoot
                call["attempt_log"].append({"attempt": call["attempts"], "error": message, "kind": error.kind,
                                            "phase": error.phase, "billed": error.billed, "usage": error.usage,
                                            "seconds": seconds, "reserved_usd": reservation.usd,
                                            "overshoot": overshoot})
                call.update(error=message, request_id=error.request_id or call["request_id"])
                log.warning("frontier %s gagal (%s)", step, message)
                if progress:
                    progress("end", step)
                if error.transient and call["attempts"] < max_attempts and not overshoot:
                    self.sleep(min(2 ** call["attempts"], 8))
                    continue
                call["status"] = FAILED
                if unsent == call["attempts"]:
                    self._unreachable = (self.monotonic() + UNREACHABLE_COOLDOWN_SECONDS, message)
                break
            self._unreachable = None
            seconds = round(time.monotonic() - started, 2)
            call["seconds"] = round(call["seconds"] + seconds, 2)
            cost = pricing.estimate(self.model, raw.usage, at)
            overshoot = False
            if cost.get("cost_usd_estimate") is not None:
                overshoot = self.ledger.settle(reservation, (raw.usage or {}).get("total_tokens"),
                                               cost["cost_usd_estimate"])
                total_cost += cost["cost_usd_estimate"]
                add_usage(raw.usage)
            else:
                self.ledger.mark_unknown(reservation, "usage tidak dikembalikan")
                mark_unknown()
            call["overshoot"] = call["overshoot"] or overshoot
            call.update(request_id=raw.request_id, response_model=raw.model, window=cost.get("window"))
            if progress:
                progress("end", step)
            try:
                review = self._parse(raw.content)
            except (ValueError, ValidationError) as error:
                kind = "schema_invalid" if isinstance(error, ValidationError) else "invalid_json"
                message = f"{kind}: jawaban akhir tidak sesuai schema JSON."
                call["attempt_log"].append({"attempt": call["attempts"], "error": message, "kind": kind,
                                            "billed": "yes" if raw.usage else "unknown", "usage": raw.usage,
                                            "seconds": seconds, "overshoot": overshoot})
                call["error"] = message
                if call["attempts"] < max_attempts and not overshoot:
                    continue
                call["status"] = FAILED
                break
            self.cache.put(key, {"created_at": at.isoformat(), "provider": self.settings.frontier_provider,
                                 "model": self.model, "step": step, "prompt_version": PROMPT_VERSION,
                                 "schema_version": SCHEMA_VERSION, "evidence_hash": evidence_hash,
                                 "response": {"content": raw.content, "finish_reason": raw.finish_reason,
                                              "usage": raw.usage, "request_id": raw.request_id, "model": raw.model},
                                 "cost": cost})
            call["attempt_log"].append({"attempt": call["attempts"], "ok": True, "usage": raw.usage,
                                        "seconds": seconds, "overshoot": overshoot})
            call.update(status=COMPLETED, error=None, cost_usd_known_part=round(total_cost, 8),
                        cost_usd_estimate=round(total_cost, 8) if call["usage_known"] else None)
            return review, call
        call["cost_usd_known_part"] = round(total_cost, 8)
        call["cost_usd_estimate"] = round(total_cost, 8) if call["usage_known"] else None
        if not call["usage_known"]:
            call["cost_note"] = ("Sebagian percobaan tanpa usage (mis. timeout setelah request terkirim); biayanya "
                                 "tidak diketahui dan dihitung ke budget sebesar reservasinya. Token yang tampil "
                                 "hanya bagian yang diketahui.")
        return None, call

    # -- tinjauan lengkap --------------------------------------------------------------------------

    def review(self, *, run_key: str, scope: str, packet: dict, claims: list[dict], valid_ids: set[str],
               local_opinions: dict[str, list[dict]], triggers: list[dict], offline: bool = False,
               progress=None) -> dict:
        """Catatan audit frontier. `claims`: [{claim_id, ...}] yang dieskalasi (sudah lolos cek mekanis)."""
        offline = offline or self.settings.frontier_cache_mode == "offline"
        record = self._record(NOT_TRIGGERED, scope=scope, triggers=triggers, offline=offline)
        if not triggers or not claims:
            record["message"] = ("Tidak ada konflik pembanding atau fakta lintas waktu yang perlu dieskalasi."
                                 if not triggers else
                                 "Ada pemicu, tetapi tidak ada klaim/fakta yang bisa dinilai dari bukti; konflik "
                                 "tetap tercatat dan hasil lokal tidak berubah.")
            return self._finish(record, run_key)
        if self.config_issue and not offline:
            record.update(status=UNAVAILABLE, message=self.config_issue)
            return self._finish(record, run_key)
        if not pricing.known_model(self.model) and not offline:
            record.update(status=UNAVAILABLE, message=(
                f"Harga model {self.model} tidak ada di tabel harga {pricing.PRICE_VERSION}; batas biaya tidak bisa "
                "ditegakkan, jadi frontier tidak dipanggil. Pakai FRONTIER_MODEL=deepseek-flash atau perbarui "
                "app/frontier/pricing.py."))
            return self._finish(record, run_key)

        known_claims = {claim["claim_id"] for claim in claims}
        evidence_hash = digest(packet)
        claims_hash = digest(claims)
        reason = ", ".join(sorted({item["reason"] for item in triggers}))
        independent, call = self._step(step="independent", run_key=run_key, reason=reason,
                                       instruction=INDEPENDENT_INSTRUCTION, payload={**packet, "klaim": claims},
                                       evidence_hash=evidence_hash, claims_hash=claims_hash, opinions_hash=None,
                                       offline=offline, progress=progress)
        record["calls"].append(call)
        record["evidence_hash"] = evidence_hash
        if independent is None:
            record.update(status=call["status"] if call["status"] in {BUDGET_EXHAUSTED, OFFLINE_MISS, IN_FLIGHT}
                          else FAILED, message=call["error"])
            return self._finish(record, run_key)

        first = self._valid_verdicts(independent, known_claims, valid_ids)
        record["evidence_reading"] = [item[:400] for item in independent.evidence_reading]
        record["limitations"] = [item[:400] for item in independent.limitations]
        record["timeline_raw"] = [item.model_dump() for item in independent.timeline]

        disputed = [claim_id for claim_id, verdict in first["valid"].items()
                    if any(normalize(opinion.get("status")) != verdict["status"]
                           for opinion in local_opinions.get(claim_id, []) if opinion.get("status"))]
        second = {"valid": {}, "invalid": {}}
        if disputed:
            opinions = {claim_id: local_opinions.get(claim_id, []) for claim_id in disputed}
            payload = {**packet, "klaim": [claim for claim in claims if claim["claim_id"] in disputed],
                       "pembacaan_independen_anda": [first["valid"][claim_id] | {"claim_id": claim_id}
                                                     for claim_id in disputed],
                       "pendapat_pembanding_lokal": opinions}
            looked, call = self._step(step="second_look", run_key=run_key, reason="pendapat berbeda dari pembanding lokal",
                                      instruction=SECOND_LOOK_INSTRUCTION, payload=payload,
                                      evidence_hash=evidence_hash, claims_hash=digest(payload["klaim"]),
                                      opinions_hash=digest(opinions), offline=offline, progress=progress)
            record["calls"].append(call)
            if looked is not None:
                second = self._valid_verdicts(looked, set(disputed), valid_ids)
            else:
                record["second_look_status"] = call["status"]
                record["message"] = f"Langkah kedua tidak berjalan ({call['status']}): {call['error']}"

        for claim in claims:
            claim_id = claim["claim_id"]
            verdict = first["valid"].get(claim_id)
            record["verdicts"].append({
                "claim_id": claim_id, "independent": verdict,
                "independent_invalid": first["invalid"].get(claim_id),
                "second_look_needed": claim_id in disputed,
                "second_look": second["valid"].get(claim_id),
                "second_look_invalid": second["invalid"].get(claim_id)})
        failed_second = disputed and any(item["second_look_needed"] and not item["second_look"]
                                         for item in record["verdicts"])
        record["status"] = PARTIAL if failed_second or len(first["valid"]) < len(known_claims) else COMPLETED
        return self._finish(record, run_key)

    def _valid_verdicts(self, review: FrontierReview, known_claims: set[str], valid_ids: set[str]) -> dict:
        """Putusan dengan claim_id dikenal, minimal satu ID bukti sah, dan alasan tidak kosong."""
        valid, invalid = {}, {}
        for verdict in review.verdicts:
            if verdict.claim_id not in known_claims or verdict.claim_id in valid:
                continue
            unknown = [ref for ref in verdict.evidence_ids if ref not in valid_ids]
            cited = [ref for ref in verdict.evidence_ids if ref in valid_ids]
            if unknown or not cited or not verdict.reason.strip():
                invalid[verdict.claim_id] = {
                    "status": verdict.status, "reason": verdict.reason.strip()[:400],
                    "problem": (f"ID bukti tidak dikenal: {', '.join(unknown[:4])}" if unknown else
                                "tanpa rujukan bukti" if not cited else "tanpa alasan")}
                continue
            valid[verdict.claim_id] = {"status": verdict.status, "evidence_ids": cited,
                                       "reason": verdict.reason.strip()[:400]}
        return {"valid": valid, "invalid": invalid}

    def _finish(self, record: dict, run_key: str) -> dict:
        calls = record["calls"]
        # Hanya pemakaian BARU run ini: cache hit tidak menambah token/biaya (usage aslinya di `cached_usage`).
        live = [call for call in calls if not call.get("cached")]
        usage = [call.get("usage") or {} for call in live]
        unknown = any(not call.get("usage_known", True) for call in live)
        record["totals"] = {
            "calls": sum(1 for call in live if call.get("attempts")), "attempts": sum(call.get("attempts", 0) for call in live),
            "cache_hits": sum(1 for call in calls if call.get("cached")),
            "prompt_tokens": sum(int(item.get("prompt_tokens") or 0) for item in usage),
            "completion_tokens": sum(int(item.get("completion_tokens") or 0) for item in usage),
            "reasoning_tokens": sum(int(item.get("reasoning_tokens") or 0) for item in usage),
            "cost_usd_estimate": None if unknown else round(sum(call.get("cost_usd_estimate") or 0.0 for call in live), 8),
            "cost_usd_known_part": round(sum(call.get("cost_usd_known_part") or 0.0 for call in live), 8),
            "usage_unknown": unknown, "usage_partial": unknown and any(usage),
            "overshoot": any(call.get("overshoot") for call in live),
            "cost_basis": "estimate", "price_version": pricing.PRICE_VERSION,
        }
        try:
            record["budget"] = self.ledger.usage(run_key)
        except Exception as error:  # noqa: BLE001 - audit budget tidak boleh menjatuhkan run
            record["budget"] = {"error": redact(str(error), self._secrets())}
        return record

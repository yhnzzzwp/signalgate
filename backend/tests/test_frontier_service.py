"""Layanan frontier dengan klien palsu: retry, cache/offline, budget, in-flight, dan validasi putusan."""
import json
import threading

from app.frontier.client import FrontierCallError
from app.frontier.reconcile import reconcile
from tests.frontier_fakes import (AGREE, FAKE_KEY, SPLIT, NoCallClient, ScriptedClient, completion,
                                  frontier_settings, review_json, run_review, service, timeout_error, verdict)


def test_c02_enabled_without_key_is_unavailable_and_never_calls(tmp_path):
    frontier = service(frontier_settings(tmp_path, deepseek_api_key=""), NoCallClient())
    record = run_review(frontier)
    assert record["status"] == "unavailable" and "DEEPSEEK_API_KEY" in record["message"]


def test_not_triggered_makes_no_call(tmp_path):
    record = run_review(service(frontier_settings(tmp_path), NoCallClient()), triggers=[])
    assert record["status"] == "not_triggered" and record["totals"]["calls"] == 0


def test_b04_f07_token_totals_include_every_attempt(tmp_path):
    """Percobaan 1 JSON cacat (tetap ditagih 100/50), percobaan 2 sah (100/50): total 200/100."""
    client = ScriptedClient([completion("{rusak"), completion(review_json(verdict()))])
    record = run_review(service(frontier_settings(tmp_path), client))
    call = record["calls"][0]
    assert call["attempts"] == 2 and call["status"] == "completed"
    assert call["usage"]["prompt_tokens"] == 200 and call["usage"]["completion_tokens"] == 100
    assert record["totals"]["prompt_tokens"] == 200 and record["totals"]["completion_tokens"] == 100
    assert record["totals"]["attempts"] == 2
    assert record["totals"]["cost_usd_estimate"] > 0


def test_u03_cache_hit_adds_no_new_tokens_or_cost(tmp_path):
    settings = frontier_settings(tmp_path)
    run_review(service(settings, ScriptedClient([completion(review_json(verdict()))])))
    record = run_review(service(settings, NoCallClient()), run_key="run-2")
    call = record["calls"][0]
    assert call["cached"] and call["usage"] is None and call["cached_usage"]["prompt_tokens"] == 100
    assert record["totals"]["prompt_tokens"] == 0 and record["totals"]["completion_tokens"] == 0
    assert record["totals"]["cost_usd_estimate"] == 0 and record["totals"]["cache_hits"] == 1
    assert record["totals"]["calls"] == 0


def test_k03_f08_offline_replay_reads_the_cache_even_in_refresh_mode(tmp_path):
    settings = frontier_settings(tmp_path, frontier_cache_mode="refresh")
    run_review(service(settings, ScriptedClient([completion(review_json(verdict()))])))
    hit = run_review(service(settings, NoCallClient()), offline=True, run_key="replay")
    assert hit["calls"][0]["status"] == "cache_hit" and hit["status"] == "completed"
    miss = run_review(service(frontier_settings(tmp_path / "kosong", frontier_cache_mode="refresh"), NoCallClient()),
                      offline=True)
    assert miss["status"] == "offline_cache_miss"


def test_k02_offline_corrupt_cache_is_an_explicit_miss_without_network(tmp_path):
    settings = frontier_settings(tmp_path)
    run_review(service(settings, ScriptedClient([completion(review_json(verdict()))])))
    for path in (tmp_path / "frontier" / "cache").glob("*.json"):
        path.write_text("{rusak", encoding="utf-8")
    record = run_review(service(settings, NoCallClient()), offline=True)
    assert record["status"] == "offline_cache_miss"


def test_k01_cache_key_changes_with_evidence_and_effort(tmp_path):
    settings = frontier_settings(tmp_path)
    first = run_review(service(settings, ScriptedClient([completion(review_json(verdict()))])))
    changed_evidence = run_review(service(settings, NoCallClient()), offline=True,
                                  packet={"bukti": {"artikel": [{"id": "news:a", "isi": "angka berubah"}]}})
    assert changed_evidence["status"] == "offline_cache_miss"
    effort = run_review(service(frontier_settings(tmp_path, frontier_reasoning_effort="max"), NoCallClient()),
                        offline=True)
    assert effort["status"] == "offline_cache_miss"
    same = run_review(service(settings, NoCallClient()), offline=True)
    assert same["calls"][0]["cache_key"] == first["calls"][0]["cache_key"]


def test_b05_budget_stops_the_request_before_http(tmp_path):
    record = run_review(service(frontier_settings(tmp_path, frontier_max_cost_usd_total=0.001), NoCallClient()))
    assert record["status"] == "budget_exhausted" and record["calls"][0]["budget_limit"] == "cost_total"
    assert record["calls"][0]["attempts"] == 0


def test_h05_retries_are_bounded_and_counted_against_the_call_limit(tmp_path):
    busy = FrontierCallError("rate_limited", "HTTP 429", transient=True, billed="no")
    client = ScriptedClient([busy, busy, busy])
    record = run_review(service(frontier_settings(tmp_path, frontier_max_retries=3, frontier_max_calls_per_run=2),
                                client))
    assert len(client.prompts) == 2  # batas panggilan per run menghentikan retry ke-3
    assert record["status"] == "budget_exhausted"


def test_h06_timeout_is_unknown_cost_not_zero(tmp_path):
    frontier = service(frontier_settings(tmp_path), ScriptedClient([timeout_error(), timeout_error()]))
    record = run_review(frontier)
    assert record["status"] == "failed"
    assert record["totals"]["usage_unknown"] and record["totals"]["cost_usd_estimate"] is None
    assert frontier.ledger.usage("run-1")["run_cost_usd_charged"] > 0


def test_b02_identical_job_running_concurrently_is_not_sent_twice(tmp_path):
    settings = frontier_settings(tmp_path)
    gate, entered = threading.Event(), threading.Event()
    slow = ScriptedClient([completion(review_json(verdict()))], gate=gate, entered=entered)
    results = {}
    worker = threading.Thread(target=lambda: results.setdefault("a", run_review(service(settings, slow))))
    worker.start()
    assert entered.wait(5)
    results["b"] = run_review(service(settings, NoCallClient()), run_key="run-2")
    gate.set()
    worker.join(10)
    assert len(slow.prompts) == 1
    assert results["a"]["status"] == "completed" and results["b"]["status"] == "in_flight"


def test_r06_unknown_evidence_ids_or_empty_reason_are_rejected(tmp_path):
    client = ScriptedClient([completion(review_json(verdict(evidence=("news:karangan",))))])
    record = run_review(service(frontier_settings(tmp_path), client))
    item = record["verdicts"][0]
    assert item["independent"] is None and "tidak dikenal" in item["independent_invalid"]["problem"]
    client = ScriptedClient([completion(review_json(verdict(reason="  ")))])
    record = run_review(service(frontier_settings(tmp_path / "b"), client))
    assert record["verdicts"][0]["independent"] is None


def test_second_look_runs_only_for_disagreement_and_sees_its_own_reading(tmp_path):
    client = ScriptedClient([completion(review_json(verdict())), completion(review_json(verdict()))])
    record = run_review(service(frontier_settings(tmp_path), client), opinions=SPLIT)
    assert [call["step"] for call in record["calls"]] == ["independent", "second_look"]
    assert "pembanding_lokal" not in client.prompts[0]
    assert "pembacaan_independen_anda" in client.prompts[1] and "pendapat_pembanding_lokal" in client.prompts[1]
    assert record["verdicts"][0]["second_look"]["status"] == "supported"
    no_split = ScriptedClient([completion(review_json(verdict()))])
    assert len(run_review(service(frontier_settings(tmp_path / "b"), no_split), opinions=AGREE)["calls"]) == 1


def test_r04_second_look_blocked_by_budget_stays_unresolved(tmp_path):
    client = ScriptedClient([completion(review_json(verdict()))])
    record = run_review(service(frontier_settings(tmp_path, frontier_max_calls_per_run=1), client), opinions=SPLIT)
    assert record["status"] == "partial" and record["second_look_status"] == "budget_exhausted"
    decision = reconcile(local_statuses=["supported", "contradicted"], expected_reviewers=2,
                         verdict=record["verdicts"][0])
    assert decision["rule"] == "R2_frontier_unstable" and decision["final_status"] == "contradicted"


def test_oversized_prompt_is_not_sent(tmp_path):
    big = {"bukti": {"artikel": [{"id": "news:a", "isi": "x" * 30_000}]}}
    record = run_review(service(frontier_settings(tmp_path, frontier_max_input_chars=10_000), NoCallClient()),
                        packet=big)
    assert record["status"] == "failed" and "input_too_large" in record["message"]


def test_the_key_never_appears_in_the_audit_record(tmp_path):
    leak = FrontierCallError("server_error", f"HTTP 500 Bearer {FAKE_KEY}", transient=False, billed="unknown")
    record = run_review(service(frontier_settings(tmp_path), ScriptedClient([leak])))
    assert FAKE_KEY not in json.dumps(record)


def test_overshoot_is_reported_and_stops_further_calls(tmp_path):
    huge = {"prompt_tokens": 900_000, "completion_tokens": 900_000, "total_tokens": 1_800_000,
            "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 900_000}
    client = ScriptedClient([completion(review_json(verdict()), usage=huge)])
    frontier = service(frontier_settings(tmp_path, frontier_max_cost_usd_per_run=5, frontier_max_cost_usd_per_day=5,
                                         frontier_max_cost_usd_total=5, frontier_max_tokens_per_run=2_000_000),
                       client)
    record = run_review(frontier, opinions=SPLIT)
    assert record["calls"][0]["overshoot"] and record["totals"]["overshoot"]
    assert record["calls"][1]["status"] == "budget_exhausted" and record["calls"][1]["budget_limit"] == "overshoot"

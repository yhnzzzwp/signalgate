"""Pilihan GPU, cek kesiapan, aktivasi untuk run berikutnya, dan pengikatan konfigurasi per run (F01, F03, F09)."""
import json
import socket

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.runtime_routes import register_runtime_routes
from app.config import Settings
from app.runtime import EndpointError, RuntimeStore, check, diff_snapshots, normalize_endpoint, resolve
from tests.frontier_fakes import FAKE_KEY

TOKEN = "gateway-token-abcdefghijklmnopqrstuvwxyz"
COLAB = "https://abc-def.trycloudflare.com"


def public_resolver(host, port, proto=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, proto, "", ("104.16.1.2", port))]


def resolver_to(address):
    return lambda host, port, proto=0: [(socket.AF_INET, socket.SOCK_STREAM, proto, "", (address, port))]


def settings(tmp_path, **overrides):
    values = dict(runtime_directory=tmp_path / "runtime", frontier_directory=tmp_path / "frontier",
                  llm_backend="ollama", signalgate_profile="workstation")
    values.update(overrides)
    return Settings(_env_file=None, **values)


def ollama(tags=("qwen2.5:14b", "glm4:9b", "gemma3:12b"), ps=(), gpus=({"name": "Tesla T4", "memory_total_mb": 15360},),
           token=TOKEN, status=200, calls=None):
    loaded = list(ps)

    def handler(request: httpx.Request) -> httpx.Response:
        response = answer(request)
        response.headers["X-SignalGate-Gateway"] = "1"
        return response

    def answer(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append((request.method, request.url.path))
        if status != 200:
            return httpx.Response(status, text="tunnel down")
        if token and request.headers.get("authorization") != f"Bearer {token}":
            return httpx.Response(401, json={"error": "token"})
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.0"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": name} for name in tags]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": loaded})
        if request.url.path == "/gateway/info":
            return httpx.Response(200, json={"gpus": list(gpus)})
        if request.url.path == "/api/generate":
            loaded.append({"name": json.loads(request.content)["model"], "size": 8_000, "size_vram": 8_000})
            return httpx.Response(200, json={"done": True})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


# -- validasi endpoint (G02) -------------------------------------------------------------------------

def test_local_mode_needs_no_url_and_accepts_only_loopback():
    assert normalize_endpoint("local", None, default_local="http://127.0.0.1:11434") == "http://127.0.0.1:11434"
    with pytest.raises(EndpointError):
        normalize_endpoint("local", "http://192.168.1.5:11434", default_local="http://127.0.0.1:11434")


@pytest.mark.parametrize("url,expected", [
    ("https://colab.research.google.com/drive/1abc", "link halaman notebook"),
    ("http://abc.trycloudflare.com", "wajib HTTPS"),
    ("https://user:pass@abc.trycloudflare.com", "kata sandi"),
    ("https://abc.trycloudflare.com/api/tags", "tanpa path"),
    ("https://localhost:8080", "loopback"),
    ("", "wajib diisi"),
])
def test_bad_colab_urls_are_rejected_with_guidance(url, expected):
    with pytest.raises(EndpointError) as caught:
        normalize_endpoint("colab", url, default_local="http://127.0.0.1:11434", resolver=public_resolver)
    assert expected in str(caught.value)


@pytest.mark.parametrize("address", ["10.0.0.5", "192.168.1.9", "169.254.169.254", "127.0.0.1", "100.64.0.1", "::1"])
def test_remote_names_resolving_to_internal_addresses_are_refused(address):
    with pytest.raises(EndpointError):
        normalize_endpoint("colab", COLAB, default_local="", resolver=resolver_to(address))


def test_a_public_https_tunnel_is_accepted():
    assert normalize_endpoint("colab", COLAB + "/", default_local="", resolver=public_resolver) == COLAB


# -- cek kesiapan (G01, G03) --------------------------------------------------------------------------

def test_g01_colab_check_separates_connection_models_gpu_and_frontier(tmp_path):
    result = check(settings(tmp_path), target="colab", url=COLAB, token=TOKEN, resolver=public_resolver,
                   required_models=["qwen2.5:14b", "glm4:9b", "gemma3:12b"], transport=ollama())
    assert result["backend"]["ok"] and result["ollama"]["status"] == "connected"
    assert result["models"]["missing"] == [] and result["gpu"]["status"] == "proven"
    assert result["frontier"]["enabled"] is False and result["ready_for_next_run"]
    assert TOKEN not in json.dumps(result)


def test_missing_model_and_cpu_only_are_reported_not_hidden(tmp_path):
    result = check(settings(tmp_path), target="colab", url=COLAB, token=TOKEN, resolver=public_resolver,
                   required_models=["qwen2.5:14b", "gemma3:12b"], transport=ollama(tags=("qwen2.5:14b",), gpus=()))
    assert result["models"]["missing"] == ["gemma3:12b"] and not result["ready_for_next_run"]
    assert result["gpu"]["status"] == "not_proven"


def test_wrong_token_and_expired_tunnel_are_distinct(tmp_path):
    wrong = check(settings(tmp_path), target="colab", url=COLAB, token="salah-" + TOKEN, resolver=public_resolver,
                  required_models=[], transport=ollama())
    assert wrong["ollama"]["status"] == "unauthorized"
    expired = check(settings(tmp_path), target="colab", url=COLAB, token=TOKEN, resolver=public_resolver,
                    required_models=[], transport=ollama(status=530))
    assert expired["ollama"]["status"] == "disconnected" and "tidak otomatis pindah" in expired["ollama"]["error"]


def test_local_gpu_is_unknown_until_a_model_is_loaded_and_the_probe_proves_it(tmp_path):
    calls = []
    unknown = check(settings(tmp_path), target="local", url=None, token="", required_models=["gemma3:12b"],
                    transport=ollama(token="", calls=calls))
    assert unknown["gpu"]["status"] == "unknown" and ("POST", "/api/generate") not in calls
    probed = check(settings(tmp_path), target="local", url=None, token="", required_models=["gemma3:12b"],
                   probe_gpu=True, transport=ollama(token=""))
    assert probed["gpu"]["status"] == "proven" and probed["gpu"]["probe_model"] == "gemma3:12b"


def test_frontier_key_check_never_generates(tmp_path):
    class Account:
        def account(self):
            return {"key_valid": True, "balance": {"balances": [{"currency": "USD", "total_balance": "2.00"}]}}

        def complete(self, *_args):
            raise AssertionError("cek kesiapan tidak boleh memanggil generasi berbayar")

    result = check(settings(tmp_path, frontier_enabled=True, deepseek_api_key=FAKE_KEY), target="local", url=None,
                   token="", required_models=[], transport=ollama(token=""), check_frontier_key=True,
                   frontier_client=Account())
    assert result["frontier"]["account"]["key_valid"] and FAKE_KEY not in json.dumps(result)


# -- API aktivasi (F01) ------------------------------------------------------------------------------

def app_for(tmp_path, transport, **overrides):
    base = settings(tmp_path, **overrides)
    store = RuntimeStore(base.runtime_directory)
    app = FastAPI()
    register_runtime_routes(app, base, store, lambda: {"id": "job-aktif"}, transport=transport,
                            resolver=public_resolver)
    return app, store, base


def test_activation_applies_to_the_next_run_and_never_returns_the_token(tmp_path):
    app, store, base = app_for(tmp_path, ollama())
    client = TestClient(app, client=("127.0.0.1", 50000))
    before = resolve(base, store)
    response = client.post("/runtime/activate", json={"target": "colab", "colab_url": COLAB, "token": TOKEN,
                                                      "frontier_mode": "off"})
    assert response.status_code == 200, response.text
    assert TOKEN not in response.text and response.json()["state"]["token_configured"]
    assert response.json()["active_run"] == {"id": "job-aktif"}
    assert before.settings.ollama_base_url == base.ollama_base_url  # salinan run berjalan tidak berubah (G04)
    after = resolve(base, store)
    assert after.settings.ollama_base_url == COLAB and after.settings.ollama_auth_token.get_secret_value() == TOKEN
    assert after.snapshot["target"] == "colab" and TOKEN not in json.dumps(after.snapshot)
    assert (tmp_path / "runtime" / "secrets.json").stat().st_mode & 0o777 == 0o600
    assert TOKEN not in client.get("/runtime/config").text


def test_activation_is_refused_when_the_endpoint_is_down_and_nothing_changes(tmp_path):
    app, store, _base = app_for(tmp_path, ollama(status=530))
    client = TestClient(app, client=("127.0.0.1", 50000))
    response = client.post("/runtime/activate", json={"target": "colab", "colab_url": COLAB, "token": TOKEN,
                                                      "frontier_mode": "off"})
    assert response.status_code == 409 and store.state().revision == 0


def test_notebook_link_and_remote_callers_are_refused(tmp_path):
    app, _store, _base = app_for(tmp_path, ollama())
    local = TestClient(app, client=("127.0.0.1", 50000))
    bad = local.post("/runtime/activate", json={"target": "colab", "frontier_mode": "off",
                                               "colab_url": "https://colab.research.google.com/drive/x"})
    assert bad.status_code == 422 and "notebook" in bad.json()["detail"]
    remote = TestClient(app, client=("203.0.113.9", 50000))
    assert remote.post("/runtime/check", json={"target": "local"}).status_code == 403
    foreign = local.post("/runtime/check", json={"target": "local"}, headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403


def test_frontier_mode_needs_a_backend_key_and_is_applied_when_present(tmp_path):
    app, _store, _base = app_for(tmp_path, ollama(token=""))
    client = TestClient(app, client=("127.0.0.1", 50000))
    refused = client.post("/runtime/activate", json={"target": "local", "frontier_mode": "shadow"})
    assert refused.status_code == 422 and "DEEPSEEK_API_KEY" in refused.json()["detail"]
    app, store, base = app_for(tmp_path / "k", ollama(token=""), deepseek_api_key=FAKE_KEY)
    client = TestClient(app, client=("127.0.0.1", 50000))
    assert client.post("/runtime/activate", json={"target": "local", "frontier_mode": "shadow"}).status_code == 200
    effective = resolve(base, store).settings
    assert effective.frontier_enabled and effective.frontier_mode == "shadow"
    assert FAKE_KEY not in client.get("/runtime/config").text


# -- precedence dan diff (C03, F03) -------------------------------------------------------------------

def test_c03_explicit_model_beats_the_profile_on_both_paths(tmp_path):
    snapshot = resolve(settings(tmp_path, ollama_model="qwen2.5:7b"), None).snapshot
    assert snapshot["workflow"]["analyst"] == "qwen2.5:7b" and snapshot["screening"]["analyst"] == "qwen2.5:7b"
    assert snapshot["workflow"]["reviewers"] == ["glm4:9b", "gemma3:12b"]
    assert snapshot["screening"]["reviewers"] == ["glm4:9b", "gemma3:12b"]


def test_switching_gpu_location_never_touches_keys_mode_or_limits(tmp_path):
    base = settings(tmp_path, deepseek_api_key=FAKE_KEY, frontier_enabled=True, frontier_max_cost_usd_total=1.23)
    store = RuntimeStore(base.runtime_directory)
    store.save(target="colab", colab_url=COLAB, frontier_mode=None, token=TOKEN)
    effective = resolve(base, store).settings
    assert effective.deepseek_api_key.get_secret_value() == FAKE_KEY and effective.frontier_enabled
    assert effective.frontier_max_cost_usd_total == 1.23
    store.save(target="local", colab_url=None, frontier_mode=None, token=None)
    assert resolve(base, store).settings.ollama_base_url == base.ollama_local_url


def test_decision_relevant_differences_are_listed():
    before = {"target": "colab", "ollama_url": COLAB, "frontier": {"mode": "shadow", "enabled": True}}
    after = {"target": "colab", "ollama_url": "https://baru.trycloudflare.com",
             "frontier": {"mode": "escalation", "enabled": True}}
    fields = {item["field"] for item in diff_snapshots(before, after)}
    assert fields == {"ollama_url", "frontier.mode"}


def test_the_run_scripts_no_longer_copy_env_files():
    from pathlib import Path

    for name in ("run_local.sh", "run_colab.sh"):
        script = Path(__file__).resolve().parents[1] / "scripts_local" / name
        if script.exists():
            text = script.read_text(encoding="utf-8")
            assert "cp .env" not in text and ".env.local .env" not in text and ".env.colab .env" not in text


def test_secret_token_is_not_in_settings_repr():
    assert TOKEN not in repr(Settings(_env_file=None, ollama_auth_token=SecretStr(TOKEN)))


# -- temuan QA 28 Sep: token terikat endpoint, model tak terbaca, aktivasi belum siap, probe saat run -------

OTHER = "https://lain-host.trycloudflare.com"


def recording(transport_status=200, tags_status=200, tags=("qwen2.5:14b", "glm4:9b", "gemma3:12b"), token=TOKEN):
    """Transport Ollama palsu yang mencatat host, path, dan header Authorization setiap request."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        response = answer(request)
        response.headers["X-SignalGate-Gateway"] = "1"
        return response

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.method, request.url.path, request.headers.get("authorization")))
        if request.headers.get("authorization") != f"Bearer {token}":
            return httpx.Response(401, json={"error": "token"})
        if request.url.path == "/api/version":
            return httpx.Response(transport_status, json={"version": "0.12.0"})
        if request.url.path == "/api/tags":
            if tags_status != 200:
                return httpx.Response(tags_status, text="<html>Service Unavailable</html>")
            return httpx.Response(200, json={"models": [{"name": name} for name in tags]})
        if request.url.path == "/gateway/info":
            return httpx.Response(200, json={"gpus": [{"name": "Tesla T4", "memory_total_mb": 15360}]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"done": True})
        return httpx.Response(404)

    return httpx.MockTransport(handler), seen


def routes(tmp_path, transport, *, active=None, free=True):
    from contextlib import contextmanager

    base = settings(tmp_path)
    store = RuntimeStore(base.runtime_directory)

    @contextmanager
    def exclusive():
        yield free

    app = FastAPI()
    register_runtime_routes(app, base, store, lambda: active, exclusive=exclusive, transport=transport,
                            resolver=public_resolver)
    return TestClient(app, client=("127.0.0.1", 50000)), store, base


def test_p1_saved_token_is_never_sent_to_a_different_host(tmp_path):
    transport, seen = recording()
    client, store, base = routes(tmp_path, transport)
    store.save(target="colab", colab_url=COLAB, frontier_mode="off", token=TOKEN, ready=True)
    result = client.post("/runtime/check", json={"target": "colab", "colab_url": OTHER}).json()
    assert result["token_withheld"] and "terikat" in result["warnings"][0]
    assert all(auth is None for host, _method, _path, auth in seen if host == "lain-host.trycloudflare.com")
    revision = store.state().revision
    refused = client.post("/runtime/activate", json={"target": "colab", "colab_url": OTHER, "frontier_mode": "off"})
    assert refused.status_code == 422 and store.state().revision == revision
    assert TOKEN not in refused.text
    assert resolve(base, store).settings.ollama_base_url == COLAB  # konfigurasi lama tetap berlaku


def test_p1_the_same_host_keeps_using_the_saved_token(tmp_path):
    transport, seen = recording()
    client, store, _base = routes(tmp_path, transport)
    store.save(target="colab", colab_url=COLAB, frontier_mode="off", token=TOKEN, ready=True)
    result = client.post("/runtime/check", json={"target": "colab", "colab_url": COLAB + "/"}).json()
    assert result["ollama"]["status"] == "connected" and not result.get("token_withheld")
    assert seen and all(auth == f"Bearer {TOKEN}" for _host, _method, _path, auth in seen)


def test_p1_a_new_host_needs_a_new_token_or_explicit_reuse(tmp_path):
    transport, _seen = recording()
    client, store, base = routes(tmp_path, transport)
    store.save(target="colab", colab_url=COLAB, frontier_mode="off", token=TOKEN, ready=True)
    reused = client.post("/runtime/activate", json={"target": "colab", "colab_url": OTHER, "frontier_mode": "off",
                                                    "reuse_saved_token": True})
    assert reused.status_code == 200 and store.token_endpoint() == OTHER
    assert resolve(base, store).settings.ollama_auth_token.get_secret_value() == TOKEN
    fresh = "token-baru-dari-notebook-" + "x" * 20
    transport, _seen = recording(token=fresh)
    client, store, base = routes(tmp_path / "baru", transport)
    store.save(target="colab", colab_url=COLAB, frontier_mode="off", token=TOKEN, ready=True)
    assert client.post("/runtime/activate", json={"target": "colab", "colab_url": OTHER, "frontier_mode": "off",
                                                  "token": fresh}).status_code == 200
    assert store.token_endpoint() == OTHER and store.token() == fresh


def test_p1_mismatched_or_legacy_tokens_are_not_used_for_a_run(tmp_path):
    base = settings(tmp_path)
    store = RuntimeStore(base.runtime_directory)
    store.save(target="colab", colab_url=COLAB, frontier_mode=None, token=TOKEN)
    (tmp_path / "runtime" / "secrets.json").write_text(json.dumps({"colab_token": TOKEN}), encoding="utf-8")
    assert resolve(base, store).settings.ollama_auth_token.get_secret_value() == ""
    env_token = settings(tmp_path / "env", ollama_auth_token=TOKEN)
    env_store = RuntimeStore(env_token.runtime_directory)
    env_store.save(target="colab", colab_url=OTHER, frontier_mode=None, token=None)
    assert resolve(env_token, env_store).settings.ollama_auth_token.get_secret_value() == ""


def test_p2_an_unreadable_model_list_is_not_ready(tmp_path):
    transport, _seen = recording(tags_status=503)
    result = check(settings(tmp_path), target="colab", url=COLAB, token=TOKEN, resolver=public_resolver,
                   required_models=["qwen2.5:14b"], transport=transport)
    assert result["ollama"]["ok"] and result["models"]["status"] == "unreadable"
    assert result["models"]["ok"] is False and result["ready_for_next_run"] is False
    assert "HTTP 503" in result["models"]["error"]


def test_p2_activation_is_refused_until_ready_unless_explicitly_forced(tmp_path):
    transport, _seen = recording(tags=())
    client, store, _base = routes(tmp_path, transport)
    body = {"target": "colab", "colab_url": COLAB, "token": TOKEN, "frontier_mode": "off"}
    refused = client.post("/runtime/activate", json=body)
    assert refused.status_code == 409 and refused.json()["detail"]["can_force"] is True
    assert set(refused.json()["detail"]["check"]["models"]["missing"]) == {"qwen2.5:14b", "glm4:9b", "gemma3:12b"}
    assert store.state().revision == 0
    forced = client.post("/runtime/activate", json={**body, "allow_not_ready": True})
    assert forced.status_code == 200 and forced.json()["saved_not_ready"] is True
    assert store.state().ready_at_activation is False
    unreadable, _seen = recording(tags_status=503)
    client, store, _base = routes(tmp_path / "u", unreadable)
    assert client.post("/runtime/activate", json=body).status_code == 409 and store.state().revision == 0


def test_p2_gpu_probe_is_refused_while_an_analysis_runs(tmp_path):
    transport, seen = recording()
    body = {"target": "colab", "colab_url": COLAB, "token": TOKEN, "probe_gpu": True}
    busy, _store, _base = routes(tmp_path, transport, active={"id": "job-1", "kind": "workflow"})
    assert busy.post("/runtime/check", json=body).status_code == 409
    locked, _store, _base = routes(tmp_path / "l", transport, free=False)
    assert locked.post("/runtime/check", json=body).status_code == 409
    assert not any(path == "/api/generate" for _host, _method, path, _auth in seen)
    idle, _store, _base = routes(tmp_path / "i", transport)
    assert idle.post("/runtime/check", json=body).status_code == 200
    assert any(path == "/api/generate" for _host, _method, path, _auth in seen)
    # Cek tanpa probe tetap boleh saat run berjalan.
    assert busy.post("/runtime/check", json={**body, "probe_gpu": False}).status_code == 200


def test_p2_main_holds_the_run_lock_during_a_probe():
    import app.main as main

    with main._machine_free() as free:
        assert free is True
        with main._machine_free() as second:
            assert second is False  # job/probe lain ditolak selama probe
    assert main.run_lock.acquire(blocking=False)
    main.run_lock.release()


def test_a_colab_url_answered_by_another_service_is_named_as_such(tmp_path):
    """Kasus nyata 28 Sep: gateway gagal bind port 8080, tunnel meneruskan ke Jupyter Server Colab (404 HTML)."""
    jupyter = httpx.MockTransport(lambda request: httpx.Response(
        404, headers={"content-type": "text/html", "server": "cloudflare"}, text="<title>Jupyter Server</title>"))
    result = check(settings(tmp_path), target="colab", url=COLAB, token=TOKEN, resolver=public_resolver,
                   required_models=["gemma3:12b"], transport=jupyter)
    assert result["ollama"]["status"] == "not_gateway" and "bukan gateway SignalGate" in result["ollama"]["error"]
    assert not result["ready_for_next_run"]
    unauthorized_elsewhere = httpx.MockTransport(lambda request: httpx.Response(401, text="login"))
    other = check(settings(tmp_path), target="colab", url=COLAB, token=TOKEN, resolver=public_resolver,
                  required_models=[], transport=unauthorized_elsewhere)
    assert other["ollama"]["status"] == "not_gateway"  # 401 tanpa penanda bukan "token salah"


def test_local_ollama_does_not_need_the_gateway_marker(tmp_path):
    plain = httpx.MockTransport(lambda request: httpx.Response(200, json={"version": "0.12.0"})
                                if request.url.path == "/api/version" else
                                httpx.Response(200, json={"models": [{"name": "gemma3:12b"}]}))
    result = check(settings(tmp_path), target="local", url=None, token="", required_models=["gemma3:12b"],
                   transport=plain)
    assert result["ollama"]["status"] == "connected"

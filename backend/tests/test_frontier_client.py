"""Klien HTTP DeepSeek dengan transport palsu: H01-H07 dan cek akun tanpa generasi."""
import json

import httpx
import pytest

from app.frontier.client import DeepSeekClient, FrontierCallError, parse_json_object, redact
from tests.frontier_fakes import FAKE_KEY


def client_with(handler) -> tuple[DeepSeekClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return DeepSeekClient(FAKE_KEY, transport=httpx.MockTransport(record)), seen


def chat(content, finish="stop", reasoning="jejak penalaran rahasia", usage=True):
    body = {"id": "req-1", "model": "deepseek-flash",
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": content, "reasoning_content": reasoning}}]}
    if usage:
        body["usage"] = {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200,
                         "prompt_cache_hit_tokens": 20, "prompt_cache_miss_tokens": 100,
                         "completion_tokens_details": {"reasoning_tokens": 30}}
    return httpx.Response(200, json=body)


def test_h01_valid_json_returns_only_final_content_and_sends_the_verified_contract():
    client, seen = client_with(lambda request: chat('{"verdicts": []}'))
    raw = client.complete("sistem json", "pengguna json")
    assert raw.content == '{"verdicts": []}'
    assert "penalaran" not in json.dumps(raw.__dict__)
    assert raw.usage["reasoning_tokens"] == 30 and raw.usage["prompt_cache_hit_tokens"] == 20
    body = json.loads(seen[0].content)
    assert body["model"] == "deepseek-flash"
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "high"
    assert body["response_format"] == {"type": "json_object"}
    assert "temperature" not in body and body["stream"] is False
    assert seen[0].headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert seen[0].url.path == "/chat/completions"


@pytest.mark.parametrize("content", ["", "   ", None])
def test_h02_empty_content_is_a_transient_error(content):
    client, _ = client_with(lambda request: chat(content))
    with pytest.raises(FrontierCallError) as caught:
        client.complete("s", "u")
    assert caught.value.kind == "empty" and caught.value.transient and caught.value.usage


def test_h02_malformed_json_is_rejected_by_the_parser():
    with pytest.raises(ValueError):
        parse_json_object('{"verdicts": [}')
    with pytest.raises(ValueError):
        parse_json_object("[1, 2]")
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_h03_truncated_answer_is_not_retried_and_not_used():
    client, _ = client_with(lambda request: chat('{"verdicts": [', finish="length"))
    with pytest.raises(FrontierCallError) as caught:
        client.complete("s", "u")
    assert caught.value.kind == "truncated" and not caught.value.transient and caught.value.billed == "yes"


@pytest.mark.parametrize("status,kind", [(401, "auth_failed"), (402, "insufficient_balance"),
                                         (422, "invalid_parameters"), (400, "bad_request")])
def test_h04_client_errors_are_final_and_never_echo_the_key(status, kind):
    client, _ = client_with(lambda request: httpx.Response(status, json={"error": {"message": f"key {FAKE_KEY} salah"}}))
    with pytest.raises(FrontierCallError) as caught:
        client.complete("s", "u")
    assert caught.value.kind == kind and not caught.value.transient and caught.value.billed == "no"
    assert FAKE_KEY not in str(caught.value)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_h05_rate_limit_and_server_errors_are_transient(status):
    client, _ = client_with(lambda request: httpx.Response(status, json={"error": {"message": "sibuk"}}))
    with pytest.raises(FrontierCallError) as caught:
        client.complete("s", "u")
    assert caught.value.transient
    assert caught.value.billed == ("no" if status == 429 else "unknown")


def test_h06_timeout_after_sending_has_unknown_billing():
    def slow(request):
        raise httpx.ReadTimeout("lambat", request=request)

    client, _ = client_with(slow)
    with pytest.raises(FrontierCallError) as caught:
        client.complete("s", "u")
    assert caught.value.kind == "timeout" and caught.value.billed == "unknown" and caught.value.usage is None


def test_connection_refused_is_not_billed():
    def refused(request):
        raise httpx.ConnectError("tidak tersambung", request=request)

    client, _ = client_with(refused)
    with pytest.raises(FrontierCallError) as caught:
        client.complete("s", "u")
    assert caught.value.billed == "no" and caught.value.transient


@pytest.mark.parametrize("payload", [{"id": "x"}, {"choices": []}, {"choices": "rusak"}, ["bukan", "objek"]])
def test_h07_malformed_structure_is_a_controlled_error(payload):
    client, _ = client_with(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(FrontierCallError) as caught:
        client.complete("s", "u")
    assert caught.value.kind == "invalid_response"


def test_empty_key_never_sends_a_request():
    seen = []
    client = DeepSeekClient("", transport=httpx.MockTransport(lambda request: seen.append(request)))
    with pytest.raises(FrontierCallError) as caught:
        client.complete("s", "u")
    assert caught.value.kind == "config" and seen == []


def test_account_check_uses_metadata_endpoints_only():
    def handler(request):
        if request.url.path == "/user/balance":
            return httpx.Response(200, json={"is_available": True, "balance_infos": [
                {"currency": "USD", "total_balance": "1.98", "granted_balance": "0.00", "topped_up_balance": "1.98"}]})
        if request.url.path == "/models":
            return httpx.Response(200, json={"object": "list", "data": [
                {"id": "deepseek-flash", "object": "model", "owned_by": "deepseek", "name": "DeepSeek-V4.1-Flash"}]})
        raise AssertionError(f"endpoint berbayar dipanggil: {request.url.path}")

    client, seen = client_with(handler)
    account = client.account()
    assert account["key_valid"] is True and account["model_available"] is True
    assert account["model_name"] == "DeepSeek-V4.1-Flash"
    assert account["balance"]["balances"][0]["total_balance"] == "1.98"
    assert {request.url.path for request in seen} == {"/user/balance", "/models"}


def test_account_check_reports_a_rejected_key():
    client, _ = client_with(lambda request: httpx.Response(401, json={"error": {"message": "invalid"}}))
    assert client.account()["key_valid"] is False


def test_redaction_removes_keys_and_bearer_tokens():
    text = f"Authorization: Bearer {FAKE_KEY} dan sk-abcdefghijklmnop"
    cleaned = redact(text, (FAKE_KEY,))
    assert FAKE_KEY not in cleaned and "sk-abcdefghijklmnop" not in cleaned
    assert FAKE_KEY not in repr(DeepSeekClient(FAKE_KEY))

"""Local runtime, model readiness, GPU checks and configuration isolation."""
from contextlib import contextmanager
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.runtime_routes import register_runtime_routes
from app.config import Settings
from app.runtime import EndpointError, RuntimeStore, check, diff_snapshots, normalize_endpoint, resolve, _gpu_from_ps

MODELS = ['qwen2.5:14b', 'glm4:9b', 'gemma3:12b']


def settings(**overrides):
    return Settings(_env_file=None, llm_backend='ollama', signalgate_profile='workstation', **overrides)


def transport(*, models=MODELS, tags_status=200, version_status=200, gpu=False):
    seen = []
    loaded = []
    def handler(request):
        seen.append((request.method, request.url.path))
        assert 'authorization' not in request.headers
        if request.url.path == '/api/version':
            return httpx.Response(version_status, json={'version': 'test'})
        if request.url.path == '/api/tags':
            return httpx.Response(tags_status, json={'models': [{'name': name} for name in models]})
        if request.url.path == '/api/ps':
            return httpx.Response(200, json={'models': loaded})
        if request.url.path == '/api/generate':
            body = json.loads(request.content)
            if body.get('keep_alive') == 0:
                loaded.clear()
            else:
                loaded.append({'name': body['model'], 'size': 8000, 'size_vram': 8000 if gpu else 0})
            return httpx.Response(200, json={'done': True})
        return httpx.Response(404)
    return httpx.MockTransport(handler), seen


def probe(mock, **kwargs):
    return check(settings(), target='local', url=None, token='', required_models=MODELS,
                 transport=mock, **kwargs)


@pytest.mark.parametrize('url', ['http://127.0.0.1:11434', 'http://localhost:11434', 'http://[::1]:11434'])
def test_loopback_endpoints(url):
    assert normalize_endpoint('local', url+'/', default_local='') == url


@pytest.mark.parametrize('url', ['https://example.com', 'http://192.168.1.2:11434', 'http://localhost:bad',
                                  'http://user:pass@localhost', 'http://localhost/path', 'http://localhost?x=1'])
def test_nonlocal_and_malformed_endpoints_are_rejected(url):
    with pytest.raises(EndpointError):
        normalize_endpoint('local', url, default_local='')


def test_local_gpu_unknown_without_loading():
    mock, seen = transport()
    result = probe(mock)
    assert result['ready_for_next_run']
    assert result['gpu']['status'] == 'unknown'
    assert ('POST', '/api/generate') not in seen


@pytest.mark.parametrize('gpu,status', [(False, 'not_proven'), (True, 'proven')])
def test_cpu_is_ready_and_gpu_probe_identifies_placement(gpu, status):
    mock, seen = transport(gpu=gpu)
    result = probe(mock, probe_gpu=True)
    assert result['ready_for_next_run']
    assert result['gpu']['status'] == status
    assert ('POST', '/api/generate') in seen


def test_mixed_cpu_gpu_is_not_reported_as_full_gpu():
    result = _gpu_from_ps({'models': [{'name': 'a', 'size': 8, 'size_vram': 8},
                                    {'name': 'b', 'size': 8, 'size_vram': 0}]})
    assert 'Sebagian' in result['detail']


@pytest.mark.parametrize('options,status', [({'models': []}, 'read'), ({'tags_status': 503}, 'unreadable')])
def test_missing_or_unreadable_models_are_not_ready(options, status):
    mock, _ = transport(**options)
    result = probe(mock)
    assert not result['ready_for_next_run']
    assert result['models']['status'] == status


def test_unreachable_ollama_is_not_ready():
    mock, _ = transport(version_status=503)
    assert not probe(mock)['ready_for_next_run']


def routes(tmp_path, *, active=False, free=True, **options):
    mock, seen = transport(**options)
    app = FastAPI()
    store = RuntimeStore(tmp_path)
    @contextmanager
    def exclusive():
        yield free
    register_runtime_routes(app, settings(local_only=True), store, lambda: {'id': 'job'} if active else None,
                            exclusive=exclusive, transport=mock)
    return TestClient(app, client=('127.0.0.1', 12345)), store, seen


def test_activation_requires_models_unless_forced(tmp_path):
    client, store, _ = routes(tmp_path, models=[])
    body = {'target': 'local', 'frontier_mode': 'off'}
    assert client.post('/runtime/activate', json=body).status_code == 409
    assert store.state().revision == 0
    response = client.post('/runtime/activate', json={**body, 'allow_not_ready': True})
    assert response.status_code == 200
    assert response.json()['saved_not_ready']
    assert store.state().ready_at_activation is False


def test_disconnected_activation_cannot_be_forced(tmp_path):
    client, store, _ = routes(tmp_path, version_status=503)
    assert client.post('/runtime/activate', json={'allow_not_ready': True}).status_code == 409
    assert store.state().revision == 0


@pytest.mark.parametrize('active,free', [(True, True), (False, False)])
def test_gpu_probe_respects_active_job_and_lock(tmp_path, active, free):
    client, _, seen = routes(tmp_path, active=active, free=free)
    assert client.post('/runtime/check', json={'probe_gpu': True}).status_code == 409
    assert ('POST', '/api/generate') not in seen
    assert client.post('/runtime/check', json={}).status_code == 200


def test_runtime_operator_origin_and_local_source(tmp_path):
    client, _, _ = routes(tmp_path)
    assert client.post('/runtime/check', json={}, headers={'Origin': 'https://example.com'}).status_code == 403
    assert client.post('/runtime/check', json={}, headers={'Origin': 'http://localhost:8000'}).status_code == 200


def test_config_is_copied_and_tokens_are_never_forwarded(tmp_path):
    base = settings(local_only=True, frontier_enabled=True, ollama_auth_token='private')
    store = RuntimeStore(tmp_path)
    store.save(frontier_mode='escalation')
    result = resolve(base, store)
    assert base.frontier_enabled and not result.settings.frontier_enabled
    assert result.settings.ollama_auth_token.get_secret_value() == ''
    assert 'private' not in json.dumps(result.snapshot)
    changed = resolve(base.model_copy(update={'ollama_local_url': 'http://localhost:11435'}), store)
    assert 'ollama_url' in {row['field'] for row in diff_snapshots(result.snapshot, changed.snapshot)}


def test_main_holds_job_lock_during_probe():
    import app.main as main
    with main._machine_free() as free:
        assert free
        with main._machine_free() as second:
            assert not second
    assert main.run_lock.acquire(blocking=False)
    main.run_lock.release()

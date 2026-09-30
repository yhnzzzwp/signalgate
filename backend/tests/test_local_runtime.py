from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.runtime_routes import register_runtime_routes
from app.config import REPO_ROOT, Settings
from app.runtime import RuntimeStore, resolve


def test_relative_database_is_independent_of_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None, signalgate_db_path="./signalgate.db")
    assert Path(settings.signalgate_db_path) == REPO_ROOT / "backend" / "signalgate.db"
    assert Settings(_env_file=None, signalgate_db_path=":memory:").signalgate_db_path == ":memory:"


def test_local_runtime_overrides_saved_cloud_without_changing_it(tmp_path):
    store = RuntimeStore(tmp_path)
    store.save(target="colab", colab_url="https://example.com", frontier_mode="escalation", token=None, ready=True)
    base = Settings(_env_file=None, local_only=True, frontier_enabled=True,
                    ollama_base_url="https://example.com", ollama_auth_token="private-token")
    effective = resolve(base, store)
    assert effective.settings.ollama_base_url == "http://127.0.0.1:11434"
    assert not effective.settings.frontier_enabled
    assert not effective.settings.ollama_auth_token.get_secret_value()
    assert store.state().target == "colab"
    assert base.frontier_enabled


@pytest.mark.parametrize("path,body", [
    ("check", {"target": "colab", "colab_url": "https://example.com"}),
    ("check", {"target": "local", "check_frontier_key": True}),
    ("activate", {"target": "local", "frontier_mode": "escalation"}),
    ("activate", {"target": "colab", "frontier_mode": "off"}),
])
def test_local_mode_refuses_cloud_before_network(tmp_path, path, body):
    app = FastAPI()
    def unexpected(request):
        pytest.fail("Cloud request must be rejected before network access")
    register_runtime_routes(app, Settings(_env_file=None, local_only=True), RuntimeStore(tmp_path),
                            lambda: None, transport=httpx.MockTransport(unexpected))
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.post(f"/runtime/{path}", json=body,
                               headers={"Origin": "http://127.0.0.1:8000"})
    assert response.status_code == 422


def test_built_dashboard_and_api_share_the_server():
    if not (REPO_ROOT / "frontend" / "dist" / "index.html").exists():
        pytest.skip("Frontend build is checked separately in CI")
    from app.main import app
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/missing-resource").status_code == 404

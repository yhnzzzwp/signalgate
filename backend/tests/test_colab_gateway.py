"""Gateway autentikasi Colab (F02) diuji dengan Ollama palsu di loopback; tidak ada tunnel atau internet."""
import http.client
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOKEN = "token-gateway-yang-cukup-panjang-123456"


def load_gateway():
    spec = importlib.util.spec_from_file_location("ollama_gateway", REPO / "colab" / "ollama_gateway.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeOllama(BaseHTTPRequestHandler):
    hits: list = []

    def log_message(self, *_args):
        pass

    def do_GET(self):  # noqa: N802
        FakeOllama.hits.append(("GET", self.path, self.headers.get("Host"), self.headers.get("Authorization")))
        body = json.dumps({"models": [{"name": "gemma3:12b"}]} if self.path == "/api/tags" else {"version": "x"})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        FakeOllama.hits.append(("POST", self.path, self.headers.get("Host"), self.headers.get("Authorization")))
        lines = [json.dumps({"message": {"content": part}, "done": False}) for part in ("a", "b", "c")]
        lines.append(json.dumps({"message": {"content": ""}, "done": True, "done_reason": "stop"}))
        payload = ("\n".join(lines) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture()
def gateway():
    FakeOllama.hits = []
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    module = load_gateway()
    server = module.make_server("127.0.0.1", 0, TOKEN, ("127.0.0.1", upstream.server_address[1]),
                                gpus=lambda: [{"name": "Tesla T4", "memory_total_mb": 15360}])
    threads = [threading.Thread(target=item.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
               for item in (upstream, server)]
    for thread in threads:
        thread.start()
    yield server.server_address[1]
    for item in (server, upstream):
        item.shutdown()
        item.server_close()


def call(port, method, path, token=None, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, data


def test_requests_without_or_with_a_wrong_token_are_refused(gateway):
    assert call(gateway, "GET", "/api/tags")[0] == 401
    assert call(gateway, "GET", "/api/tags", token="salah")[0] == 401
    assert FakeOllama.hits == []


def test_the_right_token_reads_models_and_streams_inference(gateway):
    status, data = call(gateway, "GET", "/api/tags", token=TOKEN)
    assert status == 200 and json.loads(data)["models"][0]["name"] == "gemma3:12b"
    status, data = call(gateway, "POST", "/api/chat", token=TOKEN, body={"model": "gemma3:12b"})
    lines = [json.loads(line) for line in data.decode().splitlines() if line]
    assert status == 200 and lines[-1]["done"] and [line["message"]["content"] for line in lines[:3]] == ["a", "b", "c"]
    # Token tidak diteruskan ke Ollama, dan Host diganti ke localhost supaya Ollama menerimanya.
    assert all(hit[3] is None and hit[2].startswith("localhost:") for hit in FakeOllama.hits)


@pytest.mark.parametrize("method,path", [("POST", "/api/pull"), ("DELETE", "/api/delete"), ("POST", "/api/create"),
                                         ("POST", "/api/copy"), ("POST", "/api/push"), ("GET", "/")])
def test_management_paths_are_blocked_even_with_the_token(gateway, method, path):
    assert call(gateway, method, path, token=TOKEN, body={"model": "x"} if method != "GET" else None)[0] == 403
    assert FakeOllama.hits == []


def test_every_gateway_answer_carries_the_marker_header(gateway):
    for method, path, token in (("GET", "/api/tags", None), ("GET", "/api/tags", TOKEN), ("POST", "/api/pull", TOKEN)):
        connection = http.client.HTTPConnection("127.0.0.1", gateway, timeout=10)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        connection.request(method, path, body="{}" if method == "POST" else None, headers=headers)
        response = connection.getresponse()
        response.read()
        assert response.getheader("X-SignalGate-Gateway") == "1", (method, path, response.status)
        connection.close()


def test_gateway_info_reports_the_gpu(gateway):
    status, data = call(gateway, "GET", "/gateway/info", token=TOKEN)
    assert status == 200 and json.loads(data)["gpus"][0]["name"] == "Tesla T4"


def test_short_tokens_are_refused_at_startup():
    with pytest.raises(ValueError):
        load_gateway().make_server("127.0.0.1", 0, "pendek", ("127.0.0.1", 1))


def test_the_notebook_is_one_cell_that_embeds_the_same_gateway_and_never_exposes_ollama_directly():
    import ast

    notebook = json.loads((REPO / "colab" / "signalgate_gpu_setup.ipynb").read_text(encoding="utf-8"))
    code_cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    assert len(code_cells) == 1, "pengguna cukup mengunggah notebook dan menjalankan satu sel"
    source = "".join(code_cells[0]["source"])
    tree = ast.parse(source)  # juga memastikan sel adalah Python yang sah
    embedded = next(node.value.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                    and any(getattr(target, "id", None) == "GATEWAY_SOURCE" for target in node.targets))
    assert embedded == (REPO / "colab" / "ollama_gateway.py").read_text(encoding="utf-8")
    assert '"OLLAMA_HOST": "127.0.0.1:11434"' in source and "OLLAMA_ORIGINS" not in source
    assert "PORT = 11435" in source and ":8080" not in source  # 8080 milik Jupyter Server Colab
    assert '"--url", LOCAL' in source and "localhost:11434" not in source
    assert '["pkill", "-x", "cloudflared"]' in source  # bukan pkill -f yang bisa membunuh sel sendiri
    assert not code_cells[0].get("outputs")
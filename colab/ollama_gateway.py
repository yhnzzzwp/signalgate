"""Gateway autentikasi di depan Ollama untuk tunnel Colab SignalGate.

Tunnel Cloudflare meneruskan ke gateway ini, BUKAN ke Ollama langsung (Ollama hanya mendengar di
127.0.0.1). Setiap request wajib membawa `Authorization: Bearer <token>`; yang diteruskan hanya endpoint
yang dipakai SignalGate. Pengelolaan model (pull, delete, create, copy, push, blobs) dan endpoint lain
ditolak. Header Authorization tidak pernah dicatat ke log.

Menjalankan:  SIGNALGATE_GATEWAY_TOKEN=<token> python ollama_gateway.py --port 11435
Port 8080 sengaja dihindari: di runtime Colab port itu sudah dipakai layanan internal Colab.
File ini disalin apa adanya ke notebook colab/signalgate_gpu_setup.ipynb; test backend memastikan isinya sama.
"""
from __future__ import annotations

import argparse
import hmac
import http.client
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ALLOWED = {("GET", "/api/version"), ("GET", "/api/tags"), ("GET", "/api/ps"), ("POST", "/api/chat"),
           ("POST", "/api/generate"), ("POST", "/api/show")}
MAX_BODY = 8 * 1024 * 1024
CHUNK = 8192
MIN_TOKEN_CHARS = 24
DEFAULT_PORT = 11435
# Penanda di setiap jawaban gateway. Header `Server` tidak bisa dipakai: Cloudflare menggantinya dengan
# "cloudflare". Dengan penanda ini backend bisa membedakan gateway SignalGate dari layanan lain di URL itu.
MARKER_HEADER = "X-SignalGate-Gateway"


def gpu_info() -> list[dict]:
    """GPU yang terlihat nvidia-smi di runtime ini; kosong bila runtime tanpa GPU."""
    try:
        output = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for line in output.strip().splitlines():
        name, _, memory = line.rpartition(",")
        try:
            gpus.append({"name": name.strip(), "memory_total_mb": int(float(memory.strip()))})
        except ValueError:
            gpus.append({"name": line.strip(), "memory_total_mb": None})
    return gpus


def make_handler(token: str, upstream: tuple[str, int] = ("127.0.0.1", 11434), gpus=gpu_info):
    expected = f"Bearer {token}".encode()

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0: respons stream (NDJSON dari /api/chat) diakhiri dengan menutup koneksi.
        protocol_version = "HTTP/1.0"
        server_version = "SignalGateGateway/1"
        sys_version = ""

        def log_message(self, format, *args):  # noqa: A002 - tanda tangan dari BaseHTTPRequestHandler
            # Hanya metode, path, dan status; tidak pernah header (token) atau badan request.
            sys.stderr.write(f"gateway {self.command} {self.path.split('?', 1)[0]} {args[1] if len(args) > 1 else ''}\n")

        def end_headers(self) -> None:
            self.send_header(MARKER_HEADER, "1")
            super().end_headers()

        def _json(self, status: int, payload: dict, headers: tuple = ()) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for key, value in headers:
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            return hmac.compare_digest((self.headers.get("Authorization") or "").encode(), expected)

        def _handle(self, method: str) -> None:
            path = self.path.split("?", 1)[0]
            if not self._authorized():
                self._json(401, {"error": "token gateway salah atau tidak ada"}, (("WWW-Authenticate", "Bearer"),))
                return
            if method == "GET" and path == "/gateway/info":
                self._json(200, {"gpus": gpus(), "allowed": sorted(f"{m} {p}" for m, p in ALLOWED)})
                return
            if (method, path) not in ALLOWED:
                self._json(403, {"error": f"{method} {path} tidak diizinkan lewat gateway SignalGate"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                self._json(413, {"error": "badan request tidak sah atau terlalu besar"})
                return
            body = self.rfile.read(length) if length else None
            connection = http.client.HTTPConnection(*upstream, timeout=900)
            started = False
            try:
                # Ollama memeriksa Host; permintaan diteruskan seolah dari localhost.
                connection.request(method, path, body=body,
                                   headers={"Content-Type": self.headers.get("Content-Type") or "application/json",
                                            "Host": f"localhost:{upstream[1]}"})
                response = connection.getresponse()
                self.send_response(response.status)
                self.send_header("Content-Type", response.getheader("Content-Type") or "application/json")
                self.end_headers()
                started = True
                while True:
                    chunk = response.read1(CHUNK)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (OSError, http.client.HTTPException):
                if not started:
                    self._json(502, {"error": "Ollama di runtime ini tidak menjawab"})
            finally:
                connection.close()

        def do_GET(self) -> None:  # noqa: N802
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def do_DELETE(self) -> None:  # noqa: N802
            self._handle("DELETE")

        def do_PUT(self) -> None:  # noqa: N802
            self._handle("PUT")

        def do_HEAD(self) -> None:  # noqa: N802
            self._handle("HEAD")

    return Handler


def make_server(host: str, port: int, token: str, upstream: tuple[str, int] = ("127.0.0.1", 11434),
                gpus=gpu_info) -> ThreadingHTTPServer:
    if len(token) < MIN_TOKEN_CHARS:
        raise ValueError(f"Token gateway minimal {MIN_TOKEN_CHARS} karakter.")
    server = ThreadingHTTPServer((host, port), make_handler(token, upstream, gpus))
    server.daemon_threads = True
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Gateway autentikasi Ollama untuk SignalGate")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--upstream-port", type=int, default=11434)
    args = parser.parse_args()
    token = os.environ.get("SIGNALGATE_GATEWAY_TOKEN", "")
    try:
        server = make_server(args.host, args.port, token, ("127.0.0.1", args.upstream_port))
    except ValueError as error:
        sys.exit(str(error))
    sys.stderr.write(f"gateway siap di {args.host}:{args.port} -> Ollama 127.0.0.1:{args.upstream_port}\n")
    server.serve_forever()


if __name__ == "__main__":
    main()

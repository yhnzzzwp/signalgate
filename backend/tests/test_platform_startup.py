"""Portable startup checks executed natively by the Windows/Linux/macOS CI matrix."""
import importlib.util
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import sys
from threading import Thread
from unittest.mock import patch

import pytest

from app.config import REPO_ROOT, Settings
from app.db.session import build_engine


def launcher():
    spec = importlib.util.spec_from_file_location('signalgate_launcher', REPO_ROOT / 'run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('root,windows,expected', [
    (PureWindowsPath('C:/Users/Test User/SignalGate'), True, 'C:/Users/Test User/SignalGate/backend/.venv/Scripts/python.exe'),
    (PurePosixPath('/home/test user/SignalGate'), False, '/home/test user/SignalGate/backend/.venv/bin/python'),
    (PurePosixPath('/Users/Test User/SignalGate'), False, '/Users/Test User/SignalGate/backend/.venv/bin/python'),
])
def test_launcher_paths_with_spaces(root, windows, expected):
    assert launcher().interpreter(root, windows).as_posix() == expected


def test_launcher_uses_backend_cwd_and_propagates_failure(tmp_path, monkeypatch):
    module = launcher()
    python = tmp_path / 'path with spaces' / 'python'
    python.parent.mkdir()
    python.touch()
    monkeypatch.setattr(module, 'interpreter', lambda: python)
    monkeypatch.setattr(sys, 'argv', ['run.py', '--check'])
    with patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 7)) as run:
        assert module.main() == 7
        assert run.call_args.args[0] == [str(python), '-m', 'app.local', '--check']
        assert run.call_args.kwargs['cwd'] == REPO_ROOT / 'backend'


def test_database_handles_spaces_unicode_and_reserved_url_characters(tmp_path):
    path = tmp_path / 'data lokal #1' / 'analisis ü.sqlite'
    engine = build_engine(Settings(_env_file=None, signalgate_db_path=str(path)))
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql('SELECT 1').scalar() == 1
        assert path.is_file()
    finally:
        engine.dispose()


@pytest.mark.parametrize('present,expected', [(True, 0), (False, 1)])
def test_actual_startup_process_checks_local_models(tmp_path, present, expected):
    if not (REPO_ROOT / 'frontend/dist/index.html').exists():
        pytest.skip('Build the frontend before startup smoke tests')
    class Ollama(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == '/api/tags'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            models = [{'name': 'test:local', 'digest': 'test'}] if present else []
            self.wfile.write(json.dumps({'models': models}).encode())
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Ollama)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {**os.environ, 'OLLAMA_LOCAL_URL': f'http://127.0.0.1:{server.server_port}',
               'SIGNALGATE_PROFILE': 'laptop', 'OLLAMA_MODEL': 'test:local', 'OLLAMA_REVIEWER_MODELS': '[]',
               'OLLAMA_VALIDATOR_MODEL': '', 'WORKFLOW_ANALYST_MODEL': 'test:local',
               'WORKFLOW_REVIEWER_MODEL': '', 'WORKFLOW_REVIEWER_MODELS': '[]',
               'SIGNALGATE_DB_PATH': str(tmp_path / 'test.db'), 'RUNTIME_DIRECTORY': str(tmp_path / 'runtime')}
        # Uses this OS's real process creation, Python interpreter and network stack.
        result = subprocess.run([sys.executable, '-m', 'app.local', '--check'], cwd=REPO_ROOT / 'backend',
                                env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == expected, result.stdout + result.stderr
        assert ('Model lokal:' if present else 'belum diunduh') in result.stdout + result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_windows_batch_wrapper_returns_python_failure(tmp_path):
    if os.name != 'nt':
        pytest.skip('Native cmd.exe verification runs on Windows CI')
    # Copy the real wrapper beside a deterministic failing setup script.
    wrapper = tmp_path / 'setup.cmd'
    wrapper.write_bytes((REPO_ROOT / 'scripts/setup.cmd').read_bytes())
    (tmp_path / 'setup.py').write_text('raise SystemExit(7)\n')
    result = subprocess.run(['cmd.exe', '/d', '/c', str(wrapper)], capture_output=True, timeout=20)
    assert result.returncode == 7

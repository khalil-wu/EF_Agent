"""Windows launcher acceptance: real services, isolated data and a local fake model."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
from threading import Event, Thread

import pytest
import requests


@pytest.mark.skipif(os.name != "nt", reason="PowerShell desktop launcher")
def test_launcher_reuses_instances_and_updates_only_idle_components(tmp_path):
    started, complete = Event(), Event()

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200);self.send_header("Content-Type", "application/json");self.end_headers()
            self.wfile.write(b'{"data":[{"id":"mock-before"},{"id":"mock-after"}]}')

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200);self.send_header("Content-Type", "text/event-stream");self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"reasoning_content":"local fixture"}}]}\n\n');self.wfile.flush()
            started.set()
            complete.wait(30)
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"completed"}}]}\n\ndata: [DONE]\n\n');self.wfile.flush()

    def free_port():
        with socket.socket() as connection:
            connection.bind(("127.0.0.1", 0))
            return connection.getsockname()[1]

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    Thread(target=provider.serve_forever, daemon=True).start()
    worker_port, gateway_port = free_port(), free_port()
    root = Path(__file__).parents[1]
    data = tmp_path / "launcher-data"
    env = {key:value for key,value in os.environ.items() if not key.startswith(("EEGAGENT_", "EFAGENT_"))}
    env.update(EFAGENT_STORAGE_DIR=str(data), EFAGENT_PYTHON=sys.executable,
        EFAGENT_WORKER_URL=f"http://127.0.0.1:{worker_port}", EFAGENT_ADDR=f"127.0.0.1:{gateway_port}",
        EFAGENT_LLM_BASE_URL=f"http://127.0.0.1:{provider.server_port}/v1", EFAGENT_LLM_MODEL="mock-before",
        EFAGENT_LLM_API_KEY="local-test-value", EFAGENT_LLM_PROXY="direct")
    shell = shutil.which("pwsh") or shutil.which("powershell")
    command = [shell,"-NoProfile","-ExecutionPolicy","Bypass","-File",str(root/"scripts/start.ps1"),"-NoBrowser"]
    http = requests.Session();http.trust_env=False
    gateway = f"http://127.0.0.1:{gateway_port}"
    worker = f"http://127.0.0.1:{worker_port}"

    def launch():
        log = tmp_path / "start-command.log"
        with log.open("w", encoding="utf-8") as output:
            result = subprocess.run(command, env=env, cwd=root, stdout=output, stderr=subprocess.STDOUT, timeout=100)
        assert result.returncode == 0, log.read_text(encoding="utf-8", errors="replace")

    def health(url):
        result = http.get(url+"/health", timeout=5);result.raise_for_status();return result.json()

    try:
        launch()
        first_worker, first_gateway = health(worker), health(gateway)
        assert first_worker["source_root"] == first_gateway["source_root"] == str(root)
        assert first_worker["storage_dir"] == first_gateway["storage_dir"] == str(data)
        launch()
        assert health(worker)["process_id"] == first_worker["process_id"]
        assert health(gateway)["process_id"] == first_gateway["process_id"]
        sid = http.post(gateway+"/sessions", json={}, timeout=10).json()["session_id"]
        with ThreadPoolExecutor() as pool:
            future = pool.submit(http.post,gateway+f"/sessions/{sid}/messages",json={"text":"hello"},timeout=40)
            assert started.wait(10)
            env["EFAGENT_LLM_MODEL"] = "mock-after"
            launch()
            assert health(gateway)["process_id"] == first_gateway["process_id"]
            assert health(worker)["process_id"] == first_worker["process_id"]
            complete.set();future.result(timeout=10).raise_for_status()
        launch()
        assert health(worker)["process_id"] == first_worker["process_id"]
        updated = health(gateway)
        assert updated["process_id"] != first_gateway["process_id"]
        assert updated["model"] == "mock-after"
    finally:
        complete.set()
        for url in (gateway, worker):
            try:
                current = health(url)
                if current["storage_dir"] == str(data):
                    os.kill(current["process_id"], signal.SIGTERM)
            except requests.RequestException:
                pass
        provider.shutdown();provider.server_close()

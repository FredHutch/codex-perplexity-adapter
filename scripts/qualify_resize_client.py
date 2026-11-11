"""Actual Codex app-server recovery with synthetic images and mock upstream."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_perplexity_adapter.app import Settings, create_app
from codex_perplexity_adapter.request_size import encode_request
from codex_perplexity_adapter.image_resize import resize_request
from unittest.mock import patch
from scripts.qualify_app_server import RPC
from scripts.work_budget_fixtures import many_images
import base64
from research.tool_continuation import experiment

OUT = Path(__file__).resolve().parents[1] / "build/qualification-0.1.15"

def run(binary):
    OUT.mkdir(parents=True, exist_ok=True)
    seen = []
    measured = []
    def prepare(payload, enabled, work=None):
        measured.append(len(encode_request(payload)))
        return resize_request(payload, enabled, work)
    guard_patch = patch("codex_perplexity_adapter.image_resize.resize_request", prepare)
    guard_patch.start()
    async def provider(request):
        p = json.loads(request.content)
        seen.append({"bytes": len(request.content), "continued": "previous_response_id" in p})
        obj = {"id": "resp_resize_" + str(len(seen)), "object": "response", "status": "completed",
               "output": [{"id": "msg_test", "type": "message", "role": "assistant", "phase": "final_answer",
                           "content": [{"type": "output_text", "text": "RESIZE_MARKER_724", "annotations": []}]}],
               "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}}
        return httpx.Response(200, text="data: " + json.dumps({"type": "response.completed", "response": obj}) + "\n\n", headers={"content-type": "text/event-stream"})
    app = create_app(Settings(api_key="synthetic", local_token="qualification-token", resize_images_on_limit=True), httpx.MockTransport(provider))
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True); thread.start()
    while not server.started: time.sleep(.01)
    try:
        with tempfile.TemporaryDirectory(prefix="resize-client-") as folder:
            root = Path(folder); config = root / "home"; config.mkdir()
            (config / "config.toml").write_text(f'model="gpt-6.1-sol"\nmodel_provider="probe"\nmodel_context_window=40000000\nmodel_auto_compact_token_limit=30000000\n[model_providers.probe]\nname="Mock"\nwire_api="responses"\nexperimental_bearer_token="qualification-token"\nsupports_websockets=false\nbase_url="http://127.0.0.1:{port}/v1"\n')
            env = dict(os.environ, CODEX_HOME=str(config)); env.pop("PERPLEXITY_API_KEY", None)
            with (OUT / "client-stderr.log").open("w") as errors:
                process = subprocess.Popen([binary, "app-server", "--stdio"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True, bufsize=1)
                rpc = RPC(process)
                try:
                    rpc.call("initialize", {"clientInfo": {"name": "resize-test", "version": "0.1.15"}}); rpc.send("initialized")
                    ident = rpc.call("thread/start", {"cwd": str(root), "approvalPolicy": "never", "sandbox": "read-only"})["thread"]["id"]
                    urls = many_images(unique=150, count=150)["input"][-1]["output"]
                    paths = []
                    for index, part in enumerate(urls):
                        path = root / f"page-{index}.jpg"
                        path.write_bytes(base64.b64decode(part["image_url"].split(",", 1)[1]))
                        paths.append(path)
                    inputs = [{"type": "text", "text": "Keep RESIZE_MARKER_724"}] + [{"type": "localImage", "path": str(path)} for path in paths]
                    rpc.call("turn/start", {"threadId": ident, "input": inputs})
                    first = rpc.until(lambda e: e.get("method") == "turn/completed", timeout=120)
                    rpc.call("turn/start", {"threadId": ident, "input": [{"type": "text", "text": "Repeat the marker"}]})
                    second = rpc.until(lambda e: e.get("method") == "turn/completed", timeout=60)
                    with httpx.Client() as c:
                        stats = c.get(f"http://127.0.0.1:{port}/v1/adapter/status", headers={"Authorization": "Bearer qualification-token"}).json()
                    report = {"client": subprocess.check_output([binary, "--version"], text=True).strip(), "requests": seen, "largest_original_body": max(measured), "first": first["params"]["turn"]["status"], "followup": second["params"]["turn"]["status"], "stats": stats}
                    (OUT / "client-resize.json").write_text(json.dumps(report, indent=2) + "\n")
                    print(json.dumps(report))
                    assert report["first"] == report["followup"] == "completed"
                    assert stats["image_resize_requests"] >= 1 and all(r["bytes"] <= 19_000_000 for r in seen)
                finally:
                    process.terminate(); process.wait(timeout=10)
    finally:
        server.should_exit = True; thread.join(timeout=10); guard_patch.stop()

if __name__ == "__main__":
    with experiment("anchor"):
        run(str(Path(sys.argv[1]).resolve()))

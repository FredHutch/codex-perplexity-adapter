"""Isolated real Codex CLI HTTP/SSE and tool-loop compatibility probe."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_perplexity_adapter.app import Settings, create_app
from codex_perplexity_adapter.continuation import canonical_item

DIRECTORY = Path(__file__).resolve().parents[1] / "build/qualification-0.1.13"


def run(binary, live=False):
    seen, saved = [], {}
    async def provider(request):
        payload = json.loads(request.content)
        seen.append(payload)
        prior = payload.get("previous_response_id")
        history = saved[prior] + payload["input"] if prior else payload["input"]
        tools = payload.get("tools", [])
        names = [t.get("name", "") for t in tools]
        if len(seen) == 1:
            print("Normalized tool names:", names, flush=True)
        tool_name = next((n for n in names if n.endswith("exec_command") or n.endswith("shell_command") or n == "shell" or n == "functions__exec"), None)
        output = []
        if len(seen) == 1 and tool_name:
            if tool_name == "functions__exec":
                arguments = {"content": 'text(await tools.exec_command({cmd:"printf SYNTHETIC_TOOL_924",max_output_tokens:50}));'}
            elif tool_name.endswith("exec_command"):
                arguments = {"cmd": "printf SYNTHETIC_TOOL_924", "max_output_tokens": 50}
            elif tool_name.endswith("shell_command"):
                arguments = {"command": "printf SYNTHETIC_TOOL_924"}
            else:
                arguments = {"command": ["/bin/zsh", "-c", "printf SYNTHETIC_TOOL_924"]}
            output = [{"id": "fc_synthetic", "type": "function_call", "name": tool_name,
                       "call_id": "call_synthetic", "arguments": json.dumps(arguments), "status": "completed"}]
        else:
            output = [{"id": "msg_synthetic", "type": "message", "role": "assistant", "phase": "final_answer",
                       "content": [{"type": "output_text", "text": "SYNTHETIC_TOOL_924", "annotations": []}]}]
        ident = "resp_synthetic_" + str(len(seen))
        saved[ident] = history + output
        response = {"id": ident, "object": "response", "status": "completed", "output": output,
                    "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}}
        stream_events = [{"type": "response.created", "response": {**response, "status": "in_progress", "output": []}}]
        for index, item in enumerate(output):
            if item["type"] == "function_call":
                stream_events += [
                    {"type": "response.output_item.added", "output_index": index, "item": {**item, "arguments": ""}},
                    {"type": "response.function_call_arguments.done", "output_index": index,
                     "item_id": item["id"], "arguments": item["arguments"]},
                    {"type": "response.output_item.done", "output_index": index, "item": item}]
        stream_events += [{"type": "response.completed", "response": response}]
        body = "".join("data: " + json.dumps(event) + "\n\n" for event in stream_events)
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    metered = None
    if live:
        from scripts.qualify_continuation import Budget, MeteredTransport
        metered = MeteredTransport(Budget())
    app = create_app(Settings(api_key=os.environ.get("PERPLEXITY_API_KEY", "unused-local-relay") if live else "synthetic-unused",
                             local_token="qualification-token"), metered if live else httpx.MockTransport(provider))
    if live:
        # Qualification-only settings: bound purchased output before normalization
        # and fingerprinting. Production requests are never modified this way.
        class TokenBound:
            def __init__(self, inner):
                self.inner = inner
            async def __call__(self, scope, receive, send):
                if scope.get("path") != "/v1/responses":
                    return await self.inner(scope, receive, send)
                chunks = b""
                more = True
                while more:
                    event = await receive()
                    chunks += event.get("body", b"")
                    more = event.get("more_body", False)
                body = json.loads(chunks)
                body["max_output_tokens"] = 1024
                seen.append(body)
                sent = False
                async def bounded_receive():
                    nonlocal sent
                    if not sent:
                        sent = True
                        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
                    return await receive()
                return await self.inner(scope, bounded_receive, send)
        serving_app = TokenBound(app)
    else:
        serving_app = app
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(serving_app, log_level="error", access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    while not server.started:
        time.sleep(.01)
    try:
        with tempfile.TemporaryDirectory(prefix="codex-013-client-") as folder:
            root = Path(folder)
            home = root / "codex-home"
            home.mkdir()
            (home / "config.toml").write_text('model="gpt-6.1-sol"\nmodel_provider="probe"\nmodel_reasoning_effort="low"\n'
                '[model_providers.probe]\nname="Probe"\nwire_api="responses"\n'
                'env_key="QUALIFICATION_LOCAL_TOKEN"\nsupports_websockets=false\n'
                f'base_url="http://127.0.0.1:{port}/v1"\n')
            env = dict(os.environ, CODEX_HOME=str(home), QUALIFICATION_LOCAL_TOKEN="qualification-token")
            env.pop("PERPLEXITY_API_KEY", None)
            result = subprocess.run([str(Path(binary).resolve()), "exec", "--skip-git-repo-check", "-C", str(root),
                "--json", "Run a shell command that prints SYNTHETIC_TOOL_924, then repeat its result."],
                env=env, capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL)
            # Persist only synthetic result evidence and aggregate request metadata.
            version = subprocess.check_output([str(Path(binary).resolve()), "--version"], env=env, text=True).strip()
            report = {"client": version, "returncode": result.returncode, "upstream_requests": len(seen),
                "continuation_used": app.state.continuation.snapshot()["continuation_requests"] > 0,
                "tool_result_received": any(any(isinstance(i, dict) and i.get("type") in {"function_call_output", "custom_tool_call_output"}
                    and "SYNTHETIC_TOOL_924" in str(i.get("output")) for i in p["input"]) for p in seen),
                "shell_command_completed": any(event.get("type") == "item.completed"
                    and event.get("item", {}).get("type") == "command_execution"
                    and event["item"].get("exit_code") == 0
                    and "SYNTHETIC_TOOL_924" in event["item"].get("aggregated_output", "")
                    for event in (json.loads(line) for line in result.stdout.splitlines() if line.startswith("{"))),
                "completed_final_answer": '"type":"turn.completed"' in result.stdout or '"type": "turn.completed"' in result.stdout,
                "stats": app.state.continuation.snapshot(),
                "request_shapes": [{"input_items": len(p["input"]), "body_bytes": len(json.dumps(p).encode()),
                                    "continued": bool(p.get("previous_response_id"))} for p in seen]}
            DIRECTORY.mkdir(parents=True, exist_ok=True)
            (DIRECTORY / (version.replace(" ", "-") + ("-live.json" if live else "-mock.json"))).write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report), flush=True)
            if not report["completed_final_answer"] or not report["tool_result_received"]:
                print("Client diagnostics:", result.stderr[-4000:], result.stdout[-2000:], flush=True)
                # In-memory fields only, containing synthetic tool calls and results.
                print("Input type shapes:", [[(i.get("type"), i.get("role"), i.get("name"), i.get("phase"))
                    for i in p["input"] if isinstance(i, dict)] for p in seen], flush=True)
            return report
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    run(sys.argv[1], live="--live" in sys.argv)

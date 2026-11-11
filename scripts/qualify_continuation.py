"""Opt-in, synthetic live qualification. Never logs credentials or response IDs.

Run from the repository with PERPLEXITY_API_KEY configured. The persistent ledger
reserves a conservative maximum before every upstream attempt, including retries.
Unknown/failed-call costs retain that reservation. No provider retrieval is used.
"""
import asyncio
import base64
import fcntl
import json
import os
import struct
import sys
import tempfile
import zlib
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_perplexity_adapter.app import BUILTIN_MODELS, Settings, create_app
from codex_perplexity_adapter.request_size import encode_request
from codex_perplexity_adapter.transform import transform_request

DIRECTORY = Path(__file__).resolve().parents[1] / "build/qualification-0.1.13"
LEDGER = DIRECTORY / "live-ledger.json"
# November 11: user approved $10 more, then $5 for the desktop retry.
# Historical reservations and unknown charges remain intact.
LIVE_CEILING = 24.883652
HEADERS = {"Authorization": "Bearer qualification-token", "session-id": "synthetic-live",
           "thread-id": "synthetic-model"}


class Budget:
    def __init__(self):
        DIRECTORY.mkdir(parents=True, exist_ok=True)
        self.calls = json.loads(LEDGER.read_text()) if LEDGER.exists() else []

    def save(self):
        # Readers see either complete ledger version, including on a crash.
        with tempfile.NamedTemporaryFile(mode="w", dir=LEDGER.parent, delete=False) as staged:
            staged.write(json.dumps(self.calls, indent=2) + "\n")
            staged.flush()
            os.fsync(staged.fileno())
        os.replace(staged.name, LEDGER)

    def locked(self):
        return LEDGER.with_suffix(".lock").open("a")

    def reserve(self, payload, body_bytes):
        def size(value):
            if isinstance(value, str):
                if value.startswith("data:image/"):
                    return 4096  # Tiny synthetic raster, conservative image-token allowance.
                return len(value.encode("utf-8"))
            if isinstance(value, dict):
                return sum(size(k) + size(v) for k, v in value.items())
            if isinstance(value, list):
                return sum(size(v) for v in value)
            return 16
        output = payload.get("max_output_tokens")
        if not isinstance(output, int) or output > 1024:
            raise RuntimeError("Qualification requires max_output_tokens <= 1024")
        # Published upper (>272k) rates, doubled; count each UTF-8 byte as
        # one token and ignore cache discounts. Unknown models retain the
        # highest built-in rate. Prior reservations are never rewritten.
        rates = {"gpt-6.1-sol": (4, 15), "gpt-6-sol": (4, 15),
                 "gpt-6-luna": (.2, .75), "gpt-5.6-sol": (8, 30),
                 "gpt-5.6-terra": (4, 18), "gpt-5.6-luna": (.4, 1.8)}
        input_rate, output_rate = rates.get(payload.get("model", "").removeprefix("openai/"), (20, 75))
        reserve = max(.05, 2 * (size(payload) * input_rate + output * output_rate) / 1_000_000)
        record = {"reservation_id": uuid.uuid4().hex, "model": payload.get("model"), "reserved_max": round(reserve, 6),
                  "body_bytes": body_bytes, "continuation": bool(payload.get("previous_response_id"))}
        with self.locked() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.calls = json.loads(LEDGER.read_text()) if LEDGER.exists() else []
            spent = sum(call.get("actual_cost", call["reserved_max"]) for call in self.calls)
            if spent + reserve > LIVE_CEILING:
                raise RuntimeError("Stopping before the approved cumulative live budget is exhausted")
            self.calls.append(record)
            self.save()
        return record

    def settle(self, record, response):
        usage = response.get("usage", {})
        cost = usage.get("cost")
        if isinstance(cost, dict):
            cost = cost.get("total_cost")
        if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost < 0:
            return
        with self.locked() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.calls = json.loads(LEDGER.read_text())
            ident = record.get("reservation_id")
            target = next((c for c in self.calls if ident and c.get("reservation_id") == ident), None)
            if target is None:
                raise RuntimeError("Missing cost reservation")
            target["actual_cost"] = cost
            record["actual_cost"] = cost
            self.save()


class MeteredTransport(httpx.AsyncBaseTransport):
    def __init__(self, budget):
        self.budget = budget
        self.inner = httpx.AsyncHTTPTransport()
        self.records = []
        self.relay = os.environ.get("QUALIFICATION_ADAPTER_RELAY")
        if self.relay and self.relay != "http://127.0.0.1:4000/v1/responses":
            raise ValueError("Qualification relay must be the existing localhost adapter")

    async def handle_async_request(self, request):
        data = await request.aread()
        payload = json.loads(data)
        record = self.budget.reserve(payload, len(data))
        self.records.append(record)
        if self.relay:
            for _ in range(2):
                self.budget.reserve(payload, len(data))
            # The existing adapter holds the credential in its own process.
            # Omit session headers so it cannot optimize a second time.
            relay_payload = {**payload, "model": payload["model"].removeprefix("openai/")}
            request = httpx.Request("POST", self.relay, content=encode_request(relay_payload),
                headers={"Authorization": "Bearer local-adapter-token", "Content-Type": "application/json",
                         "Accept": request.headers.get("Accept", "application/json")}, extensions=request.extensions)
        response = await self.inner.handle_async_request(request)
        original = response.stream
        budget = self.budget
        class UsageStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                buffer = b""
                async for data in original:
                    buffer += data
                    while b"\n\n" in buffer:
                        block, buffer = buffer.split(b"\n\n", 1)
                        for line in block.splitlines():
                            if line.startswith(b"data: {"):
                                try:
                                    event = json.loads(line[6:])
                                    if event.get("type") == "response.completed":
                                        budget.settle(record, event.get("response", {}))
                                except ValueError:
                                    pass
                    if len(buffer) > 2_000_000:
                        buffer = b""  # Do not retain unbounded test response data.
                    yield data
                if buffer.startswith(b"{"):
                    try:
                        budget.settle(record, json.loads(buffer))
                    except ValueError:
                        pass
            async def aclose(self):
                await original.aclose()
        response.stream = UsageStream()
        return response

    async def aclose(self):
        # The adapter closes its per-request client. Keep this qualification-only
        # shared transport alive until all checks finish.
        pass


def msg(text, role="user"):
    return {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}


def completed(response):
    if response.status_code >= 400:
        return {"status": "http_error", "error": response.json().get("error")}
    if "text/event-stream" not in response.headers.get("content-type", ""):
        return response.json()
    data = [json.loads(x[6:]) for x in response.text.splitlines() if x.startswith("data: {")]
    terminals = [x.get("response") or {"status": x["type"].removeprefix("response."),
                                           "error": x.get("error")}
                 for x in data if x.get("type") in {"response.completed", "response.failed", "response.incomplete"}]
    return terminals[-1] if terminals else {"status": "missing_terminal"}


def text(response):
    return "".join(p.get("text", "") for i in response.get("output", []) if i.get("type") == "message"
                   for p in i.get("content", []) if isinstance(p, dict))


def image(color, padding=5_250_000):
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))
    # A valid 32x32 RGB PNG with a large, ignorable ancillary chunk. This tests
    # transport bytes without buying millions of tokens or embedding private data.
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress((b"\0" + bytes(color) * 32) * 32))
    data += chunk(b"npAD", b"S" * padding) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(data).decode()


async def run(section):
    if section == "cli":
        from scripts.qualify_clients import run as run_client
        binary = DIRECTORY / "current-cli/bin/codex"
        await asyncio.to_thread(run_client, str(binary), True)
        return
    if section == "desktop":
        import threading, uvicorn
        meter = MeteredTransport(Budget())
        target = create_app(Settings(api_key=os.environ["PERPLEXITY_API_KEY"], local_token="qualification-token", timeout_seconds=45), meter)
        class Bounded:
            async def __call__(self, scope, receive, send):
                if scope.get("path") != "/v1/responses":
                    return await target(scope, receive, send)
                chunks = b""
                while True:
                    event = await receive()
                    chunks += event.get("body", b"")
                    if not event.get("more_body", False):
                        break
                payload = json.loads(chunks)
                payload["max_output_tokens"] = 1024
                sent = False
                async def once():
                    nonlocal sent
                    if not sent:
                        sent = True
                        return {"type": "http.request", "body": encode_request(payload), "more_body": False}
                    return await receive()
                return await target(scope, once, send)
        server = uvicorn.Server(uvicorn.Config(Bounded(), host="127.0.0.1", port=45013, log_level="error", access_log=False))
        threading.Thread(target=server.run, daemon=True).start()
        print("Isolated desktop adapter: http://127.0.0.1:45013/v1", flush=True)
        return
    budget = Budget()
    transport = MeteredTransport(budget)
    app = create_app(Settings(api_key=os.environ.get("PERPLEXITY_API_KEY", "unused-local-relay"), local_token="qualification-token", timeout_seconds=45), transport)
    checks = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated", timeout=300) as client:
        async def call(label, history, model="gpt-6.1-sol", thread="synthetic-model", **settings):
            payload = {"model": model, "input": history, "store": False, "max_output_tokens": 256,
                       "reasoning": {"effort": "low"}, **settings}
            before = len(transport.records)
            response = await client.post("/v1/responses", headers={**HEADERS, "thread-id": thread}, json=payload)
            result = completed(response)
            if len(transport.records) > before:
                budget.settle(transport.records[-1], result)
            full, _ = transform_request(payload, "openai/" + model)
            records = transport.records[before:]
            check = {"check": label, "http_status": response.status_code, "status": result.get("status"),
                     "full_body_bytes": len(encode_request(full)),
                     "transmitted_bytes": [r["body_bytes"] for r in records],
                     "continued": any(r["continuation"] for r in records),
                     "completed": result.get("status") == "completed"}
            if result.get("error"):
                # Synthetic provider errors only; strip IDs and arbitrary text.
                check["error_code"] = result["error"].get("code")
                check["error_message"] = result["error"].get("message", "")
                check["downstream_20mb"] = "20 MB downstream transport limit" in result["error"].get("message", "")
            checks.append(check)
            print(json.dumps(check), flush=True)
            (DIRECTORY / (section + "-results.json")).write_text(json.dumps(checks, indent=2) + "\n")
            return result, check

        if section == "models":
            for route in {r.upstream: r for r in BUILTIN_MODELS}.values():
                h = [msg("Remember synthetic marker ORCHID-713. Reply only OK.")]
                first, _ = await call(route.alias + " first", h, model=route.alias, thread=route.alias)
                if first.get("status") != "completed":
                    continue
                follow, check = await call(route.alias + " continuation", h + first["output"] + [msg("Repeat the marker.")],
                                           model=route.alias, thread=route.alias)
                check["retained_context"] = "ORCHID-713" in text(follow)
                print("Retained context:", check["retained_context"], flush=True)
                (DIRECTORY / (section + "-results.json")).write_text(json.dumps(checks, indent=2) + "\n")
        elif section == "features":
            h = [msg("Remember ORCHID-713. Reply only OK.")]
            first, _ = await call("streaming first", h, stream=True)
            follow, check = await call("streaming continuation", h + first.get("output", []) + [msg("Repeat marker")], stream=True)
            check["retained_context"] = "ORCHID-713" in text(follow)
            tools = [{"type": "function", "name": "lookup", "description": "Look up the synthetic test marker.", "parameters": {"type": "object", "properties": {}, "required": [],
                       "additionalProperties": False}, "strict": True}]
            h = [msg("Call lookup then report its synthetic result.")]
            first, _ = await call("tool dispatch", h, thread="tools", tools=tools, stream=False)
            calls = [x for x in first.get("output", []) if x.get("type") == "function_call"]
            if calls:
                outputs = [{"type": "function_call_output", "call_id": x["call_id"], "output": "SYNTHETIC_TOOL_924"} for x in calls]
                second, check = await call("tool result continuation", h + first["output"] + outputs,
                    thread="tools", tools=tools, stream=False)
                check["correct_dispatch"] = calls[0].get("name") == "lookup"
                check["retained_tool_result"] = "SYNTHETIC_TOOL_924" in text(second)
            h = [msg("Remember ORCHID-713. Reply OK.")]
            first, _ = await call("instruction baseline", h, thread="instruction", instructions="Be concise.")
            await call("instruction reset", h + first.get("output", []) + [msg("Reply only NEW")],
                       thread="instruction", instructions="Reply only NEW.")
            await call("compacted replay", [msg("Synthetic compacted history: marker ORCHID-713. Reply OK")], thread="instruction")
            (DIRECTORY / (section + "-results.json")).write_text(json.dumps(checks, indent=2) + "\n")
        elif section == "tool-proof":
            tools = [{"type": "function", "name": "lookup", "description": "Return a synthetic test marker.",
                      "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, "strict": True}]
            h = [msg("Call lookup once. After receiving its result, repeat that result and do not call tools again.")]
            first, _ = await call("tool proof baseline", h, thread="tool-proof", tools=tools)
            calls = [i for i in first.get("output", []) if i.get("type") == "function_call"]
            if calls:
                print("Synthetic call field names:", sorted(calls[0]), flush=True)
                h += first["output"] + [{"type": "function_call_output", "call_id": i["call_id"], "output": "SYNTHETIC_TOOL_924"} for i in calls]
                result, check = await call("tool proof continuation", h, thread="tool-proof", tools=tools)
                check["retained_tool_result"] = "SYNTHETIC_TOOL_924" in text(result)
                if result.get("status") == "completed":
                    follow, check = await call("tool proof resumed continuation",
                        h + result["output"] + [msg("Repeat the synthetic result without calling tools.")],
                        thread="tool-proof", tools=tools)
                    check["retained_tool_result"] = "SYNTHETIC_TOOL_924" in text(follow)
                (DIRECTORY / (section + "-results.json")).write_text(json.dumps(checks, indent=2) + "\n")
        elif section == "images":
            h = []
            for i, color in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255))):
                item = msg(("Remember marker ORCHID-713. " if i == 0 else "") + "Describe this solid color in one word.")
                item["content"].append({"type": "input_image", "image_url": image(color), "detail": "low"})
                h.append(item)
                result, _ = await call("image turn " + str(i + 1), h, thread="images", stream=True)
                if result.get("status") != "completed":
                    break
                h += result["output"]
            if result.get("status") == "completed":
                result, check = await call("oversized history small follow-up", h + [msg("Repeat the marker and the first image color.")],
                                           thread="images", stream=True)
                check["retained_context"] = "ORCHID-713" in text(result) and "red" in text(result).lower()
                sizes = check["transmitted_bytes"]
                check["byte_reduction"] = 1 - sizes[-1] / check["full_body_bytes"] if sizes else 0
            await call("image compaction recovery", [msg("Compacted synthetic history: marker ORCHID-713; first image red. Repeat marker and first color.")],
                       thread="images", stream=True)
            (DIRECTORY / (section + "-results.json")).write_text(json.dumps(checks, indent=2) + "\n")
    await transport.inner.aclose()
    print("Total accounted live cost:", sum(c.get("actual_cost", c["reserved_max"]) for c in budget.calls), flush=True)


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1]))

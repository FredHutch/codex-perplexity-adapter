"""Synthetic release checks through the adapter, with one persistent $5 ledger."""
import argparse
import asyncio
import json
import os
import re
import stat
import time
from pathlib import Path

import httpx

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_perplexity_adapter.app import Settings, _normalize_model_request, _route, create_app
from codex_perplexity_adapter.transform import transform_request
from scripts.qualify_continuation import Budget, MeteredTransport, completed, image, msg, text

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build/qualification-0.1.17"
MODELS = ("gpt-6.2-sol", "gpt-6.2-astra")
TOOLS = [{"type": "function", "name": "probe", "description": "Return a synthetic test marker.",
          "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
          "strict": True}]


async def run(output_dir=OUT, key_file=None):
    key = os.environ.get("PERPLEXITY_API_KEY")
    if key_file is not None:
        fd = os.open(key_file, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise RuntimeError("The credential file must be an owner-only regular file")
            values = set(re.findall(r"\bpplx-[A-Za-z0-9_-]+\b", handle.read(65536)))
        if len(values) != 1:
            raise RuntimeError("Save exactly one Perplexity API key in the credential file")
        key = values.pop()
    if not key:
        raise RuntimeError("PERPLEXITY_API_KEY is required")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    # The report location cannot create a new allowance. All release probes,
    # including restarted runs, use this one fixed ledger.
    budget = Budget(directory=OUT / "live-budget", ceiling=5.0)
    meter = MeteredTransport(budget)
    meter.relay = None
    app = create_app(Settings(api_key=key,
                              local_token="qualification-token", timeout_seconds=120), meter)
    report = {"models": list(MODELS), "checks": [], "budget_ceiling": 5.0}

    def save():
        records = json.loads(budget.ledger.read_text()) if budget.ledger.exists() else []
        report["budget"] = {"attempts": len(records),
                            "accounted_usd": round(sum(r.get("actual_cost", r["reserved_max"]) for r in records), 6),
                            "reported_usd": round(sum(r.get("actual_cost", 0) for r in records), 6),
                            "unknown_cost_attempts": sum("actual_cost" not in r for r in records)}
        (output_dir / "live-gpt62.json").write_text(json.dumps(report, indent=2) + "\n")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://qualification", timeout=180) as client:
        async def call(model, label, history, stream=False, **options):
            payload = {"model": model, "input": history, "stream": stream,
                       "reasoning": {"effort": "low"}, "max_output_tokens": 1024, **options}
            route = _route(model, "openai/" + model)
            meter.effective_payload = transform_request(_normalize_model_request(payload, route), route.upstream)[0]
            before = len(meter.records)
            start = time.monotonic()
            response = await client.post("/v1/responses", json=payload,
                headers={"Authorization": "Bearer qualification-token", "session-id": "release-0.1.17",
                         "thread-id": model + ("-tool-chain" if label.startswith("tool-") else "-" + label)})
            result = completed(response)
            error = result.get("error") or {}
            check = {"model": model, "check": label, "stream": stream, "http_status": response.status_code,
                     "status": result.get("status"), "seconds": round(time.monotonic() - start, 3),
                     "attempts": len(meter.records) - before,
                     "error_type": error.get("type"), "error_code": error.get("code")}
            report["checks"].append(check)
            save()
            if response.status_code != 200 or result.get("status") != "completed":
                raise RuntimeError(f"{model} {label} failed; see sanitized release evidence")
            return result, check

        try:
            models = await client.get("/v1/models", headers={"Authorization": "Bearer qualification-token"})
            assert set(MODELS) <= {item["id"] for item in models.json()["data"]}
            for model in MODELS:
                marker = "RELEASE_GPT62_917"
                for stream in (False, True):
                    result, check = await call(model, "streaming" if stream else "json",
                        [msg("Reply exactly " + marker)], stream=stream,
                        reasoning={"effort": "none" if stream else "minimal"}, temperature=0.5, top_p=0.8,
                        top_logprobs=5, include=["message.output_text.logprobs", "reasoning.encrypted_content"])
                    check["marker_received"] = marker in text(result)
                    assert check["marker_received"]
                history = [msg("Call probe exactly once to obtain the marker.")]
                result, check = await call(model, "tool-dispatch", history, stream=True,
                    tools=TOOLS, tool_choice={"type": "function", "name": "probe"})
                calls = [item for item in result["output"] if item.get("type") == "function_call"]
                check["tool_dispatched"] = len(calls) == 1 and calls[0].get("name") == "probe"
                assert check["tool_dispatched"]
                history += result["output"] + [{"type": "function_call_output", "call_id": calls[0]["call_id"], "output": marker}]
                result, check = await call(model, "tool-result", history, tools=TOOLS, tool_choice="none")
                check["marker_received"] = marker in text(result)
                assert check["marker_received"]
                history += result["output"] + [msg("Repeat the marker from the preceding tool result, with no other text.")]
                before_continuations = app.state.continuation.snapshot()["continuation_requests"]
                result, check = await call(model, "tool-followup", history, stream=True, tools=TOOLS, tool_choice="none")
                check["marker_received"] = marker in text(result)
                check["continuation_used"] = app.state.continuation.snapshot()["continuation_requests"] > before_continuations
                assert check["marker_received"] and check["continuation_used"]
                result, check = await call(model, "image", [{"role": "user", "content": [
                    {"type": "input_text", "text": "Name the dominant color in this image. Reply with one word."},
                    {"type": "input_image", "image_url": image((0, 0, 255), padding=0), "detail": "low"}]}])
                check["color_received"] = "blue" in text(result).lower()
                assert check["color_received"]
                save()
                print(json.dumps({"model": model, "passed": True, "budget": report["budget"]}), flush=True)
            report["passed"] = True
        finally:
            save()
            await meter.inner.aclose()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--key-file", type=Path)
    args = parser.parse_args()
    asyncio.run(run(args.output_dir, args.key_file))

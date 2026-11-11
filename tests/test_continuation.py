"""Stateful provider reconstruction and strict continuation/lifetime checks."""
import asyncio
import copy
import json
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from codex_perplexity_adapter.app import Settings, create_app
from codex_perplexity_adapter.cli import build_parser
from codex_perplexity_adapter.continuation import ContinuationManager, canonical_item, prior_response_error
from codex_perplexity_adapter.request_size import REQUEST_BUDGET_BYTES, encode_request, prepare_request_body
from codex_perplexity_adapter.transform import transform_request

HEADERS = {"Authorization": "Bearer local-adapter-token", "session-id": "synthetic-session",
           "thread-id": "synthetic-thread"}


def message(text, role="user", **extra):
    return {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}], **extra}


def answer(text="OK", **extra):
    return {"id": "msg_test", "type": "message", "role": "assistant", "phase": "final_answer",
            "status": "completed", "content": [{"type": "output_text", "text": text, "annotations": []}], **extra}


def events(response):
    return "data: " + json.dumps({"type": "response.completed", "response": response}) + "\n\n"


def terminal(response):
    if "text/event-stream" not in response.headers.get("content-type", ""):
        return response.json()
    parsed = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
    return next(x["response"] for x in reversed(parsed) if x.get("type") == "response.completed")


class StatefulProvider:
    """Reconstruct saved provider state and compare it with normalized full replay."""
    def __init__(self, case):
        self.case = case
        self.saved = {}
        self.seen = []
        self.expected = None
        self.output = [answer()]
        self.failure = None
        self.tool_responses = set()

    async def __call__(self, request):
        payload = json.loads(request.content)
        self.seen.append(payload)
        if self.failure:
            status, body = self.failure
            self.failure = None
            return httpx.Response(status, json=body)
        prior = payload.get("previous_response_id")
        if prior in self.tool_responses:
            return httpx.Response(400, json={"error": {"code": "invalid_request", "message": "invalid request"}})
        reconstructed = self.saved[prior] + payload["input"] if prior else payload["input"]
        if self.expected is not None:
            self.case.assertEqual([canonical_item(x) for x in reconstructed],
                                  [canonical_item(x) for x in self.expected])
        ident = "resp_" + str(len(self.seen))
        response = {"id": ident, "status": "completed", "output": copy.deepcopy(self.output)}
        self.saved[ident] = reconstructed + copy.deepcopy(self.output)
        if any(i.get("type") == "function_call" for i in self.output):
            self.tool_responses.add(ident)
        if payload.get("stream"):
            return httpx.Response(200, text=events(response), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=response)


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.provider = StatefulProvider(self)
        self.app = create_app(Settings(api_key="synthetic-unused"), httpx.MockTransport(self.provider))
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def send(self, history, headers=None, **settings):
        payload = {"model": "gpt-6.1-sol", "input": history, **settings}
        before = copy.deepcopy(payload)
        normalized, _ = transform_request(payload, "openai/" + payload["model"])
        self.provider.expected = normalized["input"]
        response = self.client.post("/v1/responses", headers=HEADERS if headers is None else headers, json=payload)
        self.assertEqual(payload, before)
        return response

    def first(self, **settings):
        history = [message("Remember synthetic marker ORCHID.")]
        response = terminal(self.send(history, **settings))
        return history + response["output"], response

    def test_three_turns_exact_suffix_and_response_ids(self):
        history, first = self.first(store=False)
        second_item = message("Second turn")
        second = terminal(self.send(history + [second_item], store=False))
        self.assertEqual(self.provider.seen[-1]["input"], [second_item])
        self.assertEqual(self.provider.seen[-1]["previous_response_id"], first["id"])
        third_item = message("Third turn")
        third = self.send(history + [second_item] + second["output"] + [third_item], store=False)
        self.assertEqual(third.status_code, 200)
        self.assertEqual(self.provider.seen[-1]["input"], [third_item])
        self.assertEqual(self.provider.seen[-1]["previous_response_id"], second["id"])
        self.assertFalse(self.provider.seen[-1]["store"])
        status = self.client.get("/v1/adapter/status", headers=HEADERS).json()
        self.assertEqual(status["continuation_requests"], 2)
        self.assertEqual(status["full_replay_requests"], 1)
        self.assertEqual(status["continuation_cache_entries"], 1)
        self.assertNotIn("ORCHID", json.dumps(status))
        self.assertTrue(all(type(v) is int for v in status.values()))

    def test_long_history_savings_exact_utf8_and_image_bytes(self):
        image = "data:image/png;base64," + "a" * 100_000
        history = [message("é" * 50_000), {"type": "function_call_output", "call_id": "c",
                    "output": [{"type": "input_image", "image_url": image}]}]
        first = terminal(self.send(history))
        self.assertEqual(self.provider.seen[0]["input"][1]["output"][0]["image_url"], image)
        history += first["output"] + [message("Again é")]
        full, _ = transform_request({"model": "gpt-6.1-sol", "input": history}, "openai/gpt-6.1-sol")
        self.send(history)
        selected_bytes = len(encode_request(self.provider.seen[-1]))
        full_bytes = len(encode_request(full))
        self.assertGreater(1 - selected_bytes / full_bytes, .90)
        status = self.client.get("/v1/adapter/status", headers=HEADERS).json()
        self.assertEqual(status["saved_request_bytes"], full_bytes - selected_bytes)
        self.assertEqual(status["latest_completed_request_bytes"], selected_bytes)

    def test_canonical_text_transport_fields_and_conservative_fields(self):
        history, _ = self.first()
        history[0] = {"role": "user", "id": "transport-changed", "status": "completed",
                      "content": "Remember synthetic marker ORCHID."}
        history[1]["id"] = "other-transport-id"
        self.send(history + [message("Next")])
        self.assertIn("previous_response_id", self.provider.seen[-1])
        for field, value in (("phase", "commentary"), ("extension", {"future": 1})):
            with self.subTest(field=field):
                history, _ = self.first()
                history[1][field] = value
                self.send(history + [message("Next")])
                self.assertNotIn("previous_response_id", self.provider.seen[-1])
        history, _ = self.first()
        history[0]["content"][0]["text"] += " "
        self.send(history + [message("Next")])
        self.assertNotIn("previous_response_id", self.provider.seen[-1])

    def test_tool_loops_parallel_custom_namespace_images_and_reasoning(self):
        for custom, namespace, stream in ((False, False, False), (True, False, True),
                                           (False, True, True), (True, True, False)):
            with self.subTest(custom=custom, namespace=namespace, stream=stream):
                tool = {"type": "custom" if custom else "function", "name": "inspect"}
                if not custom:
                    tool["parameters"] = {"type": "object", "properties": {}}
                tools = [{"type": "namespace", "name": "functions", "tools": [tool]}] if namespace else [tool]
                name = "functions__inspect" if namespace else "inspect"
                reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "SYNTHETIC"}
                self.provider.output = [reasoning, answer("Inspecting", phase="commentary")] + [
                    {"type": "function_call", "id": "fc_" + str(i), "call_id": "c" + str(i),
                     "name": name, "arguments": '{"content":"inspect"}' if custom else "{}", "status": "completed"}
                    for i in range(2)]
                history, response = self.first(tools=tools, stream=stream)
                calls = response["output"][-2:]
                self.assertEqual(calls[0]["type"], "custom_tool_call" if custom else "function_call")
                if namespace:
                    self.assertEqual(calls[0]["namespace"], "functions")
                results = [{"type": "custom_tool_call_output" if custom else "function_call_output",
                    "call_id": "c" + str(i), "output": [{"type": "input_text", "text": "synthetic é"},
                    {"type": "input_image", "image_url": "data:image/png;base64,TEST", "detail": "original"}]}
                    for i in range(2)]
                self.provider.output = [answer()]
                final = terminal(self.send(history + results, tools=tools, stream=stream))
                self.assertNotIn("previous_response_id", self.provider.seen[-1])
                self.assertEqual(len(self.provider.seen[-1]["input"]), len(history) + 2)
                self.assertEqual(self.provider.seen[-1]["input"][-2]["call_id"], "c0")
                self.assertEqual(self.provider.seen[-1]["input"][-2]["output"][1]["detail"], "high")
                self.send(history + results + final["output"] + [message("Follow up")], tools=tools, stream=stream)
                self.assertEqual(self.provider.seen[-1]["previous_response_id"], final["id"])
                self.assertEqual(len(self.provider.seen[-1]["input"]), 1)

    def test_config_instructions_modes_tools_model_compaction_duplicate_and_branch_reset(self):
        changes = [({"instructions": "new"}, None), ({"max_output_tokens": 100}, None),
                   ({"reasoning": {"effort": "high"}}, None), ({"tools": [{"type": "function", "name": "new"}]}, None),
                   ({"model": "gpt-6-sol"}, None), ({"store": False}, None),
                   ({}, lambda h: [message("Compacted synthetic summary")]),
                   ({}, lambda h: h[:-1] + [answer("branch")]),
                   ({}, lambda h: h),
                   ({}, lambda h: [message("New instructions", "developer")] + h + [message("next")])]
        for settings, edit in changes:
            with self.subTest(settings=settings, edit=edit):
                history, _ = self.first()
                self.send(edit(history) if edit else history + [message("Next")], **settings)
                self.assertNotIn("previous_response_id", self.provider.seen[-1])
        mode = "<collaboration_mode>\n# Collaboration Mode: Default\nDefault\n</collaboration_mode>"
        history = [message(mode, "developer"), message("go")]
        out = terminal(self.send(history))
        history += out["output"] + [message(mode.replace("Default", "Plan"), "developer"), message("next")]
        self.send(history)
        self.assertNotIn("previous_response_id", self.provider.seen[-1])

    def test_later_multiple_tool_rounds_replay_then_resume(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                history, first = self.first(stream=stream)
                history += [message("Use tools twice")]
                for round_number in (1, 2):
                    call_id = "round_" + str(round_number)
                    self.provider.output = [{"type": "function_call", "id": call_id, "call_id": call_id,
                                             "name": "inspect", "arguments": "{}"}]
                    dispatched = terminal(self.send(history, stream=stream))
                    if round_number == 1:
                        self.assertEqual(self.provider.seen[-1]["previous_response_id"], first["id"])
                    else:
                        self.assertNotIn("previous_response_id", self.provider.seen[-1])
                    self.assertEqual(self.app.state.continuation.snapshot()["continuation_cache_entries"], 0)
                    history += dispatched["output"] + [{"type": "function_call_output",
                                                       "call_id": call_id, "output": "é result"}]
                self.provider.output = [answer("Finished")]
                before = self.app.state.continuation.tool_replays
                self.provider.failure = (429, {"error": {"code": "rate_limit"}})
                with patch("codex_perplexity_adapter.app.asyncio.sleep", return_value=None):
                    final = terminal(self.send(history, stream=stream))
                self.assertEqual(self.app.state.continuation.tool_replays, before + 1)
                self.assertNotIn("previous_response_id", self.provider.seen[-1])
                self.send(history + final["output"] + [message("Next")], stream=stream)
                self.assertEqual(self.provider.seen[-1]["previous_response_id"], final["id"])
                self.assertEqual(len(self.provider.seen[-1]["input"]), 1)

    def test_new_tool_suffix_and_oversized_tool_replay(self):
        history, _ = self.first()
        call = {"type": "function_call", "call_id": "external", "name": "inspect", "arguments": "{}"}
        history += [call, {"type": "function_call_output", "call_id": "external", "output": "é" * 1000}]
        before = len(self.provider.seen)
        with patch("codex_perplexity_adapter.app.prepare_request_body", lambda p: prepare_request_body(p, budget=500)):
            result = self.send(history)
        self.assertEqual(result.json()["error"]["code"], "context_length_exceeded")
        self.assertEqual(len(self.provider.seen), before)
        self.assertEqual(self.app.state.continuation.tool_replays, 0)
        self.send(history)
        self.assertNotIn("previous_response_id", self.provider.seen[-1])
        self.assertEqual(self.app.state.continuation.tool_replays, 1)

    def test_failed_optimized_responses_invalidate_without_savings(self):
        for stream in (False, True):
            for kind in ("failed", "incomplete", "malformed"):
                with self.subTest(stream=stream, kind=kind):
                    history, _ = self.first()
                    before = self.app.state.continuation.saved_bytes
                    async def failure(request):
                        if kind == "malformed":
                            return httpx.Response(200, text="data: invalid\n\n" if stream else "invalid",
                                headers={"content-type": "text/event-stream" if stream else "application/json"})
                        failed = {"id": "failed", "status": kind, "output": [],
                                  "error": {"code": "invalid_request", "message": "invalid request"}}
                        return (httpx.Response(200, text="data: " + json.dumps({"type": "response." + kind,
                                    "response": failed}) + "\n\n", headers={"content-type": "text/event-stream"})
                                if stream else httpx.Response(200, json=failed))
                    with patch.object(self.provider, "failure", None):
                        async def failing_provider(provider, request):
                            return await failure(request)
                        with patch.object(self.provider.__class__, "__call__", failing_provider):
                            self.send(history + [message("Next")], stream=stream)
                    self.assertEqual(self.app.state.continuation.saved_bytes, before)
                    self.assertFalse(self.app.state.continuation.entries)

    def test_stream_flag_does_not_reset(self):
        history, _ = self.first(stream=False)
        self.send(history + [message("Next")], stream=True)
        self.assertIn("previous_response_id", self.provider.seen[-1])

    def test_missing_either_header_and_different_chats_replay(self):
        for removed in ("session-id", "thread-id"):
            history, _ = self.first()
            self.send(history + [message("Next")], headers={k: v for k, v in HEADERS.items() if k != removed})
            self.assertNotIn("previous_response_id", self.provider.seen[-1])
        history, _ = self.first()
        self.send(history + [message("Next")], headers={**HEADERS, "thread-id": "other-chat"})
        self.assertNotIn("previous_response_id", self.provider.seen[-1])

    def test_disabled_and_explicit_continuation(self):
        for mode in ("auto", "disabled"):
            provider = StatefulProvider(self)
            with TestClient(create_app(Settings(api_key="unused", continuation=mode), httpx.MockTransport(provider))) as c:
                first = c.post("/v1/responses", headers=HEADERS, json={"model": "gpt-6.1-sol", "input": [message("one")]})
                prior = first.json()["id"]
                c.post("/v1/responses", headers=HEADERS, json={"model": "gpt-6.1-sol", "input": [message("two")],
                       "previous_response_id": prior, "store": False})
                self.assertEqual(provider.seen[-1]["previous_response_id"], prior)
                self.assertFalse(provider.seen[-1]["store"])
                c.post("/v1/responses", headers=HEADERS, json={"model": "gpt-6.1-sol", "input": [message("fresh")]})
                self.assertNotIn("previous_response_id", provider.seen[-1])
        self.assertEqual(build_parser().parse_args([]).continuation, "auto")
        self.assertEqual(build_parser().parse_args(["--continuation", "disabled"]).continuation, "disabled")
        self.assertNotIn("budget-notices", build_parser().format_help())

    def test_targeted_fallback_once_and_unrelated_errors_preserved(self):
        for code, fallback in (("previous_response_not_found", True), ("previous_response_not_completed", True),
                               ("previous_response_id_not_supported", True), ("invalid_model", False)):
            with self.subTest(code=code):
                history, _ = self.first()
                self.provider.failure = (400, {"error": {"code": code, "message": "synthetic failure"}})
                before = len(self.provider.seen)
                response = self.send(history + [message("Next")])
                self.assertEqual(len(self.provider.seen) - before, 2 if fallback else 1)
                self.assertEqual(response.status_code, 200 if fallback else 400)
                if fallback:
                    self.assertNotIn("previous_response_id", self.provider.seen[-1])
                else:
                    self.assertEqual(response.json()["error"]["code"], code)

    def test_first_sse_error_fallback_once_and_second_rejection_preserved(self):
        for second_fails in (False, True):
            seen = []
            async def handler(request):
                payload = json.loads(request.content)
                seen.append(payload)
                failure = {"type": "response.failed", "response": {"id": "failure", "status": "failed", "output": [],
                    "error": {"param": "previous_response_id", "code": "previous_response_not_found"}}}
                if len(seen) == 1:
                    return httpx.Response(200, json={"id": "r", "status": "completed", "output": [answer()]})
                if len(seen) == 2 or second_fails:
                    return httpx.Response(200, text="data: " + json.dumps(failure) + "\n\n",
                                          headers={"content-type": "text/event-stream"})
                return httpx.Response(200, text=events({"id": "r2", "status": "completed", "output": [answer()]}),
                                      headers={"content-type": "text/event-stream"})
            with TestClient(create_app(Settings(api_key="unused"), httpx.MockTransport(handler))) as c:
                history = [message("go")]
                c.post("/v1/responses", headers=HEADERS, json={"model": "gpt-6.1-sol", "input": history})
                response = c.post("/v1/responses", headers=HEADERS,
                    json={"model": "gpt-6.1-sol", "input": history + [answer(), message("next")], "stream": True})
                self.assertEqual(len(seen), 3)
                self.assertNotIn("previous_response_id", seen[-1])
                self.assertEqual("response.failed" in response.text, second_fails)
                status = c.get("/v1/adapter/status", headers=HEADERS).json()
                self.assertEqual(status["continuation_fallbacks"], 1)
                self.assertEqual(status["saved_request_bytes"], 0)

    def test_optimized_and_fallback_guard_exact_utf8_boundaries(self):
        history, _ = self.first()
        history += [message("é")]
        full, _ = transform_request({"model": "gpt-6.1-sol", "input": history}, "openai/gpt-6.1-sol")
        selected = {**full, "input": history[-1:], "previous_response_id": "resp_1"}
        selected_size = len(encode_request(selected))
        self.assertLess(selected_size, len(encode_request(full)))
        def guard(body):
            return prepare_request_body(body, budget=selected_size)
        self.provider.failure = (404, {"error": {"param": "previous_response_id", "code": "previous_response_not_found"}})
        with patch("codex_perplexity_adapter.app.prepare_request_body", guard):
            response = self.send(history)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "context_length_exceeded")
        self.assertEqual(len(self.provider.seen), 2)  # Selected accepted, oversized fallback never sent.
        self.assertEqual(self.client.get("/v1/adapter/status", headers=HEADERS).json()["saved_request_bytes"], 0)
        for offset, succeeds in ((0, True), (-1, False)):
            history, _ = self.first()
            history += [message("é")]
            selected["previous_response_id"] = "resp_" + str(len(self.provider.seen))
            selected["input"] = history[-1:]
            limit = len(encode_request(selected)) + offset
            with patch("codex_perplexity_adapter.app.prepare_request_body", lambda b: prepare_request_body(b, budget=limit)):
                result = self.send(history)
            self.assertEqual(result.status_code, 200 if succeeds else 400)

    def test_production_guard_oversized_continuation_fallback(self):
        history = [message('x' * (REQUEST_BUDGET_BYTES - 1000))]
        result = terminal(self.send(history))
        history += result['output'] + [message('é' * 1000)]
        self.provider.failure = (404, {'error': {'code': 'previous_response_not_found', 'param': 'previous_response_id'}})
        response = self.send(history)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'context_length_exceeded')
        self.assertEqual(len(self.provider.seen), 2)
        self.assertIn('previous_response_id', self.provider.seen[-1])

    def test_production_guard_tool_result_full_replay(self):
        for stream in (False, True):
            for target in (REQUEST_BUDGET_BYTES, REQUEST_BUDGET_BYTES + 1):
                history = [message('é☃'),
                    {'type': 'function_call', 'call_id': 'call_guard', 'name': 'probe', 'arguments': '{}'},
                    {'type': 'function_call_output', 'call_id': 'call_guard', 'output': 'retained'}]
                payload = {'model': 'gpt-6.1-sol', 'input': history, 'stream': stream}
                full, _ = transform_request(payload, 'openai/gpt-6.1-sol')
                history[0]['content'][0]['text'] += 'x' * (target - len(encode_request(full)))
                before = len(self.provider.seen)
                response = self.send(history, stream=stream)
                if target == REQUEST_BUDGET_BYTES:
                    self.assertEqual(terminal(response)['status'], 'completed')
                    self.assertEqual(len(self.provider.seen), before + 1)
                    self.assertNotIn('previous_response_id', self.provider.seen[-1])
                else:
                    self.assertEqual(len(self.provider.seen), before)
                    self.assertIn('context_length_exceeded', response.text)

    def test_invalid_responses_do_not_checkpoint(self):
        for response in ({"status": "completed", "output": []}, {"id": "r", "status": "incomplete", "output": []},
                         {"id": "r", "status": "failed", "output": []}, {"id": "r", "status": "completed"}):
            async def handler(request):
                return httpx.Response(200, json=response)
            with TestClient(create_app(Settings(api_key="unused"), httpx.MockTransport(handler))) as client:
                client.post("/v1/responses", headers=HEADERS, json={"model": "gpt-6.1-sol", "input": [message("go")]})
                self.assertEqual(client.get("/v1/adapter/status", headers=HEADERS).json()["continuation_cache_entries"], 0)

    def test_malformed_incomplete_streams_missing_id_and_duplicate_completion(self):
        valid = {"id": "r", "status": "completed", "output": [answer()]}
        bad_streams = [events(valid) + "data: bad-json\n\n", events(valid) + "data: {}\n\n",
                       events(valid) + "data: []\n\n", events(valid) + "data: {\"type\":\"error\"}\n\n",
                       events(valid) + events(valid), events(valid) + "data: trailing",
                       events({"id": "r", "status": "completed"}), events({"status": "completed", "output": []}),
                       'data: {"type":"response.incomplete","response":{"status":"incomplete"}}\n\n',
                       'data: {"type":"response.output_text.delta","delta":"partial"}\n\n']
        for stream in bad_streams:
            with self.subTest(stream=stream[:100]):
                async def handler(request):
                    return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})
                with TestClient(create_app(Settings(api_key="unused"), httpx.MockTransport(handler))) as client:
                    client.post("/v1/responses", headers=HEADERS,
                                json={"model": "gpt-6.1-sol", "input": [message("go")], "stream": True})
                    self.assertEqual(client.get("/v1/adapter/status", headers=HEADERS).json()["continuation_cache_entries"], 0)

    def test_incremental_stream_output_captured_even_when_terminal_omits_commentary(self):
        calls = [{"id": "fc", "type": "function_call", "call_id": "c", "name": "inspect", "arguments": "{}"}]
        sequence = [
            {"type": "response.output_text.delta", "output_index": 0, "item_id": "msg", "delta": "Checking"},
            {"type": "response.output_text.done", "output_index": 0, "item_id": "msg", "text": "Checking"},
            {"type": "response.output_item.done", "output_index": 1, "item": calls[0]},
            {"type": "response.completed", "response": {"id": "r", "status": "completed", "output": calls}}]
        seen = []
        class ChunkStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                for event in sequence:
                    data = ("data: " + json.dumps(event) + "\n\n").encode()
                    for i in range(0, len(data), 7):
                        yield data[i:i + 7]
        async def handler(request):
            seen.append(json.loads(request.content))
            if len(seen) == 1:
                return httpx.Response(200, stream=ChunkStream(), headers={"content-type": "text/event-stream"})
            return httpx.Response(200, text=events({"id": "r2", "status": "completed", "output": [answer()]}),
                                  headers={"content-type": "text/event-stream"})
        with TestClient(create_app(Settings(api_key="unused"), httpx.MockTransport(handler))) as client:
            history = [message("go")]
            response = terminal(client.post("/v1/responses", headers=HEADERS,
                               json={"model": "gpt-6.1-sol", "input": history, "stream": True}))
            self.assertEqual(len(response["output"]), 2)
            self.assertEqual(response["output"][0]["phase"], "commentary")
            client.post("/v1/responses", headers=HEADERS, json={"model": "gpt-6.1-sol", "stream": True,
                "input": history + response["output"] + [{"type": "function_call_output", "call_id": "c", "output": "done"}]})
            self.assertNotIn("previous_response_id", seen[-1])
            self.assertEqual(len(seen[-1]["input"]), 4)

    def test_no_retry_after_stream_output_or_ambiguous_connection(self):
        seen = []
        async def handler(request):
            seen.append(json.loads(request.content))
            if len(seen) == 1:
                return httpx.Response(200, json={"id": "r", "status": "completed", "output": [answer()]})
            if len(seen) == 2:
                return httpx.Response(200, text='data: {"type":"response.output_text.delta","delta":"started"}\n\n'
                    'data: {"type":"response.failed","response":{"error":{"param":"previous_response_id"},"status":"failed"}}\n\n',
                    headers={"content-type": "text/event-stream"})
            raise httpx.ReadError("ambiguous connection")
        with TestClient(create_app(Settings(api_key="unused"), httpx.MockTransport(handler))) as c:
            history = [message("go")]
            c.post("/v1/responses", headers=HEADERS, json={"model": "gpt-6.1-sol", "input": history})
            c.post("/v1/responses", headers=HEADERS, json={"model": "gpt-6.1-sol", "input": history + [answer(), message("next")], "stream": True})
            self.assertEqual(len(seen), 2)
            result = c.post("/v1/responses", headers=HEADERS,
                            json={"model": "gpt-6.1-sol", "input": history + [answer(), message("next")]})
            self.assertEqual(result.status_code, 502)
            self.assertEqual(len(seen), 3)

    def test_429_retries_keep_selected_suffix(self):
        history, _ = self.first()
        self.provider.failure = (429, {"error": {"code": "rate_limit"}})
        with patch("codex_perplexity_adapter.app.asyncio.sleep", return_value=None):
            result = self.send(history + [message("next")])
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.provider.seen[-1], self.provider.seen[-2])
        self.assertIn("previous_response_id", self.provider.seen[-1])


class ManagerTests(unittest.IsolatedAsyncioTestCase):
    def checkpoint(self, manager, key, value="go"):
        payload = {"input": [message(value)], "model": "test"}
        hashes = manager.fingerprints(payload)
        manager.commit(key, {"id": "response", "status": "completed"}, hashes, [answer()])
        return {**payload, "input": payload["input"] + [answer(), message("next")]}

    async def test_expiry_lru_eviction_memory_bound_restart_and_key_isolation(self):
        now = [0]
        manager = ContinuationManager(max_chats=2, clock=lambda: now[0], ttl=10)
        keys = [manager.session_key("s", str(i)) for i in range(3)]
        payload = self.checkpoint(manager, keys[0])
        self.checkpoint(manager, keys[1])
        self.assertIn("previous_response_id", manager.select(keys[0], payload, manager.fingerprints(payload)))
        self.checkpoint(manager, keys[2])
        self.assertNotIn(keys[1], manager.entries)
        now[0] = 10
        self.assertNotIn("previous_response_id", manager.select(keys[0], payload, manager.fingerprints(payload)))
        self.assertEqual(manager.snapshot()["continuation_cache_entries"], 0)
        restarted = ContinuationManager()
        self.assertNotEqual(manager.digest("same"), restarted.digest("same"))
        self.assertNotIn("previous_response_id", restarted.select(keys[0], payload, restarted.fingerprints(payload)))
        bounded = ContinuationManager(max_bytes=3000)
        for i in range(10):
            self.checkpoint(bounded, bounded.session_key("s", str(i)))
        self.assertLessEqual(bounded.cache_bytes, 3000)
        self.assertLess(len(bounded.entries), 10)
        huge = ContinuationManager(max_bytes=1)
        self.checkpoint(huge, huge.session_key("s", "t"))
        self.assertFalse(huge.entries)
        self.assertNotEqual(manager.session_key("a", "b"), manager.session_key("b", "a"))

    async def test_checkpoints_retain_only_keyed_hashes_references_and_numbers(self):
        manager = ContinuationManager()
        key = manager.session_key("PRIVATE_SESSION", "PRIVATE_THREAD")
        self.checkpoint(manager, key, "PRIVATE_TEXT")
        retained = repr(manager.entries) + repr(manager.gates)
        for private in ("PRIVATE_SESSION", "PRIVATE_THREAD", "PRIVATE_TEXT"):
            self.assertNotIn(private, retained)
        self.assertTrue(all(isinstance(x, bytes) and len(x) == 32 for x in manager.entries[key].items))

    async def test_gate_serialization_independent_chats_and_cancelled_waiters(self):
        manager = ContinuationManager(max_chats=1)
        key, other = manager.session_key("s", "t"), manager.session_key("s", "other")
        self.assertEqual(await manager.acquire(key), key)
        waiter = asyncio.create_task(manager.acquire(key))
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())
        self.assertEqual(await manager.acquire(other), other)
        self.assertEqual(len(manager.gates), 2)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        manager.release(key)
        manager.release(other)
        self.assertFalse(manager.gates)

    async def test_same_chat_endpoint_serializes_until_stream_completion_and_releases_on_cancel(self):
        started, finish = asyncio.Event(), asyncio.Event()
        count = [0]
        class SlowStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                started.set()
                yield b'data: {"type":"response.output_text.delta","delta":"OK"}\n\n'
                await finish.wait()
                yield events({"id": "r", "status": "completed", "output": [answer()]}).encode()
        async def handler(request):
            count[0] += 1
            return httpx.Response(200, stream=SlowStream(), headers={"content-type": "text/event-stream"})
        app = create_app(Settings(api_key="unused"), httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as c:
            payload = {"model": "gpt-6.1-sol", "input": [message("go")], "stream": True}
            first = asyncio.create_task(c.post("/v1/responses", headers=HEADERS, json=payload))
            await started.wait()
            second = asyncio.create_task(c.post("/v1/responses", headers=HEADERS, json=payload))
            third = asyncio.create_task(c.post("/v1/responses", headers={**HEADERS, "thread-id": "other"}, json=payload))
            for _ in range(10):
                await asyncio.sleep(.01)
                if count[0] == 2:
                    break
            self.assertEqual(count[0], 2)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            finish.set()
            await asyncio.gather(second, third)
        self.assertFalse(app.state.continuation.gates)

    async def test_final_delivery_failure_cannot_commit_or_credit_savings(self):
        from codex_perplexity_adapter.app import ContinuationCapture
        from codex_perplexity_adapter.status import BudgetStatus, StatusMiddleware
        manager = ContinuationManager()
        key = manager.session_key("s", "t")
        payload = self.checkpoint(manager, key)
        await manager.acquire(key)
        capture = ContinuationCapture(manager, key, manager.fingerprints(payload), [], "test")
        capture.saved_bytes = 100
        capture.commit({"id": "new", "status": "completed", "output": [answer()]})
        async def target(scope, receive, send):
            scope["state"]["continuation_lease"] = (manager, key)
            scope["state"]["continuation_outcome"] = capture
            await send({"type": "http.response.body", "body": b"done"})
        async def disconnect(message):
            raise asyncio.CancelledError()
        middleware = StatusMiddleware(target, BudgetStatus())
        with self.assertRaises(asyncio.CancelledError):
            await middleware({"type": "http", "method": "POST", "path": "/v1/responses"}, None, disconnect)
        self.assertFalse(manager.entries)
        self.assertFalse(manager.gates)
        self.assertEqual(manager.saved_bytes, 0)

    async def test_cancellation_after_final_send_still_invalidates_checkpoint(self):
        from codex_perplexity_adapter.app import ContinuationCapture
        from codex_perplexity_adapter.status import BudgetStatus, StatusMiddleware
        manager = ContinuationManager()
        key = manager.session_key("s", "t")
        payload = self.checkpoint(manager, key)
        await manager.acquire(key)
        capture = ContinuationCapture(manager, key, manager.fingerprints(payload), [], "test")
        capture.saved_bytes = 100
        capture.commit({"id": "new", "status": "completed", "output": [answer()]})
        async def target(scope, receive, send):
            scope["state"]["continuation_lease"] = (manager, key)
            scope["state"]["continuation_outcome"] = capture
            await send({"type": "http.response.body", "body": b"done"})
            raise asyncio.CancelledError()
        async def send(message):
            pass
        middleware = StatusMiddleware(target, BudgetStatus())
        with self.assertRaises(asyncio.CancelledError):
            await middleware({"type": "http", "method": "POST", "path": "/v1/responses"}, None, send)
        self.assertFalse(manager.entries)
        self.assertFalse(manager.gates)
        self.assertEqual(manager.saved_bytes, 0)

    async def test_error_classifier_is_narrow(self):
        for error in ({}, {"error": "previous_response_id"}, {"error": {"message": "connection failed"}},
                      {"error": {"code": "context_length_exceeded"}}, {"error": {"message": "previous_response_id"}}):
            self.assertFalse(prior_response_error(error))
        self.assertTrue(prior_response_error({"error": {"param": "previous_response_id"}}))

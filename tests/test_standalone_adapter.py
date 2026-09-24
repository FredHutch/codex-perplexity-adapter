import asyncio
import json
import plistlib
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from codex_perplexity_adapter.app import (
    BUILTIN_MODELS,
    ResponsesStreamNormalizer,
    Settings,
    _translate_sse,
    create_app,
)
from codex_perplexity_adapter import __version__
from codex_perplexity_adapter.cli import build_parser
from codex_perplexity_adapter.transform import ToolContext, transform_request, transform_response


class BundleMetadataTests(unittest.TestCase):
    def test_about_metadata_matches_adapter_registry(self):
        path = Path(__file__).resolve().parents[1] / "macos" / "Info.plist"
        with path.open("rb") as handle:
            info = plistlib.load(handle)
        self.assertEqual(info["CFBundleShortVersionString"], __version__)
        self.assertEqual(info["SupportedModelAliases"], [route.alias for route in BUILTIN_MODELS])


class TransformationTests(unittest.TestCase):
    def test_namespaced_function_tool_keeps_dispatch_identity(self):
        request, context = transform_request(
            {
                "model": "gpt-6-sol",
                "tools": [
                    {"type": "namespace", "name": "mcp__cua_repl", "tools": [
                        {"type": "function", "name": "js", "parameters": {"type": "object"}},
                    ]},
                    {"type": "namespace", "name": "mcp__node_repl", "tools": [
                        {"type": "function", "name": "js", "parameters": {"type": "object"}},
                    ]},
                ],
                "tool_choice": {"type": "function", "namespace": "mcp__cua_repl", "name": "js"},
                "input": [
                    {"type": "function_call", "namespace": "mcp__cua_repl", "name": "js",
                     "call_id": "call_1", "arguments": '{"code":"await cua.getState();"}'},
                    {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
                ],
            },
            "openai/gpt-6-sol",
        )
        self.assertEqual(
            [tool["name"] for tool in request["tools"]],
            ["mcp__cua_repl__js", "mcp__node_repl__js"],
        )
        self.assertEqual(request["input"][0]["name"], "mcp__cua_repl__js")
        self.assertNotIn("namespace", request["input"][0])
        self.assertEqual(request["tool_choice"], {"type": "function", "name": "mcp__cua_repl__js"})

        response = transform_response({"output": [
            {"type": "function_call", "name": "mcp__cua_repl__js",
             "call_id": "call_2", "arguments": '{"code":"await cua.getState();"}'},
        ]}, context)
        self.assertEqual(response["output"][0]["name"], "js")
        self.assertEqual(response["output"][0]["namespace"], "mcp__cua_repl")
        self.assertEqual(response["output"][0]["type"], "function_call")

    def test_codex_custom_tool_round_trip(self):
        request, custom_names = transform_request(
            {
                "model": "gpt-5.6-sol",
                "input": [
                    {"role": "user", "content": "inspect files"},
                    {
                        "type": "custom_tool_call",
                        "name": "exec",
                        "call_id": "call_1",
                        "input": "text(await tools.exec_command({cmd: 'rg --files'}))",
                    },
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call_1",
                        "output": [
                            {"type": "input_text", "text": "Output:\n"},
                            {"type": "input_text", "text": "analysis.Rmd\n"},
                        ],
                    },
                ],
                "tools": [{"type": "custom", "name": "exec", "description": "run code"}],
                "store": False,
            },
            "openai/gpt-6-sol",
        )
        self.assertEqual(request["model"], "openai/gpt-6-sol")
        self.assertIs(request["store"], False)
        self.assertEqual(request["input"][0]["type"], "message")
        self.assertEqual(request["input"][1]["type"], "function_call")
        self.assertEqual(request["input"][2]["type"], "function_call_output")
        self.assertEqual(request["input"][2]["output"], "Output:\nanalysis.Rmd\n")
        self.assertEqual(custom_names.custom_names, {"exec"})

        response = transform_response(
            {
                "output": [
                    {
                        "type": "function_call",
                        "name": "exec",
                        "call_id": "call_2",
                        "arguments": '{"content":"text(1)"}',
                    }
                ],
                "usage": {"cost": {"total_cost": 0.01}},
            },
            custom_names,
        )
        self.assertEqual(response["output"][0]["type"], "custom_tool_call")
        self.assertEqual(response["output"][0]["input"], "text(1)")
        self.assertEqual(response["usage"]["cost"], 0.01)

    def test_parallel_custom_results_survive_next_turn(self):
        calls = [
            {"type": "custom_tool_call", "name": "exec", "id": f"item_{i}",
             "call_id": f"call_{i}", "input": f"say(\"é{i}\")"}
            for i in range(2)
        ]
        outputs = [
            {"type": "custom_tool_call_output", "call_id": f"call_{i}",
             "output": [{"type": "input_text", "text": f"ok\n{i}"}]}
            for i in range(2)
        ]
        request, _ = transform_request(
            {"model": "gpt-6-sol", "input": calls + outputs,
             "tools": [{"type": "custom", "name": "exec"}]},
            "openai/gpt-6-sol",
        )
        for i, item in enumerate(request["input"][:2]):
            self.assertEqual(item["call_id"], f"call_{i}")
            self.assertEqual(json.loads(item["arguments"])["content"], f"say(\"é{i}\")")
        self.assertEqual([item["output"] for item in request["input"][2:]], ["ok\n0", "ok\n1"])

    def test_request_and_unknown_response_fields_are_preserved(self):
        request, _ = transform_request(
            {"model": "gpt-6-sol", "input": "hello", "store": False,
             "metadata": {"purpose": "test"}, "future_field": {"enabled": True}},
            "openai/gpt-6-sol",
        )
        self.assertEqual(request["future_field"], {"enabled": True})
        self.assertEqual(request["metadata"], {"purpose": "test"})
        self.assertIs(request["store"], False)
        response = {"object": "response", "future_field": {"value": 1}, "output": []}
        self.assertEqual(transform_response(response, ToolContext()), response)


class AppTests(unittest.TestCase):
    def test_overlays_and_pinned_configuration(self):
        seen = []

        async def handler(request: httpx.Request) -> httpx.Response:
            sent = json.loads(request.content)
            seen.append(sent)
            return httpx.Response(200, json={"id": "resp_1", "output": []})

        app = create_app(
            Settings(api_key="secret", model_routes=(
                "team=preset/first", "team=preset/final", "gpt-6-sol=openai/gpt-5.6-terra",
            )), transport=httpx.MockTransport(handler),
        )
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer local-adapter-token"}
            aliases = [item["id"] for item in client.get("/v1/models", headers=headers).json()["data"]]
            self.assertEqual(aliases.count("team"), 1)
            self.assertIn("gpt-5.6", aliases)
            self.assertEqual(client.post("/v1/responses", headers=headers,
                                         json={"model": "team", "input": "hi"}).status_code, 200)
            self.assertEqual(client.post("/v1/responses", headers=headers,
                                         json={"model": "gpt-6-sol", "input": "hi"}).status_code, 200)
        self.assertEqual(seen[0]["preset"], "final")
        self.assertEqual(seen[1]["model"], "openai/gpt-5.6-terra")

        pinned = create_app(Settings(api_key="secret", upstream_model="preset/team"))
        with TestClient(pinned) as client:
            aliases = [item["id"] for item in client.get("/v1/models", headers=headers).json()["data"]]
            self.assertEqual(aliases, ["gpt-5.6-sol"])
        create_app(Settings(api_key="secret", model_alias="legacy", upstream_model="other/model"))

    def test_invalid_route_combinations_and_cyber(self):
        for routes in (("broken",), ("cyber=openai/gpt-5.6-cyber",),
                       ("gpt-5.6-cyber=openai/gpt-6-sol",)):
            with self.subTest(routes=routes), self.assertRaises(ValueError):
                create_app(Settings(api_key="secret", model_routes=routes))
        with self.assertRaises(ValueError):
            create_app(Settings(api_key="secret", model_alias="team",
                                model_routes=("team=openai/gpt-6-sol",)))
        args = build_parser().parse_args(["--model-route", "team=openai/gpt-6-sol",
                                          "--model-route", "team=preset/new",
                                          "--allow-model-passthrough"])
        self.assertEqual(args.model_route[-1], "team=preset/new")
        self.assertTrue(args.allow_model_passthrough)

    def test_passthrough_validation_and_advertising(self):
        seen = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content)["model"])
            return httpx.Response(200, json={"id": "resp_1", "output": []})

        app = create_app(Settings(api_key="secret", allow_model_passthrough=True),
                         transport=httpx.MockTransport(handler))
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer local-adapter-token"}
            models = client.get("/v1/models", headers=headers).json()
            self.assertNotIn("gpt-7-future", [item["id"] for item in models["data"]])
            good = client.post("/v1/responses", headers=headers,
                               json={"model": "gpt-7-future", "input": "hi"})
            self.assertEqual(good.status_code, 200)
            for model in ("gpt-5.6-cyber", "gpt-", "gpt-bad/name", "claude-4", "gpt-a..b"):
                with self.subTest(model=model):
                    response = client.post("/v1/responses", headers=headers,
                                           json={"model": model, "input": "hi"})
                    self.assertEqual(response.status_code, 400)
        self.assertEqual(seen, ["openai/gpt-7-future"])

    def test_upstream_error_and_failed_object_are_preserved(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            sent = json.loads(request.content)
            if sent.get("metadata"):
                return httpx.Response(422, content=b'{"error":{"message":"bad field"}}',
                                      headers={"content-type": "application/json"})
            return httpx.Response(200, content=b'{"status": "failed", "error": {"code": "no_access"}}',
                                  headers={"content-type": "application/json"})

        app = create_app(Settings(api_key="secret"), transport=httpx.MockTransport(handler))
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer local-adapter-token"}
            error = client.post("/v1/responses", headers=headers,
                                json={"model": "gpt-6-sol", "input": "hi", "metadata": {"x": 1}})
            failed = client.post("/v1/responses", headers=headers,
                                 json={"model": "gpt-6-sol", "input": "hi"})
        self.assertEqual(error.status_code, 422)
        self.assertEqual(error.content, b'{"error":{"message":"bad field"}}')
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.json()["status"], "failed")
        self.assertEqual(failed.content, b'{"status": "failed", "error": {"code": "no_access"}}')

    def test_429_retries_then_streams_success(self):
        calls = []
        delays = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(json.loads(request.content))
            if len(calls) < 3:
                return httpx.Response(429, json={"error": "limited"},
                                      headers={"retry-after": "0.01"})
            return httpx.Response(200, content=b'data: [DONE]\n\n',
                                  headers={"content-type": "text/event-stream"})

        async def record_delay(seconds: float) -> None:
            delays.append(seconds)

        app = create_app(Settings(api_key="secret"), transport=httpx.MockTransport(handler))
        with patch("codex_perplexity_adapter.app.asyncio.sleep", record_delay):
            with TestClient(app) as client:
                response = client.post("/v1/responses",
                                       headers={"Authorization": "Bearer local-adapter-token"},
                                       json={"model": "gpt-6-sol", "input": "hi", "stream": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'data: [DONE]\n\n')
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual([delay for delay in delays if delay > 0], [0.01, 0.01])

    def test_429_retry_limit_preserves_error_and_retry_after(self):
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(429, content=b'{"error":"limited"}',
                                  headers={"content-type": "application/json", "retry-after": "0"})

        app = create_app(Settings(api_key="secret"), transport=httpx.MockTransport(handler))
        with TestClient(app) as client:
            response = client.post("/v1/responses",
                                   headers={"Authorization": "Bearer local-adapter-token"},
                                   json={"model": "gpt-6-sol", "input": "hi"})
        self.assertEqual(len(calls), 3)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.content, b'{"error":"limited"}')
        self.assertEqual(response.headers["retry-after"], "0")

    def test_long_retry_after_does_not_retry(self):
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(429, json={"error": "quota"},
                                  headers={"retry-after": "120"})

        app = create_app(Settings(api_key="secret"), transport=httpx.MockTransport(handler))
        with TestClient(app) as client:
            response = client.post("/v1/responses",
                                   headers={"Authorization": "Bearer local-adapter-token"},
                                   json={"model": "gpt-6-sol", "input": "hi"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(response.status_code, 429)

    def test_codex_client_metadata_is_removed_before_upstream(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            sent = json.loads(request.content)
            self.assertNotIn("client_metadata", sent)
            self.assertEqual(sent["metadata"], {"purpose": "test"})
            self.assertEqual(sent["future_field"], "preserved")
            return httpx.Response(200, json={"id": "resp_1", "status": "completed", "output": []})

        app = create_app(Settings(api_key="secret"), transport=httpx.MockTransport(handler))
        with TestClient(app) as client:
            response = client.post("/v1/responses",
                headers={"Authorization": "Bearer local-adapter-token"},
                json={"model": "gpt-6-sol", "input": "hello",
                      "client_metadata": {"source": "codex"},
                      "metadata": {"purpose": "test"}, "future_field": "preserved"})
        self.assertEqual(response.status_code, 200)

    def test_supported_models_route_to_matching_upstream_models(self):
        expected_routes = {
            "gpt-6-astra": "openai/gpt-6-astra",
            "gpt-6-sol": "openai/gpt-6-sol",
            "gpt-6-luna": "openai/gpt-6-luna",
            "gpt-5.6-sol": "openai/gpt-5.6-sol",
            "gpt-5.6-terra": "openai/gpt-5.6-terra",
            "gpt-5.6-luna": "openai/gpt-5.6-luna",
            "gpt-5.6": "openai/gpt-5.6-sol",
        }
        seen_models = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen_models.append(json.loads(request.content)["model"])
            return httpx.Response(200, json={"id": "resp_1", "status": "completed", "output": []})

        app = create_app(
            Settings(api_key="perplexity-secret"),
            transport=httpx.MockTransport(handler),
        )
        with TestClient(app) as client:
            models = client.get(
                "/v1/models",
                headers={"Authorization": "Bearer local-adapter-token"},
            )
            self.assertEqual(
                [model["id"] for model in models.json()["data"]],
                list(expected_routes),
            )
            for alias in expected_routes:
                response = client.post(
                    "/v1/responses",
                    headers={"Authorization": "Bearer local-adapter-token"},
                    json={"model": alias, "input": "hello"},
                )
                self.assertEqual(response.status_code, 200)

        self.assertEqual(seen_models, list(expected_routes.values()))

    def test_unsupported_model_is_rejected_before_upstream_request(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.fail("unsupported models must not reach the upstream API")

        app = create_app(
            Settings(api_key="perplexity-secret"),
            transport=httpx.MockTransport(handler),
        )
        with TestClient(app) as client:
            response = client.post(
                "/v1/responses",
                headers={"Authorization": "Bearer local-adapter-token"},
                json={"model": "gpt-7-unknown", "input": "hello"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["type"], "invalid_request_error")
        self.assertIn("gpt-7-unknown", response.json()["error"]["message"])
        self.assertIn("gpt-6-sol", response.json()["error"]["message"])

    def test_astra_normalizes_unsupported_request_parameters(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            sent = json.loads(request.content)
            self.assertEqual(sent["model"], "openai/gpt-6-astra")
            self.assertEqual(sent["reasoning"], {"effort": "low", "summary": "auto"})
            self.assertNotIn("temperature", sent)
            self.assertNotIn("top_p", sent)
            return httpx.Response(200, json={"id": "resp_1", "status": "completed", "output": []})

        app = create_app(
            Settings(api_key="perplexity-secret"),
            transport=httpx.MockTransport(handler),
        )
        with TestClient(app) as client:
            response = client.post(
                "/v1/responses",
                headers={"Authorization": "Bearer local-adapter-token"},
                json={
                    "model": "gpt-6-astra",
                    "input": "hello",
                    "reasoning": {"effort": "none", "summary": "auto"},
                    "temperature": 0.5,
                    "top_p": 0.9,
                },
            )

        self.assertEqual(response.status_code, 200)

    def test_compatibility_follows_resolved_upstream_model(self):
        seen = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "resp_1", "output": []})

        app = create_app(Settings(api_key="secret", model_routes=(
            "gpt-6-astra=openai/gpt-6-sol", "team-astra=openai/gpt-6-astra",
        )), transport=httpx.MockTransport(handler))
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer local-adapter-token"}
            for model in ("gpt-6-astra", "team-astra"):
                response = client.post("/v1/responses", headers=headers, json={
                    "model": model, "input": "hi", "reasoning": {"effort": "minimal"},
                    "temperature": 0.3, "top_p": 0.7, "future_field": "forwarded",
                })
                self.assertEqual(response.status_code, 200)
        self.assertEqual(seen[0]["reasoning"]["effort"], "minimal")
        self.assertEqual(seen[0]["temperature"], 0.3)
        self.assertEqual(seen[0]["top_p"], 0.7)
        self.assertEqual(seen[0]["future_field"], "forwarded")
        self.assertEqual(seen[1]["reasoning"]["effort"], "low")
        self.assertNotIn("temperature", seen[1])
        self.assertNotIn("top_p", seen[1])

    def test_cli_style_override_keeps_single_pinned_mapping(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(json.loads(request.content)["preset"], "team-coding")
            return httpx.Response(200, json={"id": "resp_1", "status": "completed", "output": []})

        app = create_app(
            Settings(
                api_key="perplexity-secret",
                model_alias="team-model",
                upstream_model="preset/team-coding",
            ),
            transport=httpx.MockTransport(handler),
        )
        with TestClient(app) as client:
            models = client.get(
                "/v1/models",
                headers={"Authorization": "Bearer local-adapter-token"},
            )
            response = client.post(
                "/v1/responses",
                headers={"Authorization": "Bearer local-adapter-token"},
                json={"model": "team-model", "input": "hello"},
            )

        self.assertEqual([model["id"] for model in models.json()["data"]], ["team-model"])
        self.assertEqual(response.status_code, 200)

    def test_non_streaming_proxy(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            sent = json.loads(request.content)
            self.assertEqual(str(request.url), "https://api.perplexity.ai/v1/responses")
            self.assertEqual(sent["model"], "openai/gpt-5.6-sol")
            self.assertEqual(request.headers["authorization"], "Bearer perplexity-secret")
            return httpx.Response(
                200,
                json={"id": "resp_1", "status": "completed", "output": []},
            )

        app = create_app(
            Settings(api_key="perplexity-secret"),
            transport=httpx.MockTransport(handler),
        )
        with TestClient(app) as client:
            response = client.post(
                "/v1/responses",
                headers={"Authorization": "Bearer local-adapter-token"},
                json={"model": "gpt-5.6-sol", "input": "hello"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], "resp_1")

    def test_api_documentation_is_disabled(self):
        app = create_app(Settings(api_key="perplexity-secret"))
        with TestClient(app) as client:
            self.assertEqual(client.get("/docs").status_code, 404)
            self.assertEqual(client.get("/openapi.json").status_code, 404)

    def test_streaming_custom_call_translation(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            body = (
                'event: response.output_item.added\n'
                'data: {"type":"response.output_item.added","output_index":0,'
                '"item":{"id":"fc_1","type":"function_call","name":"exec",'
                '"call_id":"call_2","arguments":""}}\n\n'
                'event: response.function_call_arguments.delta\n'
                'data: {"type":"response.function_call_arguments.delta","item_id":"fc_1",'
                '"output_index":0,"delta":"{\\"content\\":\\"text(1)\\"}"}\n\n'
                'event: response.function_call_arguments.done\n'
                'data: {"type":"response.function_call_arguments.done","item_id":"fc_1",'
                '"output_index":0,"arguments":"{\\"content\\":\\"text(1)\\"}"}\n\n'
                'event: response.output_item.done\n'
                'data: {"type":"response.output_item.done","output_index":0,'
                '"item":{"id":"fc_1","type":"function_call","name":"exec",'
                '"call_id":"call_2","arguments":"{\\"content\\":\\"text(1)\\"}"}}\n\n'
                'data: [DONE]\n\n'
            )
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

        app = create_app(
            Settings(api_key="perplexity-secret"),
            transport=httpx.MockTransport(handler),
        )
        with TestClient(app) as client:
            response = client.post(
                "/v1/responses",
                headers={"Authorization": "Bearer local-adapter-token"},
                json={
                    "model": "gpt-5.6-sol",
                    "input": "hello",
                    "stream": True,
                    "tools": [{"type": "custom", "name": "exec"}],
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn('"type":"custom_tool_call"', response.text)
        self.assertIn('"input":"text(1)"', response.text)
        self.assertIn('"type":"response.custom_tool_call_input.delta"', response.text)
        self.assertIn('"type":"response.custom_tool_call_input.done"', response.text)
        self.assertNotIn('"type":"response.function_call_arguments.delta"', response.text)
        self.assertNotIn('"type":"response.function_call_arguments.done"', response.text)

    def test_streaming_namespaced_tool_restores_namespace(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            upstream = json.loads(request.content)
            self.assertEqual(upstream["tools"][0]["name"], "mcp__cua_repl__js")
            item = {"id": "fc_1", "type": "function_call",
                    "name": "mcp__cua_repl__js", "call_id": "call_1", "arguments": ""}
            body = (
                "event: response.output_item.added\n"
                f"data: {json.dumps({'type': 'response.output_item.added', 'output_index': 0, 'item': item})}\n\n"
                "event: response.output_item.done\n"
                f"data: {json.dumps({'type': 'response.output_item.done', 'output_index': 0, 'item': item})}\n\n"
            )
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

        app = create_app(Settings(api_key="perplexity-secret"), transport=httpx.MockTransport(handler))
        with TestClient(app) as client:
            response = client.post(
                "/v1/responses",
                headers={"Authorization": "Bearer local-adapter-token"},
                json={"model": "gpt-6-sol", "input": "inspect the computer", "stream": True,
                      "tools": [{"type": "namespace", "name": "mcp__cua_repl", "tools": [
                          {"type": "function", "name": "js", "parameters": {"type": "object"}},
                      ]}]},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn('"namespace":"mcp__cua_repl"', response.text)
        self.assertIn('"name":"js"', response.text)
        self.assertNotIn('"name":"mcp__cua_repl__js"', response.text)


class DelayedSSEStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"type":"response.output_text.delta","delta":"first"}\n\n'
        await asyncio.sleep(0.15)
        yield b'data: {"type":"response.output_text.delta","delta":"second"}\n\n'


class IncrementalStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_sse_frame_is_byte_preserved(self):
        client = httpx.AsyncClient()
        frame = b'event: response.future.event\nid: event-1\ndata: {"type":"response.future.event", "new": 1}\n\n'
        upstream = httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=frame,
            request=httpx.Request("POST", "https://example.test/v1/responses"),
        )
        output = b"".join([chunk async for chunk in _translate_sse(upstream, ToolContext(), client)])
        self.assertEqual(output, frame)

    async def test_ordinary_function_sse_frames_are_unchanged(self):
        client = httpx.AsyncClient()
        frames = (
            b'event: response.output_item.added\ndata: {"type":"response.output_item.added","output_index":0,"item":{"id":"fc_1","type":"function_call","name":"weather","call_id":"call_1"}}\n\n'
            b'event: response.function_call_arguments.delta\ndata: {"type":"response.function_call_arguments.delta","item_id":"fc_1","delta":"{\\"city\\":\\"Paris\\"}"}\n\n'
        )
        upstream = httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=frames,
            request=httpx.Request("POST", "https://example.test/v1/responses"),
        )
        output = b"".join([chunk async for chunk in _translate_sse(upstream, ToolContext({"exec"}), client)])
        self.assertEqual(output, frames)

    async def test_translator_yields_before_upstream_finishes(self):
        client = httpx.AsyncClient()
        upstream = httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=DelayedSSEStream(),
            request=httpx.Request("POST", "https://example.test/v1/responses"),
        )
        stream = _translate_sse(upstream, ToolContext(), client)

        started = time.monotonic()
        first = await anext(stream)
        self.assertLess(time.monotonic() - started, 0.10)
        self.assertIn(b'"type":"response.output_item.added"', first)
        content_part = await anext(stream)
        self.assertIn(b'"type":"response.content_part.added"', content_part)
        first_delta = await anext(stream)
        self.assertLess(time.monotonic() - started, 0.10)
        self.assertIn(b'"delta":"first"', first_delta)

        remaining = b""
        async for chunk in stream:
            remaining += chunk
        self.assertIn(b'"delta":"second"', remaining)
        self.assertGreaterEqual(time.monotonic() - started, 0.14)


class StreamSchemaTests(unittest.TestCase):
    def _add_custom_call(
        self,
        normalizer: ResponsesStreamNormalizer,
        *,
        item_id: str,
        name: str,
        output_index: int,
    ) -> list[dict]:
        return normalizer.normalize(
            {
                "type": "response.output_item.added",
                "output_index": output_index,
                "item": {
                    "id": item_id,
                    "type": "function_call",
                    "name": name,
                    "call_id": f"call_{output_index}",
                    "arguments": "",
                },
            }
        )

    def test_streamed_custom_tool_arguments_are_restored_for_codex(self):
        tool_name = "mcp__cua_repl__js"
        normalizer = ResponsesStreamNormalizer(ToolContext({tool_name}))
        added = self._add_custom_call(
            normalizer,
            item_id="fc_chrome",
            name=tool_name,
            output_index=0,
        )
        self.assertEqual(added[0]["item"]["type"], "custom_tool_call")

        arguments = json.dumps(
            {"content": 'await tab.typeText("héllo \\u2603")'},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        split_at = arguments.index("hé") + 1
        first_delta = arguments[:split_at]
        second_delta = arguments[split_at:]
        self.assertEqual(
            normalizer.normalize(
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "fc_chrome",
                    "output_index": 0,
                    "delta": first_delta,
                }
            ),
            [],
        )
        self.assertEqual(
            normalizer.normalize(
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "fc_chrome",
                    "output_index": 0,
                    "delta": second_delta,
                }
            ),
            [],
        )
        completed = normalizer.normalize(
            {
                "type": "response.function_call_arguments.done",
                "item_id": "fc_chrome",
                "output_index": 0,
                "arguments": arguments,
            }
        )

        self.assertEqual(
            [event["type"] for event in completed],
            [
                "response.custom_tool_call_input.delta",
                "response.custom_tool_call_input.done",
            ],
        )
        self.assertEqual(completed[0]["delta"], 'await tab.typeText("héllo \\u2603")')
        self.assertEqual(completed[1]["input"], 'await tab.typeText("héllo \\u2603")')

    def test_parallel_custom_tool_arguments_do_not_mix(self):
        normalizer = ResponsesStreamNormalizer(ToolContext({"exec", "mcp__cua_repl__js"}))
        self._add_custom_call(normalizer, item_id="fc_exec", name="exec", output_index=0)
        self._add_custom_call(
            normalizer,
            item_id="fc_chrome",
            name="mcp__cua_repl__js",
            output_index=1,
        )

        for item_id, output_index, delta in (
            ("fc_exec", 0, '{"content":"text('),
            ("fc_chrome", 1, '{"content":"await cua.'),
            ("fc_exec", 0, '1)"}'),
            ("fc_chrome", 1, 'getState()"}'),
        ):
            self.assertEqual(
                normalizer.normalize(
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": item_id,
                        "output_index": output_index,
                        "delta": delta,
                    }
                ),
                [],
            )

        exec_done = normalizer.normalize(
            {
                "type": "response.function_call_arguments.done",
                "item_id": "fc_exec",
                "output_index": 0,
            }
        )
        chrome_done = normalizer.normalize(
            {
                "type": "response.function_call_arguments.done",
                "item_id": "fc_chrome",
                "output_index": 1,
            }
        )
        self.assertEqual(exec_done[-1]["input"], "text(1)")
        self.assertEqual(chrome_done[-1]["input"], "await cua.getState()")

    def test_custom_done_with_call_id_and_missing_coordinates(self):
        normalizer = ResponsesStreamNormalizer(ToolContext({"exec"}))
        normalizer.normalize({
            "type": "response.output_item.added", "output_index": 2,
            "item": {"id": "fc_1", "type": "function_call", "name": "exec",
                     "call_id": "call_1", "arguments": ""},
        })
        self.assertEqual(normalizer.normalize({
            "type": "response.function_call_arguments.delta", "call_id": "call_1",
            "delta": '{"cont',
        }), [])
        self.assertEqual(normalizer.normalize({
            "type": "response.function_call_arguments.delta", "call_id": "call_1",
            "delta": 'ent":"hi"}',
        }), [])
        done = normalizer.normalize({
            "type": "response.function_call_arguments.done", "call_id": "call_1",
        })
        self.assertEqual(done[-1]["item_id"], "fc_1")
        self.assertEqual(done[-1]["output_index"], 2)
        self.assertEqual(done[-1]["input"], "hi")

    def test_completed_custom_item_synthesizes_input_events_when_arguments_are_not_streamed(self):
        normalizer = ResponsesStreamNormalizer(ToolContext({"echo"}))
        item = {"id": "fc_echo", "type": "function_call", "name": "echo",
                "call_id": "call_echo", "arguments": '{"content":"PING"}'}
        added = normalizer.normalize({"type": "response.output_item.added",
                                      "output_index": 1, "item": item})
        self.assertEqual(added[0]["item"]["input"], "PING")
        completed = normalizer.normalize({"type": "response.output_item.done",
                                          "output_index": 1, "item": item})
        self.assertEqual([event["type"] for event in completed], [
            "response.custom_tool_call_input.delta",
            "response.custom_tool_call_input.done",
            "response.output_item.done",
        ])
        self.assertEqual(completed[0]["delta"], "PING")
        self.assertEqual(completed[1]["input"], "PING")
        self.assertEqual(completed[2]["item"]["type"], "custom_tool_call")

        self.assertEqual(normalizer.normalize({"type": "response.function_call_arguments.done",
                                               "item_id": "fc_echo", "arguments": item["arguments"]}), [])

    def test_ordinary_function_argument_events_are_unchanged(self):
        normalizer = ResponsesStreamNormalizer(ToolContext({"exec"}))
        added = normalizer.normalize(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "id": "fc_weather",
                    "type": "function_call",
                    "name": "get_weather",
                    "call_id": "call_weather",
                    "arguments": "",
                },
            }
        )
        self.assertEqual(added[0]["item"]["type"], "function_call")

        delta = normalizer.normalize(
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_weather",
                "output_index": 0,
                "delta": '{"city":"Sea',
            }
        )
        self.assertEqual(delta[0]["type"], "response.function_call_arguments.delta")
        self.assertEqual(delta[0]["delta"], '{"city":"Sea')

    def test_malformed_message_item_is_made_valid_for_codex(self):
        normalizer = ResponsesStreamNormalizer(ToolContext())
        events = normalizer.normalize(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"id": "msg_1", "type": "message", "status": "in_progress"},
            }
        )
        self.assertEqual(
            events[0]["item"],
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [],
            },
        )

    def test_delta_without_item_gets_active_message_first(self):
        normalizer = ResponsesStreamNormalizer(ToolContext())
        events = normalizer.normalize(
            {"type": "response.output_text.delta", "output_index": 0, "delta": "Hello"}
        )
        self.assertEqual(
            [event["type"] for event in events],
            [
                "response.output_item.added",
                "response.content_part.added",
                "response.output_text.delta",
            ],
        )
        self.assertEqual(events[0]["item"]["role"], "assistant")

    def test_missing_content_part_events_are_synthesized(self):
        normalizer = ResponsesStreamNormalizer(ToolContext())
        normalizer.normalize(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"id": "msg_1", "type": "message", "content": []},
            }
        )
        delta_events = normalizer.normalize(
            {"type": "response.output_text.delta", "output_index": 0, "delta": "Hello"}
        )
        self.assertEqual(
            [event["type"] for event in delta_events],
            ["response.content_part.added", "response.output_text.delta"],
        )
        self.assertEqual(delta_events[1]["item_id"], "msg_1")
        self.assertEqual(delta_events[1]["content_index"], 0)
        self.assertEqual([event["sequence_number"] for event in delta_events], [1, 2])

        done_events = normalizer.normalize(
            {"type": "response.output_text.done", "output_index": 0, "text": "Hello"}
        )
        self.assertEqual(
            [event["type"] for event in done_events],
            ["response.output_text.done", "response.content_part.done"],
        )


if __name__ == "__main__":
    unittest.main()

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
        self.assertEqual(info["CFBundleVersion"], __version__)
        self.assertEqual(info["SupportedModelAliases"], [route.alias for route in BUILTIN_MODELS])


class TransformationTests(unittest.TestCase):
    def test_image_tool_results_preserve_content_and_replay(self):
        for result_type in ('custom_tool_call_output', 'function_call_output'):
            for detail in ('original', 'high', 'low', 'auto', None):
                with self.subTest(result_type=result_type, detail=detail):
                    image = {'type': 'input_image', 'image_url': 'data:image/png;base64,TEST',
                             '_meta': {'codex/imageDetail': 'original'}}
                    if detail is not None:
                        image['detail'] = detail
                    parts = [{'type': 'input_text', 'text': 'Before\n'}, image, 'Between',
                             {'type': 'input_image', 'image_url': 'https://example.com/image.png'},
                             {'future_part': 42}, {'type': 'text', 'text': 'After'}]
                    payload = {'input': [
                        {'type': result_type, 'call_id': 'call_image', 'output': parts},
                        {'type': result_type, 'call_id': 'call_text', 'output': 'plain'},
                    ]}
                    original = json.loads(json.dumps(payload))
                    request, _ = transform_request(payload, 'openai/gpt-6.1-sol')
                    output = request['input'][0]['output']
                    expected_image = {k: v for k, v in image.items() if k != '_meta'}
                    if detail == 'original':
                        expected_image['detail'] = 'high'
                    self.assertEqual(output, [parts[0], expected_image,
                        {'type': 'input_text', 'text': 'Between'}, parts[3],
                        {'type': 'input_text', 'text': '{"future_part":42}'},
                        {'type': 'input_text', 'text': 'After'}])
                    self.assertEqual([item['call_id'] for item in request['input']],
                                     ['call_image', 'call_text'])
                    replay, _ = transform_request(request, 'openai/gpt-6.1-sol')
                    self.assertEqual(replay, request)
                    self.assertEqual(payload, original)

    def test_image_only_and_text_only_tool_results(self):
        image = {'type': 'input_image', 'image_url': 'data:image/png;base64,TEST'}
        for result_type in ('custom_tool_call_output', 'function_call_output'):
            for output in ([image], 'plain', None, [],
                           [{'type': 'input_text', 'text': 'one'}, 'two'],
                           '{"type":"input_image","image_url":"text only"}'):
                with self.subTest(result_type=result_type, output=output):
                    request, _ = transform_request({'input': [
                        {'type': result_type, 'call_id': 'call_1', 'output': output}
                    ]}, 'openai/gpt-6.1-sol')
                    expected = output
                    if result_type == 'custom_tool_call_output':
                        if output is None or output == []:
                            expected = ''
                        elif isinstance(output, list) and output != [image]:
                            expected = 'onetwo'
                    self.assertEqual(request['input'][0]['output'], expected)

    def test_user_images_remain_unchanged(self):
        message = {'type': 'message', 'role': 'user', 'content': [
            {'type': 'input_image', 'image_url': 'data:image/png;base64,TEST', 'detail': 'original'}]}
        request, _ = transform_request({'input': [message]}, 'openai/gpt-6.1-sol')
        self.assertEqual(request['input'], [message])

    def test_latest_developer_mode_supersedes_replayed_modes(self):
        plan = '<collaboration_mode># Plan Mode (Conversational)\nDo not edit files.\n</collaboration_mode>'
        default = '<collaboration_mode># Collaboration Mode: Default\nExecute authorized work.\n</collaboration_mode>'
        for modes in ([plan, default], [default, plan], [plan, default, plan]):
            for blocked in (False, True):
                with self.subTest(modes=modes, blocked=blocked):
                    def content(text):
                        return [{'type': 'input_text', 'text': text}] if blocked else text
                    history = [{'role': 'developer', 'content': content('Keep permissions.\n' + modes[0] + '\nKeep tools.')} ]
                    for mode in modes[1:]:
                        history += [{'role': 'assistant', 'content': 'A previous reply.'},
                                    {'role': 'developer', 'content': content(mode)}]
                    original = json.loads(json.dumps(history))
                    request, _ = transform_request({'model': 'gpt-6.1-sol', 'input': history}, 'openai/gpt-6.1-sol')
                    developers = [i['content'] for i in request['input'] if i.get('role') == 'developer']
                    text = '\n'.join(c[0]['text'] if blocked else c for c in developers)
                    self.assertEqual(text.count('<collaboration_mode>'), 1)
                    self.assertIn(modes[-1], text)
                    self.assertIn('Keep permissions.', text)
                    self.assertIn('Keep tools.', text)
                    self.assertEqual(history, original)

    def test_mode_normalization_ignores_untrusted_content_and_incomplete_blocks(self):
        plan = '<collaboration_mode># Plan Mode\nDo not edit.\n</collaboration_mode>'
        default = '<collaboration_mode># Collaboration Mode: Default\nExecute.\n</collaboration_mode>'
        history = [
            {'type': 'message', 'role': 'developer', 'content': plan},
            {'type': 'message', 'role': 'user', 'content': default},
            {'type': 'message', 'role': 'assistant', 'content': default},
            {'type': 'function_call_output', 'call_id': 'call_1', 'output': default},
            {'type': 'message', 'role': 'developer', 'content': '<collaboration_mode># Collaboration Mode: Default\nUnclosed'},
        ]
        request, _ = transform_request({'input': history}, 'openai/gpt-6.1-sol')
        self.assertEqual(request['input'], history)

    def test_multiple_mode_blocks_in_one_message_preserve_other_blocks(self):
        plan = '<collaboration_mode># Plan Mode\nDo not edit.\n</collaboration_mode>'
        default = '<collaboration_mode># Collaboration Mode: Default\nUse the latest <collaboration_mode>...</collaboration_mode> instructions.\n</collaboration_mode>'
        request, _ = transform_request({'input': [
            {'role': 'developer', 'content': [{'type': 'input_text', 'text': plan},
                                            {'type': 'input_text', 'text': 'Keep permission rules.'}]},
            {'role': 'developer', 'content': plan + default},
        ]}, 'openai/gpt-6.1-sol')
        self.assertEqual(request['input'][0]['content'], [{'type': 'input_text', 'text': 'Keep permission rules.'}])
        self.assertEqual(request['input'][1]['content'], default)

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
    def test_images_reach_upstream_in_json_and_stream_requests(self):
        for result_type in ('custom_tool_call_output', 'function_call_output'):
            for stream in (False, True):
                for status in (200, 400):
                    with self.subTest(result_type=result_type, stream=stream, status=status):
                        async def handler(request):
                            sent = json.loads(request.content)
                            result = sent['input'][1]
                            self.assertEqual(result['call_id'], 'call_1')
                            self.assertEqual(result['output'], [
                                {'type': 'input_text', 'text': 'Image follows'},
                                {'type': 'input_image', 'image_url': 'data:image/png;base64,TEST',
                                 'detail': 'high'}])
                            if status == 400:
                                return httpx.Response(400, json={'error': {'message': 'image rejected'}})
                            response = {'status': 'completed', 'output': []}
                            if stream:
                                return httpx.Response(200, text='data: ' + json.dumps(
                                    {'type': 'response.completed', 'response': response}) + '\n\n',
                                    headers={'content-type': 'text/event-stream'})
                            return httpx.Response(200, json=response)
                        call = {'type': 'function_call', 'name': 'inspect',
                                'call_id': 'call_1', 'arguments': '{}'}
                        if result_type == 'custom_tool_call_output':
                            call = {'type': 'custom_tool_call', 'name': 'inspect',
                                    'call_id': 'call_1', 'input': 'inspect()'}
                        payload = {'model': 'gpt-6.1-sol', 'stream': stream, 'input': [call,
                            {'type': result_type, 'call_id': 'call_1', 'output': [
                                {'type': 'input_text', 'text': 'Image follows'},
                                {'type': 'input_image', 'image_url': 'data:image/png;base64,TEST',
                                 'detail': 'original'}]}]}
                        with TestClient(create_app(Settings(budget_notices=False, api_key='test'),
                                        transport=httpx.MockTransport(handler))) as client:
                            response = client.post('/v1/responses', json=payload,
                                headers={'Authorization': 'Bearer local-adapter-token'})
                        self.assertEqual(response.status_code, status)
                        if status == 400:
                            self.assertEqual(response.json()['error']['message'], 'image rejected')

    def test_long_mode_replay_reaches_upstream_without_stale_restrictions(self):
        plan = '<collaboration_mode># Plan Mode (Conversational)\nDo not edit files.\n</collaboration_mode>'
        default = '<collaboration_mode># Collaboration Mode: Default\nExecute authorized work.\n</collaboration_mode>'
        history = [{'role': 'developer', 'content': plan}]
        for index in range(30):
            history.extend([
                {'role': 'user', 'content': f'Implement step {index}.'},
                {'role': 'assistant', 'content': 'I am still in Plan mode.'},
                {'type': 'function_call', 'name': 'inspect', 'call_id': f'call_{index}', 'arguments': '{}'},
                {'type': 'function_call_output', 'call_id': f'call_{index}', 'output': f'Inspected step {index}.'},
            ])
        history.extend([
            {'role': 'developer', 'content': default},
            {'role': 'user', 'content': 'Implement the approved plan now.'},
        ])
        original = json.loads(json.dumps(history))
        seen = []

        async def handler(request):
            seen.append(json.loads(request.content))
            if seen[-1].get('stream'):
                event = {'type': 'response.completed', 'response': {'status': 'completed', 'output': []}}
                return httpx.Response(200, headers={'Content-Type': 'text/event-stream'},
                                      text='data: ' + json.dumps(event) + '\n\n')
            return httpx.Response(200, json={'status': 'completed', 'output': []})

        with TestClient(create_app(Settings(budget_notices=False, api_key='test'), transport=httpx.MockTransport(handler))) as client:
            for stream in (False, True):
                response = client.post('/v1/responses', headers={'Authorization': 'Bearer local-adapter-token'},
                                       json={'model': 'gpt-6.1-sol', 'input': history, 'stream': stream})
                self.assertEqual(response.status_code, 200)
                # Only the obsolete mode message is removed: all replies and
                # call/result pairs retain their order and identity.
                expected = [{**item, 'type': item.get('type', 'message')} for item in history[1:]]
                self.assertEqual(seen[-1]['input'], expected)
                self.assertNotIn('Do not edit files.', json.dumps(seen[-1]))
        self.assertEqual(history, original)

    def test_gpt61_reasoning_for_all_route_sources(self):
        configurations = [
            (Settings(budget_notices=False, api_key="test"), "gpt-6.1"),
            (Settings(budget_notices=False, api_key="test"), "gpt-6.1-sol"),
            (Settings(budget_notices=False, api_key="test", model_routes=("team=openai/gpt-6.1-sol",)), "team"),
            (Settings(budget_notices=False, api_key="test", model_alias="pinned", upstream_model="openai/gpt-6.1-sol"), "pinned"),
            # A pinned registry excludes the built-ins, exercising passthrough.
            (Settings(budget_notices=False, api_key="test", model_alias="other", allow_model_passthrough=True), "gpt-6.1-sol"),
        ]
        for settings, alias in configurations:
            seen = []

            async def handler(request):
                seen.append(json.loads(request.content))
                return httpx.Response(200, json={"status": "completed", "output": []})

            with self.subTest(alias=alias, settings=settings):
                with TestClient(create_app(settings, transport=httpx.MockTransport(handler))) as client:
                    self.assertEqual(client.app.version, __version__)
                    for effort in ("none", "minimal", "low", "medium", "high", "xhigh", "max", None):
                        payload = {"model": alias, "input": "hello", "temperature": 0.4,
                                   "top_p": 0.8, "future_field": {"enabled": True}}
                        if effort is not None:
                            payload["reasoning"] = {"effort": effort, "summary": "auto"}
                        response = client.post("/v1/responses", json=payload,
                                               headers={"Authorization": "Bearer local-adapter-token"})
                        self.assertEqual(response.status_code, 200)
                        sent = seen[-1]
                        self.assertEqual(sent["model"], "openai/gpt-6.1-sol")
                        if effort is None:
                            self.assertNotIn("reasoning", sent)
                        else:
                            self.assertEqual(sent["reasoning"], {
                                "effort": "low" if effort in {"none", "minimal"} else effort,
                                "summary": "auto",
                            })
                        self.assertNotIn("temperature", sent)
                        self.assertNotIn("top_p", sent)
                        self.assertEqual(sent["future_field"], payload["future_field"])

    def test_gpt61_override_to_other_upstream_stays_transparent(self):
        async def handler(request):
            sent = json.loads(request.content)
            self.assertEqual(sent["model"], "openai/gpt-6-sol")
            self.assertEqual(sent["reasoning"], {"effort": "minimal"})
            self.assertEqual(sent["temperature"], 0.4)
            self.assertEqual(sent["top_p"], 0.8)
            return httpx.Response(200, json={"output": []})

        settings = Settings(budget_notices=False, api_key="test", model_routes=("gpt-6.1=openai/gpt-6-sol",))
        with TestClient(create_app(settings, transport=httpx.MockTransport(handler))) as client:
            response = client.post("/v1/responses", headers={"Authorization": "Bearer local-adapter-token"},
                                   json={"model": "gpt-6.1", "input": "hello",
                                         "reasoning": {"effort": "minimal"}, "temperature": 0.4, "top_p": 0.8})
        self.assertEqual(response.status_code, 200)

    def test_gpt61_tools_and_followup_json_and_stream(self):
        for alias in ("gpt-6.1", "gpt-6.1-sol"):
            for stream in (False, True):
                seen = []
                items = [
                    {"type": "function_call", "id": "fc_1", "call_id": "call_1",
                     "name": "probe__echo", "arguments": '{"text":"ok"}'},
                    {"type": "function_call", "id": "fc_2", "call_id": "call_2",
                     "name": "exec", "arguments": '{"content":"text(1)"}'},
                ]

                async def handler(request):
                    sent = json.loads(request.content)
                    seen.append(sent)
                    self.assertEqual(sent["model"], "openai/gpt-6.1-sol")
                    self.assertEqual(sent["reasoning"]["effort"], "low")
                    self.assertNotIn("client_metadata", sent)
                    self.assertEqual([t["name"] for t in sent["tools"]], ["probe__echo", "exec"])
                    data = {"id": "resp_1", "status": "completed", "output": items}
                    if sent.get("stream"):
                        events = [{"type": "response.output_item.done", "output_index": i, "item": item}
                                  for i, item in enumerate(items)]
                        events.append({"type": "response.completed", "response": data})
                        return httpx.Response(200, text="".join(
                            "data: " + json.dumps(event) + "\n\n" for event in events),
                            headers={"content-type": "text/event-stream"})
                    return httpx.Response(200, json=data)

                with self.subTest(alias=alias, stream=stream):
                    with TestClient(create_app(Settings(budget_notices=False, api_key="test"),
                                               transport=httpx.MockTransport(handler))) as client:
                        payload = {"model": alias, "input": "use both tools", "stream": stream,
                                   "client_metadata": {"test": True}, "reasoning": {"effort": "none"},
                                   "tools": [
                                       {"type": "namespace", "name": "probe", "tools": [
                                           {"type": "function", "name": "echo", "parameters": {"type": "object"}},
                                       ]}, {"type": "custom", "name": "exec"},
                                   ]}
                        headers = {"Authorization": "Bearer local-adapter-token"}
                        response = client.post("/v1/responses", headers=headers, json=payload)
                        self.assertEqual(response.status_code, 200)
                        if stream:
                            events = [json.loads(line[6:]) for line in response.text.splitlines()
                                      if line.startswith("data: ")]
                            data = next(e["response"] for e in events if e["type"] == "response.completed")
                            self.assertIn("response.custom_tool_call_input.done", [e["type"] for e in events])
                        else:
                            data = response.json()
                        calls = data["output"]
                        self.assertEqual((calls[0]["name"], calls[0]["namespace"]), ("echo", "probe"))
                        self.assertEqual(calls[1]["type"], "custom_tool_call")
                        self.assertEqual(calls[1]["input"], "text(1)")
                        payload["input"] = [*calls,
                            {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
                            {"type": "custom_tool_call_output", "call_id": "call_2",
                             "output": [{"type": "input_text", "text": "1"}]},
                        ]
                        followup = client.post("/v1/responses", headers=headers, json=payload)
                        self.assertEqual(followup.status_code, 200)
                        history = seen[-1]["input"]
                        self.assertEqual(history[0]["name"], "probe__echo")
                        self.assertNotIn("namespace", history[0])
                        self.assertEqual(history[1]["type"], "function_call")
                        self.assertEqual(json.loads(history[1]["arguments"]), {"content": "text(1)"})
                        self.assertEqual(history[3]["type"], "function_call_output")
                        self.assertEqual(history[3]["output"], "1")

    def test_overlays_and_pinned_configuration(self):
        seen = []

        async def handler(request: httpx.Request) -> httpx.Response:
            sent = json.loads(request.content)
            seen.append(sent)
            return httpx.Response(200, json={"id": "resp_1", "output": []})

        app = create_app(
            Settings(budget_notices=False, api_key="secret", model_routes=(
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

        pinned = create_app(Settings(budget_notices=False, api_key="secret", upstream_model="preset/team"))
        with TestClient(pinned) as client:
            aliases = [item["id"] for item in client.get("/v1/models", headers=headers).json()["data"]]
            self.assertEqual(aliases, ["gpt-5.6-sol"])
        create_app(Settings(budget_notices=False, api_key="secret", model_alias="legacy", upstream_model="other/model"))

    def test_invalid_route_combinations_and_cyber(self):
        for routes in (("broken",), ("cyber=openai/gpt-5.6-cyber",),
                       ("gpt-5.6-cyber=openai/gpt-6-sol",)):
            with self.subTest(routes=routes), self.assertRaises(ValueError):
                create_app(Settings(budget_notices=False, api_key="secret", model_routes=routes))
        with self.assertRaises(ValueError):
            create_app(Settings(budget_notices=False, api_key="secret", model_alias="team",
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

        app = create_app(Settings(budget_notices=False, api_key="secret", allow_model_passthrough=True),
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

        app = create_app(Settings(budget_notices=False, api_key="secret"), transport=httpx.MockTransport(handler))
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

        app = create_app(Settings(budget_notices=False, api_key="secret"), transport=httpx.MockTransport(handler))
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

        app = create_app(Settings(budget_notices=False, api_key="secret"), transport=httpx.MockTransport(handler))
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

        app = create_app(Settings(budget_notices=False, api_key="secret"), transport=httpx.MockTransport(handler))
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

        app = create_app(Settings(budget_notices=False, api_key="secret"), transport=httpx.MockTransport(handler))
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
            "gpt-6.1-sol": "openai/gpt-6.1-sol",
            "gpt-6.1": "openai/gpt-6.1-sol",
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
            Settings(budget_notices=False, api_key="perplexity-secret"),
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
            Settings(budget_notices=False, api_key="perplexity-secret"),
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
            Settings(budget_notices=False, api_key="perplexity-secret"),
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

        app = create_app(Settings(budget_notices=False, api_key="secret", model_routes=(
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
            Settings(budget_notices=False,
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
            Settings(budget_notices=False, api_key="perplexity-secret"),
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
        app = create_app(Settings(budget_notices=False, api_key="perplexity-secret"))
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
            Settings(budget_notices=False, api_key="perplexity-secret"),
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

        app = create_app(Settings(budget_notices=False, api_key="perplexity-secret"), transport=httpx.MockTransport(handler))
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

    async def test_ordinary_function_sse_payloads_preserved_with_adapter_sequence(self):
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
        original = [json.loads(line[5:]) for line in frames.decode().splitlines() if line.startswith('data:')]
        translated = [json.loads(line[5:]) for line in output.decode().splitlines() if line.startswith('data:')]
        self.assertEqual([event.pop('sequence_number') for event in translated], [0, 1])
        self.assertEqual(translated, original)

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

    async def test_live_shaped_mixed_stream_closes_commentary_and_renumbers_tools(self):
        tool = {'id': 'fc_1', 'type': 'function_call', 'name': 'inspect', 'call_id': 'call_1', 'arguments': '{}'}
        frames = [
            {'type': 'response.created', 'sequence_number': 0},
            {'type': 'response.output_item.added', 'output_index': 0, 'sequence_number': 1,
             'item': {'id': 'msg_0', 'type': 'message', 'role': 'assistant', 'content': []}},
            {'type': 'response.output_text.delta', 'output_index': 0, 'item_id': 'msg_0',
             'sequence_number': 2, 'delta': 'Checking the fixture.'},
            {'type': 'response.output_item.added', 'output_index': 1, 'sequence_number': 10, 'item': tool},
            {'type': 'response.function_call_arguments.done', 'output_index': 1, 'item_id': 'fc_1',
             'sequence_number': 11, 'arguments': '{}'},
            {'type': 'response.output_item.done', 'output_index': 1, 'sequence_number': 12, 'item': tool},
            {'type': 'response.completed', 'sequence_number': 13,
             'response': {'status': 'completed', 'output': [tool]}},
        ]
        upstream = httpx.Response(200, content=''.join('data: ' + json.dumps(event) + '\n\n' for event in frames),
                                  request=httpx.Request('POST', 'https://example.test/v1/responses'))
        client = httpx.AsyncClient()
        raw = b''.join([chunk async for chunk in _translate_sse(upstream, ToolContext(), client)])
        events = [json.loads(line[5:]) for line in raw.decode().splitlines() if line.startswith('data:')]
        self.assertEqual([event['sequence_number'] for event in events], list(range(len(events))))
        output = events[-1]['response']['output']
        self.assertEqual([item['id'] for item in output], ['msg_0', 'fc_1'])
        self.assertEqual(output[0]['phase'], 'commentary')
        self.assertEqual(output[0]['content'][0]['text'], 'Checking the fixture.')
        self.assertEqual(output[1], tool)
        done_position = next(i for i, event in enumerate(events) if event['type'] == 'response.output_item.done' and event['item']['id'] == 'msg_0')
        call_position = next(i for i, event in enumerate(events) if event['type'] == 'response.output_item.added' and event['item']['id'] == 'fc_1')
        self.assertLess(done_position, call_position)


class StreamSchemaTests(unittest.TestCase):
    def _message(self, index=0, text=None, phase=None):
        item = {'id': f'msg_{index}', 'type': 'message', 'role': 'assistant', 'content': []}
        if text is not None:
            item['content'] = [{'type': 'output_text', 'text': text, 'annotations': []}]
        if phase is not None:
            item['phase'] = phase
        return item

    def _tool(self, index=1):
        return {'id': f'fc_{index}', 'type': 'function_call', 'name': 'inspect',
                'call_id': f'call_{index}', 'arguments': '{}'}

    def _stream_message(self, normalizer, index, text, phase=None):
        return normalizer.normalize({'type': 'response.output_item.added', 'output_index': index,
                                     'item': self._message(index, phase=phase)}) + normalizer.normalize({
            'type': 'response.output_text.delta', 'output_index': index,
            'item_id': f'msg_{index}', 'delta': text})

    def test_missing_commentary_is_completed_before_tool_and_replayed(self):
        normalizer = ResponsesStreamNormalizer(ToolContext())
        events = self._stream_message(normalizer, 0, 'Checking the fixture.')
        tool = self._tool()
        boundary = normalizer.normalize({'type': 'response.output_item.added', 'output_index': 1, 'item': tool})
        self.assertEqual([event['type'] for event in boundary], [
            'response.output_text.done', 'response.content_part.done',
            'response.output_item.done', 'response.output_item.added'])
        events += boundary + normalizer.normalize({'type': 'response.output_item.done', 'output_index': 1, 'item': tool})
        events += normalizer.normalize({'type': 'response.completed', 'response': {'status': 'completed', 'output': [tool]}})
        output = events[-1]['response']['output']
        self.assertEqual([item['id'] for item in output], ['msg_0', 'fc_1'])
        self.assertEqual(output[0]['phase'], 'commentary')
        self.assertEqual(output[0]['content'][0]['text'], 'Checking the fixture.')
        self.assertEqual(output[1], tool)
        self.assertEqual([event['sequence_number'] for event in events], list(range(len(events))))
        history, _ = transform_request({'input': output + [{'type': 'function_call_output', 'call_id': 'call_1', 'output': 'PASS'}]}, 'openai/gpt-6.1-sol')
        self.assertEqual(history['input'][0], output[0])
        self.assertEqual(history['input'][1]['call_id'], history['input'][2]['call_id'])

    def test_phase_present_only_at_start_survives_completion(self):
        for phase in ('commentary', 'final_answer'):
            with self.subTest(phase=phase):
                normalizer = ResponsesStreamNormalizer(ToolContext())
                self._stream_message(normalizer, 0, 'Hello', phase)
                done = normalizer.normalize({'type': 'response.output_item.done', 'output_index': 0, 'item': self._message(text='Hello')})
                self.assertEqual(done[-1]['item']['phase'], phase)
                completed = normalizer.normalize({'type': 'response.completed', 'response': {'status': 'completed', 'output': [self._message(text='Hello')]}})
                self.assertEqual(completed[-1]['response']['output'][0]['phase'], phase)

    def test_unlabelled_done_waits_for_tool_or_successful_completion(self):
        for tool_follows in (False, True):
            with self.subTest(tool_follows=tool_follows):
                normalizer = ResponsesStreamNormalizer(ToolContext())
                self._stream_message(normalizer, 0, 'Hello')
                self.assertEqual(normalizer.normalize({'type': 'response.output_item.done', 'output_index': 0,
                                                      'item': self._message(text='Hello')}), [])
                if tool_follows:
                    events = normalizer.normalize({'type': 'response.output_item.added', 'output_index': 1, 'item': self._tool()})
                else:
                    events = normalizer.normalize({'type': 'response.completed', 'response': {'status': 'completed', 'output': []}})
                message = next(event['item'] for event in events if event['type'] == 'response.output_item.done')
                self.assertEqual(message['phase'], 'commentary' if tool_follows else 'final_answer')

    def test_existing_lifecycle_events_are_not_duplicated(self):
        normalizer = ResponsesStreamNormalizer(ToolContext())
        events = self._stream_message(normalizer, 0, 'Hello', 'final_answer')
        payloads = [
            {'type': 'response.output_text.done', 'output_index': 0, 'text': 'Hello'},
            {'type': 'response.content_part.done', 'output_index': 0, 'part': self._message(text='Hello')['content'][0]},
            {'type': 'response.output_item.done', 'output_index': 0, 'item': self._message(text='Hello')},
        ]
        for payload in payloads + payloads:
            events += normalizer.normalize(payload)
        events += normalizer.normalize({'type': 'response.completed', 'response': {'status': 'completed', 'output': [self._message(text='Hello')]}})
        for event_type in ('response.output_text.done', 'response.content_part.done', 'response.output_item.done'):
            self.assertEqual(sum(event['type'] == event_type for event in events), 1, event_type)
        self.assertEqual(len(events[-1]['response']['output']), 1)

    def test_multiple_messages_and_tools_preserve_order_and_content_parts(self):
        normalizer = ResponsesStreamNormalizer(ToolContext())
        events = self._stream_message(normalizer, 0, 'First')
        for index in (1, 2):
            events += normalizer.normalize({'type': 'response.output_item.added', 'output_index': index, 'item': self._tool(index)})
            events += normalizer.normalize({'type': 'response.output_item.done', 'output_index': index, 'item': self._tool(index)})
        events += self._stream_message(normalizer, 3, 'Last')
        events += normalizer.normalize({'type': 'response.output_text.delta', 'output_index': 3, 'content_index': 1,
                                       'item_id': 'msg_3', 'delta': ' second part'})
        events += normalizer.normalize({'type': 'response.completed', 'response': {'status': 'completed', 'output': [self._tool(1), self._tool(2)]}})
        output = events[-1]['response']['output']
        self.assertEqual([item['id'] for item in output], ['msg_0', 'fc_1', 'fc_2', 'msg_3'])
        self.assertEqual([output[0]['phase'], output[3]['phase']], ['commentary', 'final_answer'])
        self.assertEqual([part['text'] for part in output[3]['content']], ['Last', ' second part'])
        self.assertEqual([event['sequence_number'] for event in events], list(range(len(events))))

    def test_terminal_only_message_gets_complete_lifecycle(self):
        normalizer = ResponsesStreamNormalizer(ToolContext())
        events = normalizer.normalize({'type': 'response.completed', 'response': {'status': 'completed', 'output': [self._message(text='Hello')]}})
        self.assertEqual([event['type'] for event in events], [
            'response.output_item.added', 'response.content_part.added', 'response.output_text.done',
            'response.content_part.done', 'response.output_item.done', 'response.completed'])
        self.assertEqual(events[-1]['response']['output'][0]['phase'], 'final_answer')
        self.assertEqual(events[-1]['response']['output'][0]['content'][0]['text'], 'Hello')

    def test_failed_or_incomplete_response_is_not_finalized(self):
        for event_type, status in [('response.failed', 'failed'), ('response.incomplete', 'incomplete'),
                                   ('response.completed', 'incomplete')]:
            with self.subTest(event_type=event_type, status=status):
                normalizer = ResponsesStreamNormalizer(ToolContext())
                self._stream_message(normalizer, 0, 'Partial')
                payload = {'type': event_type, 'response': {'status': status, 'output': [], 'error': {'message': 'failure'}}}
                events = normalizer.normalize(payload)
                self.assertEqual(len(events), 1)
                self.assertEqual({k: v for k, v in events[0].items() if k != 'sequence_number'}, payload)
                self.assertFalse(normalizer.completed_items)

    def test_message_phases_and_streamed_text_survive_completion(self):
        normalizer = ResponsesStreamNormalizer(ToolContext())
        for index, phase in enumerate(('commentary', 'final_answer')):
            item = {'id': f'msg_{index}', 'type': 'message', 'role': 'assistant',
                    'phase': phase, 'content': []}
            added = normalizer.normalize({'type': 'response.output_item.added', 'output_index': index, 'item': item})
            self.assertEqual(added[0]['item']['phase'], phase)
            events = normalizer.normalize({'type': 'response.output_text.delta', 'output_index': index,
                                           'item_id': item['id'], 'delta': phase})
            done = normalizer.normalize({'type': 'response.output_item.done', 'output_index': index, 'item': item})
            self.assertEqual(done[-1]['item']['phase'], phase)
            self.assertEqual(done[-1]['item']['content'][0]['text'], phase)
            self.assertEqual([event['delta'] for event in events + done
                              if event['type'] == 'response.output_text.delta'], [phase])

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

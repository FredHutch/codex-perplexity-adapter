"""FastAPI application exposing an OpenAI-compatible Responses endpoint."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .transform import ToolContext, _unwrap_custom_arguments, transform_request, transform_response


PERPLEXITY_RESPONSES_URL = "https://api.perplexity.ai/v1/responses"
RATE_LIMIT_RETRIES = 2
MAX_RETRY_DELAY_SECONDS = 15.0


def _retry_delay(retry_after: str | None, attempt: int) -> float | None:
    """Return a bounded delay, or None when the server asks us to wait longer."""
    delay = min(2.0 ** attempt, MAX_RETRY_DELAY_SECONDS)
    if retry_after:
        try:
            delay = float(retry_after)
        except ValueError:
            try:
                date = parsedate_to_datetime(retry_after)
                delay = (date - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                pass
    if delay > MAX_RETRY_DELAY_SECONDS:
        return None
    return max(0.0, delay)


@dataclass(frozen=True)
class ModelRoute:
    alias: str
    upstream: str
    compatibility: str = "transparent"


BUILTIN_MODELS = (
    ModelRoute("gpt-6-astra", "openai/gpt-6-astra", "astra"),
    ModelRoute("gpt-6-sol", "openai/gpt-6-sol"),
    ModelRoute("gpt-6-luna", "openai/gpt-6-luna"),
    ModelRoute("gpt-5.6-sol", "openai/gpt-5.6-sol"),
    ModelRoute("gpt-5.6-terra", "openai/gpt-5.6-terra"),
    ModelRoute("gpt-5.6-luna", "openai/gpt-5.6-luna"),
    ModelRoute("gpt-5.6", "openai/gpt-5.6-sol"),
)
PASSTHROUGH_NAME = re.compile(r"gpt-[a-z0-9]+(?:[.-][a-z0-9]+)*\Z")
ROUTE_ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
ROUTE_UPSTREAM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
RESTRICTED_MODEL = "gpt-5.6-cyber"
LEGACY_MODEL_ALIAS = "gpt-5.6-sol"
LEGACY_UPSTREAM_MODEL = "openai/gpt-5.6-sol"


@dataclass(frozen=True)
class Settings:
    api_key: str
    model_alias: str | None = None
    upstream_model: str | None = None
    model_routes: tuple[str, ...] = ()
    allow_model_passthrough: bool = False
    local_token: str = "local-adapter-token"
    timeout_seconds: float = 300.0


def _parse_route(value: str) -> ModelRoute:
    alias, separator, upstream = value.partition("=")
    if not separator or not ROUTE_ALIAS.fullmatch(alias) or not ROUTE_UPSTREAM.fullmatch(upstream):
        raise ValueError(f"Invalid model route {value!r}; expected ALIAS=UPSTREAM")
    if alias == RESTRICTED_MODEL or upstream == f"openai/{RESTRICTED_MODEL}":
        raise ValueError(f"Restricted model {RESTRICTED_MODEL} cannot be routed")
    return ModelRoute(alias, upstream, "astra" if upstream == "openai/gpt-6-astra" else "transparent")


def _model_routes(settings: Settings) -> dict[str, ModelRoute]:
    """Return the configured public-to-upstream model mapping."""
    if settings.model_routes and (settings.model_alias is not None or settings.upstream_model is not None):
        raise ValueError("--model-route cannot be combined with --model-alias or --upstream-model")
    if settings.model_alias is None and settings.upstream_model is None:
        routes = {route.alias: route for route in BUILTIN_MODELS}
        for value in settings.model_routes:
            route = _parse_route(value)
            routes[route.alias] = route
        return routes
    route = _parse_route(f"{settings.model_alias or LEGACY_MODEL_ALIAS}={settings.upstream_model or LEGACY_UPSTREAM_MODEL}")
    return {route.alias: route}


def _normalize_model_request(payload: dict[str, Any], route: ModelRoute) -> dict[str, Any]:
    """Apply model-specific compatibility rules before upstream translation."""
    normalized = dict(payload)
    if route.compatibility != "astra":
        return normalized

    reasoning = normalized.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort") in {"none", "minimal"}:
        normalized["reasoning"] = {**reasoning, "effort": "low"}
    normalized.pop("temperature", None)
    normalized.pop("top_p", None)
    return normalized


def _error(status_code: int, message: str, error_type: str = "adapter_error") -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": error_type, "code": status_code}},
    )


def _authorized(authorization: str | None, local_token: str) -> bool:
    if not local_token:
        return True
    return authorization == f"Bearer {local_token}"


class ResponsesStreamNormalizer:
    """Fill protocol events that OpenAI-compatible UIs expect for live text."""

    def __init__(self, tool_context: ToolContext) -> None:
        self.tool_context = tool_context
        self.sequence_number = 0
        self.item_ids: dict[int, str] = {}
        self.content_started: set[tuple[int, int]] = set()
        self.content_done: set[tuple[int, int]] = set()
        self.text: dict[tuple[int, int], str] = {}
        self.message_items_started: set[int] = set()
        self.custom_item_ids: set[str] = set()
        self.custom_call_ids: set[str] = set()
        self.custom_call_items: dict[str, tuple[str, int]] = {}
        self.custom_item_indexes: dict[str, int] = {}
        self.custom_argument_buffers: dict[str, str] = {}
        self.custom_inputs: dict[str, str] = {}
        self.custom_input_done: set[str] = set()

    def _number(self, event: dict[str, Any]) -> dict[str, Any]:
        numbered = dict(event)
        numbered["sequence_number"] = self.sequence_number
        self.sequence_number += 1
        return numbered

    def _coordinates(self, event: dict[str, Any]) -> tuple[int, int, str]:
        output_index = event.get("output_index")
        if not isinstance(output_index, int):
            output_index = 0
        content_index = event.get("content_index")
        if not isinstance(content_index, int):
            content_index = 0
        item_id = event.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            item_id = self.item_ids.get(output_index, f"msg_{output_index}")
        return output_index, content_index, item_id

    def _canonical_message_item(
        self,
        item: dict[str, Any],
        output_index: int,
        *,
        completed: bool,
    ) -> dict[str, Any]:
        """Return the minimal message shape accepted by Codex's strict parser."""
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            item_id = self.item_ids.get(output_index, f"msg_{output_index}")
        self.item_ids[output_index] = item_id

        content = item.get("content")
        if not completed or not isinstance(content, list):
            content = []
        if completed and not content:
            full_text = "".join(
                text
                for (item_output_index, _), text in sorted(self.text.items())
                if item_output_index == output_index
            )
            content = [{"type": "output_text", "text": full_text, "annotations": []}]

        canonical: dict[str, Any] = {
            "id": item_id,
            "type": "message",
            "role": "assistant",
            "content": content,
        }
        phase = item.get("phase")
        if phase in {"commentary", "final_answer"}:
            canonical["phase"] = phase
        return canonical

    def _message_item_added(self, output_index: int, item_id: str) -> dict[str, Any]:
        self.item_ids[output_index] = item_id
        self.message_items_started.add(output_index)
        return {
            "type": "response.output_item.added",
            "output_index": output_index,
            "item": {
                "id": item_id,
                "type": "message",
                "role": "assistant",
                "content": [],
            },
        }

    def _custom_input_events(self, item_id: str, output_index: int, custom_input: str) -> list[dict[str, Any]]:
        if item_id in self.custom_input_done:
            return []
        self.custom_input_done.add(item_id)
        events: list[dict[str, Any]] = []
        if custom_input:
            events.append(self._number({
                "type": "response.custom_tool_call_input.delta",
                "item_id": item_id,
                "output_index": output_index,
                "delta": custom_input,
            }))
        events.append(self._number({
            "type": "response.custom_tool_call_input.done",
            "item_id": item_id,
            "output_index": output_index,
            "input": custom_input,
        }))
        return events

    def normalize(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        event = transform_response(payload, self.tool_context)
        event_type = event.get("type")
        emitted: list[dict[str, Any]] = []

        if event_type in {
            "response.function_call_arguments.delta",
            "response.function_call_arguments.done",
        }:
            item_id = event.get("item_id")
            call_id = event.get("call_id")
            if (isinstance(item_id, str) and item_id in self.custom_item_ids) or (
                isinstance(call_id, str) and call_id in self.custom_call_ids
            ):
                buffer_id = item_id if isinstance(item_id, str) else call_id
                output_index = event.get("output_index")
                if not isinstance(output_index, int):
                    if isinstance(item_id, str):
                        output_index = self.custom_item_indexes.get(item_id, 0)
                    elif isinstance(call_id, str):
                        output_index = self.custom_call_items.get(call_id, ("", 0))[1]
                    else:
                        output_index = 0
                if event_type == "response.function_call_arguments.delta":
                    delta = event.get("delta")
                    if isinstance(delta, str):
                        self.custom_argument_buffers[buffer_id] = (
                            self.custom_argument_buffers.get(buffer_id, "") + delta
                        )
                    return []

                arguments = event.get("arguments")
                if not isinstance(arguments, str):
                    arguments = self.custom_argument_buffers.get(buffer_id, "")
                custom_input = _unwrap_custom_arguments(arguments)
                self.custom_argument_buffers.pop(buffer_id, None)
                resolved_item_id = item_id if isinstance(item_id, str) else self.custom_call_items.get(call_id, ("", 0))[0]
                emitted.extend(self._custom_input_events(resolved_item_id, output_index, custom_input))
                return emitted

        if event_type == "response.output_item.added":
            output_index, _, _ = self._coordinates(event)
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "message":
                canonical = self._canonical_message_item(item, output_index, completed=False)
                event = {**event, "output_index": output_index, "item": canonical}
                self.message_items_started.add(output_index)
            elif isinstance(item, dict) and isinstance(item.get("id"), str):
                self.item_ids[output_index] = item["id"]
                if item.get("type") == "custom_tool_call":
                    self.custom_item_ids.add(item["id"])
                    self.custom_item_indexes[item["id"]] = output_index
                    if isinstance(item.get("call_id"), str):
                        self.custom_call_ids.add(item["call_id"])
                        self.custom_call_items[item["call_id"]] = (item["id"], output_index)
                    if isinstance(item.get("input"), str):
                        self.custom_inputs[item["id"]] = item["input"]

        if event_type == "response.output_item.done":
            output_index, _, _ = self._coordinates(event)
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "message":
                canonical = self._canonical_message_item(item, output_index, completed=True)
                event = {**event, "output_index": output_index, "item": canonical}
            elif isinstance(item, dict) and item.get("type") == "custom_tool_call":
                item_id = item.get("id")
                if isinstance(item_id, str):
                    custom_input = item.get("input")
                    if not isinstance(custom_input, str) or not custom_input:
                        custom_input = self.custom_inputs.get(item_id, "")
                    if not custom_input and item_id in self.custom_argument_buffers:
                        custom_input = _unwrap_custom_arguments(self.custom_argument_buffers[item_id])
                    emitted.extend(self._custom_input_events(item_id, output_index, custom_input))
                    self.custom_argument_buffers.pop(item_id, None)
                    self.custom_inputs.pop(item_id, None)

        if event_type in {"response.output_text.delta", "response.output_text.done"}:
            output_index, content_index, item_id = self._coordinates(event)
            key = (output_index, content_index)
            if output_index not in self.message_items_started:
                emitted.append(self._number(self._message_item_added(output_index, item_id)))
            event = {
                **event,
                "output_index": output_index,
                "content_index": content_index,
                "item_id": item_id,
            }
            if key not in self.content_started:
                emitted.append(
                    self._number(
                        {
                            "type": "response.content_part.added",
                            "item_id": item_id,
                            "output_index": output_index,
                            "content_index": content_index,
                            "part": {"type": "output_text", "text": "", "annotations": []},
                        }
                    )
                )
                self.content_started.add(key)
            if event_type == "response.output_text.delta" and isinstance(event.get("delta"), str):
                self.text[key] = self.text.get(key, "") + event["delta"]

        if event_type == "response.content_part.added":
            output_index, content_index, _ = self._coordinates(event)
            key = (output_index, content_index)
            if key in self.content_started:
                return emitted
            self.content_started.add(key)

        if event_type == "response.content_part.done":
            output_index, content_index, _ = self._coordinates(event)
            key = (output_index, content_index)
            if key in self.content_done:
                return emitted
            self.content_done.add(key)

        emitted.append(self._number(event))

        if event_type == "response.output_text.done":
            output_index, content_index, item_id = self._coordinates(event)
            key = (output_index, content_index)
            if key not in self.content_done:
                text = event.get("text") if isinstance(event.get("text"), str) else self.text.get(key, "")
                emitted.append(
                    self._number(
                        {
                            "type": "response.content_part.done",
                            "item_id": item_id,
                            "output_index": output_index,
                            "content_index": content_index,
                            "part": {"type": "output_text", "text": text, "annotations": []},
                        }
                    )
                )
                self.content_done.add(key)
        return emitted


async def _translate_sse(
    upstream: httpx.Response,
    tool_context: ToolContext,
    client: httpx.AsyncClient,
) -> AsyncIterator[bytes]:
    data_lines: list[str] = []
    raw_lines: list[str] = []
    normalizer = ResponsesStreamNormalizer(tool_context)
    normalized_types = {
        "response.output_item.added", "response.output_item.done",
        "response.output_text.delta", "response.output_text.done",
        "response.content_part.added", "response.content_part.done",
        "response.function_call_arguments.delta", "response.function_call_arguments.done",
        "response.created", "response.in_progress", "response.completed", "response.failed",
    }
    try:
        async for line in upstream.aiter_lines():
            if line == "":
                if data_lines:
                    data = "\n".join(data_lines)
                    if data != "[DONE]":
                        try:
                            parsed = json.loads(data)
                            ordinary_function_event = False
                            if isinstance(parsed, dict):
                                event_type = parsed.get("type")
                                if event_type in {"response.output_item.added", "response.output_item.done"}:
                                    item = parsed.get("item")
                                    ordinary_function_event = (
                                        isinstance(item, dict)
                                        and item.get("type") == "function_call"
                                        and item.get("name") not in tool_context.custom_names
                                        and item.get("name") not in tool_context.namespace_tools
                                    )
                                elif event_type in {
                                    "response.function_call_arguments.delta",
                                    "response.function_call_arguments.done",
                                }:
                                    ordinary_function_event = (
                                        parsed.get("item_id") not in normalizer.custom_item_ids
                                        and parsed.get("call_id") not in normalizer.custom_call_ids
                                    )
                            if isinstance(parsed, dict) and parsed.get("type") in normalized_types and not ordinary_function_event:
                                for event in normalizer.normalize(parsed):
                                    event_type = event.get("type", "message")
                                    encoded = json.dumps(event, separators=(",", ":"))
                                    yield f"event: {event_type}\ndata: {encoded}\n\n".encode()
                                raw_lines.clear()
                        except json.JSONDecodeError:
                            pass
                if raw_lines:
                    yield ("\n".join(raw_lines) + "\n\n").encode()
                data_lines.clear()
                raw_lines.clear()
            else:
                raw_lines.append(line)
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
        if raw_lines:
            yield ("\n".join(raw_lines) + "\n\n").encode()
    finally:
        await upstream.aclose()
        await client.aclose()


def create_app(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    model_routes = _model_routes(settings)
    app = FastAPI(
        title="Codex–Perplexity Adapter",
        version="0.1.5",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/")
    async def root() -> dict[str, Any]:
        return {
            "name": "Codex–Perplexity Adapter",
            "status": "ok",
            "responses_endpoint": "/v1/responses",
            "model": next(iter(model_routes)),
            "models": list(model_routes),
        }

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models(authorization: str | None = Header(default=None)) -> Response:
        if not _authorized(authorization, settings.local_token):
            return _error(401, "Invalid local adapter token", "authentication_error")
        return JSONResponse(
            content={
                "object": "list",
                "data": [
                    {"id": model, "object": "model", "owned_by": "perplexity"}
                    for model in model_routes
                ],
            }
        )

    @app.post("/v1/responses")
    async def responses(request: Request, authorization: str | None = Header(default=None)) -> Response:
        if not _authorized(authorization, settings.local_token):
            return _error(401, "Invalid local adapter token", "authentication_error")
        try:
            payload = await request.json()
        except Exception:
            return _error(400, "Request body must be valid JSON", "invalid_request_error")
        if not isinstance(payload, dict):
            return _error(400, "Request body must be a JSON object", "invalid_request_error")

        requested_model = payload.get("model")
        route = model_routes.get(requested_model) if isinstance(requested_model, str) else None
        if requested_model == RESTRICTED_MODEL:
            route = None
        if route is None and settings.allow_model_passthrough and isinstance(requested_model, str):
            if PASSTHROUGH_NAME.fullmatch(requested_model) and requested_model != RESTRICTED_MODEL:
                route = ModelRoute(requested_model, f"openai/{requested_model}")
        if route is None:
            supported = ", ".join(model_routes)
            return _error(
                400,
                f"Unsupported model {requested_model!r}. Supported models: {supported}",
                "invalid_request_error",
            )

        payload = _normalize_model_request(payload, route)
        upstream_payload, tool_context = transform_request(payload, route.upstream)
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout_seconds),
            transport=transport,
        )
        upstream_request = client.build_request(
            "POST",
            PERPLEXITY_RESPONSES_URL,
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if upstream_payload.get("stream") else "application/json",
            },
            json=upstream_payload,
        )
        try:
            for attempt in range(RATE_LIMIT_RETRIES + 1):
                upstream = await client.send(upstream_request, stream=bool(upstream_payload.get("stream")))
                if upstream.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                    break
                delay = _retry_delay(upstream.headers.get("retry-after"), attempt)
                if delay is None:
                    break
                await upstream.aclose()
                await asyncio.sleep(delay)
        except httpx.HTTPError as exc:
            await client.aclose()
            return _error(502, f"Could not reach Perplexity: {exc}", "upstream_error")

        if upstream.status_code >= 400:
            body = await upstream.aread()
            content_type = upstream.headers.get("content-type", "application/json")
            retry_after = upstream.headers.get("retry-after")
            await upstream.aclose()
            await client.aclose()
            headers = {"content-type": content_type}
            if retry_after:
                headers["retry-after"] = retry_after
            return Response(content=body, status_code=upstream.status_code, headers=headers)

        if upstream_payload.get("stream"):
            return StreamingResponse(
                _translate_sse(upstream, tool_context, client),
                status_code=upstream.status_code,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                    "X-Content-Type-Options": "nosniff",
                },
            )

        body = await upstream.aread()
        content_type = upstream.headers.get("content-type", "application/json")
        await upstream.aclose()
        await client.aclose()
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return Response(content=body, status_code=upstream.status_code,
                            headers={"content-type": content_type})
        if isinstance(parsed, dict):
            transformed = transform_response(parsed, tool_context)
            if transformed == parsed:
                return Response(content=body, status_code=upstream.status_code,
                                headers={"content-type": content_type})
            parsed = transformed
        return JSONResponse(content=parsed, status_code=upstream.status_code)

    return app

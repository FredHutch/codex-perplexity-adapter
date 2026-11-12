"""FastAPI application exposing an OpenAI-compatible Responses endpoint."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import __version__
from .continuation import ContinuationManager, has_local_tools, outstanding_calls, prior_response_error
from .image_resize import ImageResizer, ResizeWorkBudget, RESIZE_POLICY
from .request_size import RequestTooLarge, encode_request, normalize_transport_error, warning_level
from .status import BudgetStatus, StatusMiddleware
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


def _route(alias: str, upstream: str) -> ModelRoute:
    compatibility = {
        "openai/gpt-6-astra": "astra",
        "openai/gpt-6.1-sol": "gpt-6.1",
    }.get(upstream, "transparent")
    return ModelRoute(alias, upstream, compatibility)


BUILTIN_MODELS = (
    ModelRoute("gpt-6-astra", "openai/gpt-6-astra", "astra"),
    _route("gpt-6.1-sol", "openai/gpt-6.1-sol"),
    _route("gpt-6.1", "openai/gpt-6.1-sol"),
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
    continuation: str = "auto"
    resize_images_on_limit: bool = True


class ContinuationCapture:
    """Request-local output capture; the manager retains only its fingerprints."""
    def __init__(self, manager, key, fingerprints, tools, model, budget_status=None):
        self.manager, self.key, self.fingerprints = manager, key, fingerprints
        self.tools, self.model = tools, model
        self.response = None
        self.invalid = False
        self.started_items = set()
        self.pending = None
        self.saved_bytes = 0
        self.input_items = []
        self.budget_status = budget_status
        self.image_prepared = None

    def observe(self, event):
        if event.get("type") == "response.output_item.added":
            item = event.get("item")
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                self.started_items.add(item["id"])
        if event.get("type") in {"response.failed", "response.incomplete", "error"}:
            self.invalid = True
        if event.get("type") == "response.completed":
            if self.response is not None:
                self.invalid = True
            self.response = event.get("response")
            if isinstance(self.response, dict) and isinstance(self.response.get("output"), list):
                terminal_ids = {item.get("id") for item in self.response["output"] if isinstance(item, dict)}
                if not self.started_items <= terminal_ids:
                    self.invalid = True

    def commit(self, response=None):
        response = self.response if response is None else response
        if self.invalid or not isinstance(response, dict) or not isinstance(response.get("output"), list):
            return
        output, _ = transform_request({"input": response["output"], "tools": self.tools}, self.model)
        response_id = response.get("id")
        if (response.get("status") == "completed" and isinstance(response_id, str)
                and 0 < len(response_id) <= 256):
            self.pending = (response, output["input"])

    def finalize(self, delivered):
        # Called only after ASGI has delivered the whole response. A cancelled
        # final send must neither establish a checkpoint nor earn savings.
        if not delivered or self.invalid or self.pending is None:
            if self.key is not None:
                self.manager.invalidate(self.key)
            return
        if delivered and not self.invalid and self.pending is not None:
            response, output = self.pending
            if self.budget_status is not None and self.image_prepared is not None:
                prepared = self.image_prepared
                if prepared.resized_images:
                    self.budget_status.image_requests += 1
                    self.budget_status.resized_images += prepared.resized_images
                    self.budget_status.image_saved_bytes += prepared.saved_bytes
            if self.key is not None:
                self.manager.saved_bytes += self.saved_bytes
            if self.key is not None:
                self.manager.accept(self.key, response, self.fingerprints, output, self.input_items)


def _parse_route(value: str) -> ModelRoute:
    alias, separator, upstream = value.partition("=")
    if not separator or not ROUTE_ALIAS.fullmatch(alias) or not ROUTE_UPSTREAM.fullmatch(upstream):
        raise ValueError(f"Invalid model route {value!r}; expected ALIAS=UPSTREAM")
    if alias == RESTRICTED_MODEL or upstream == f"openai/{RESTRICTED_MODEL}":
        raise ValueError(f"Restricted model {RESTRICTED_MODEL} cannot be routed")
    return _route(alias, upstream)


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
    if route.compatibility not in {"astra", "gpt-6.1"}:
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


def _context_limit_response(error: dict[str, Any], *, stream: bool) -> Response:
    # Older Codex clients recognize this code in failed SSE events but treat
    # HTTP 400 bodies as generic errors, preventing compaction's trim/retry loop.
    if not stream:
        return JSONResponse(status_code=400, content={"error": error})
    event = {
        "type": "response.failed", "sequence_number": 0,
        "response": {"id": "resp_" + uuid.uuid4().hex, "object": "response",
                     "status": "failed", "output": [], "error": error},
    }
    async def failed_stream():
        yield ("event: response.failed\ndata: " + json.dumps(event, separators=(",", ":")) + "\n\n").encode()
    return StreamingResponse(failed_stream(), media_type="text/event-stream")


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
        self.messages: dict[int, dict[str, Any]] = {}
        self.completed_items: dict[int, dict[str, Any]] = {}
        self.text_done: set[tuple[int, int]] = set()
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
            content = self.messages.get(output_index, {}).get("content") or [
                {"type": "output_text", "text": text, "annotations": []}
                for (item_output_index, _), text in sorted(self.text.items())
                if item_output_index == output_index
            ] or [{"type": "output_text", "text": "", "annotations": []}]

        canonical: dict[str, Any] = {
            "id": item_id,
            "type": "message",
            "role": "assistant",
            "content": content,
        }
        phase = item.get("phase") or self.messages.get(output_index, {}).get("phase")
        if phase in {"commentary", "final_answer"}:
            canonical["phase"] = phase
        self.messages[output_index] = canonical
        return canonical

    def _message_item_added(self, output_index: int, item_id: str) -> dict[str, Any]:
        self.item_ids[output_index] = item_id
        self.message_items_started.add(output_index)
        self.messages[output_index] = {
            "id": item_id, "type": "message", "role": "assistant", "content": [],
        }
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

    def _finish_message(self, output_index: int, phase: str) -> list[dict[str, Any]]:
        """Close a streamed message once its commentary/answer boundary is known."""
        if output_index in self.completed_items:
            return []
        item = self._canonical_message_item(self.messages[output_index], output_index, completed=True)
        item.setdefault("phase", phase)
        self.messages[output_index] = item
        emitted: list[dict[str, Any]] = []
        for content_index, part in enumerate(item["content"]):
            if not isinstance(part, dict) or part.get("type") != "output_text":
                continue
            key = (output_index, content_index)
            coordinates = {"output_index": output_index, "content_index": content_index, "item_id": item["id"]}
            if key not in self.content_started:
                emitted.append(self._number({"type": "response.content_part.added", **coordinates,
                                             "part": {"type": "output_text", "text": "", "annotations": []}}))
                self.content_started.add(key)
            if key not in self.text_done:
                emitted.append(self._number({"type": "response.output_text.done", **coordinates,
                                             "text": part.get("text", "")}))
                self.text_done.add(key)
            if key not in self.content_done:
                emitted.append(self._number({"type": "response.content_part.done", **coordinates, "part": part}))
                self.content_done.add(key)
        emitted.append(self._number({"type": "response.output_item.done", "output_index": output_index, "item": item}))
        self.completed_items[output_index] = item
        return emitted

    def _finish_before_tool(self, output_index: int) -> list[dict[str, Any]]:
        emitted: list[dict[str, Any]] = []
        for index in sorted(self.messages):
            if index < output_index:
                emitted.extend(self._finish_message(index, "commentary"))
        return emitted

    def _complete_response(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        response = event.get("response")
        if not isinstance(response, dict) or response.get("status") not in {None, "completed"}:
            return [self._number(event)]
        emitted: list[dict[str, Any]] = []
        output = response.get("output")
        output = output if isinstance(output, list) else []
        # Perplexity may omit streamed commentary from output, compressing its
        # indexes. Match existing items by ID rather than the terminal position.
        indexes = {item_id: index for index, item_id in self.item_ids.items()}
        terminal_items = dict(self.completed_items)
        for position, item in enumerate(output):
            if not isinstance(item, dict):
                continue
            index = indexes.get(item.get("id"))
            if index is None:
                index = position
                while index in self.item_ids or index in terminal_items:
                    index += 1
            if item.get("type") == "message":
                if index not in self.messages:
                    added = self._canonical_message_item(item, index, completed=False)
                    emitted.append(self._number({"type": "response.output_item.added", "output_index": index, "item": added}))
                    self.message_items_started.add(index)
                if index not in self.completed_items:
                    self._canonical_message_item(item, index, completed=True)
            else:
                terminal_items[index] = item
        tool_indexes = [index for index, item in terminal_items.items()
                        if item.get("type") in {"function_call", "custom_tool_call"}]
        for index in sorted(self.messages):
            phase = "commentary" if any(tool_index > index for tool_index in tool_indexes) else "final_answer"
            emitted.extend(self._finish_message(index, phase))
        terminal_items.update(self.completed_items)
        # Preserve non-object extension output entries as well.
        merged = [terminal_items[index] for index in sorted(terminal_items)]
        merged.extend(item for item in output if not isinstance(item, dict))
        emitted.append(self._number({**event, "response": {**response, "output": merged}}))
        return emitted

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
        event = normalize_transport_error(transform_response(payload, self.tool_context))
        # Perplexity can send a failed terminal event with only a top-level
        # error. Codex reads response.error; without that envelope it loses
        # the actual cause and reports a generic disconnected stream.
        if (event.get("type") == "response.failed"
                and not isinstance(event.get("response"), dict)
                and isinstance(event.get("error"), dict)):
            event = {**event, "response": {"id": "resp_" + uuid.uuid4().hex,
                     "object": "response", "status": "failed", "output": [],
                     "error": event["error"]}}
        event_type = event.get("type")
        emitted: list[dict[str, Any]] = []

        if event_type == "response.completed":
            return self._complete_response(event)

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
                if item.get("type") in {"function_call", "custom_tool_call"}:
                    emitted.extend(self._finish_before_tool(output_index))
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
                if output_index in self.completed_items:
                    return emitted
                if output_index not in self.message_items_started:
                    canonical = self._canonical_message_item(item, output_index, completed=False)
                    emitted.append(self._number({"type": "response.output_item.added", "output_index": output_index, "item": canonical}))
                    self.message_items_started.add(output_index)
                canonical = self._canonical_message_item(item, output_index, completed=True)
                # An unlabelled done event alone cannot tell us whether a tool
                # follows. Resolve its phase at the next tool or terminal event.
                if canonical.get("phase"):
                    emitted.extend(self._finish_message(output_index, canonical["phase"]))
                return emitted
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
            if isinstance(item, dict):
                if isinstance(item.get("id"), str):
                    self.item_ids[output_index] = item["id"]
                if item.get("type") in {"function_call", "custom_tool_call"}:
                    emitted.extend(self._finish_before_tool(output_index))
                self.completed_items[output_index] = item

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
            elif event_type == "response.output_text.done":
                if key in self.text_done:
                    return emitted
                self.text_done.add(key)
                if isinstance(event.get("text"), str):
                    self.text[key] = event["text"]

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
    budget_status: BudgetStatus | None = None,
    capture: ContinuationCapture | None = None,
    lines: AsyncIterator[str] | None = None,
) -> AsyncIterator[bytes]:
    data_lines: list[str] = []
    raw_lines: list[str] = []
    normalizer = ResponsesStreamNormalizer(tool_context)
    size_failure_counted = False
    normalized_types = {
        "response.output_item.added", "response.output_item.done",
        "response.output_text.delta", "response.output_text.done",
        "response.content_part.added", "response.content_part.done",
        "response.function_call_arguments.delta", "response.function_call_arguments.done",
        "response.created", "response.in_progress", "response.completed", "response.failed", "response.incomplete",
    }
    try:
        async for line in lines if lines is not None else upstream.aiter_lines():
            if line == "":
                if data_lines:
                    data = "\n".join(data_lines)
                    if data != "[DONE]":
                        parsed = None
                        try:
                            parsed = json.loads(data)
                            if budget_status is not None and not size_failure_counted and isinstance(parsed, dict):
                                if normalize_transport_error(parsed) is not parsed:
                                    budget_status.failures += 1
                                    size_failure_counted = True
                            if capture is not None:
                                if not isinstance(parsed, dict) or not isinstance(parsed.get("type"), str):
                                    capture.invalid = True
                                elif parsed.get("type") == "response.completed":
                                    terminal = parsed.get("response")
                                    if (not isinstance(terminal, dict) or terminal.get("status") != "completed"
                                            or not isinstance(terminal.get("output"), list)):
                                        capture.invalid = True
                            if isinstance(parsed, dict) and parsed.get("type") in normalized_types:
                                for event in normalizer.normalize(parsed):
                                    if capture is not None:
                                        capture.observe(event)
                                    event_type = event.get("type", "message")
                                    encoded = json.dumps(event, separators=(",", ":"))
                                    yield f"event: {event_type}\ndata: {encoded}\n\n".encode()
                                raw_lines.clear()
                        except json.JSONDecodeError:
                            if capture is not None:
                                capture.invalid = True
                            pass
                        if capture is not None and isinstance(parsed, dict) and parsed.get("type") == "error":
                            capture.invalid = True
                if raw_lines:
                    yield ("\n".join(raw_lines) + "\n\n").encode()
                data_lines.clear()
                raw_lines.clear()
            else:
                raw_lines.append(line)
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
        if raw_lines:
            if capture is not None:
                capture.invalid = True
            yield ("\n".join(raw_lines) + "\n\n").encode()
        if capture is not None:
            capture.commit()
    finally:
        await upstream.aclose()
        await client.aclose()


async def _continuation_stream_start(upstream: httpx.Response) -> tuple[AsyncIterator[str], bool]:
    """Inspect only the first SSE block before any downstream bytes are sent.

    A successful/ambiguous first block ends recovery eligibility. Bound buffering
    so this does not turn the incremental transport into a buffered response.
    """
    iterator = upstream.aiter_lines()
    prefix: list[str] = []
    size = 0
    async for line in iterator:
        prefix.append(line)
        size += len(line.encode("utf-8")) + 1
        if not line or size >= 65536:
            break
    rejected = False
    data = "\n".join(line[5:].lstrip() for line in prefix if line.startswith("data:"))
    if prefix and prefix[-1] == "" and data:
        try:
            parsed = json.loads(data)
            if isinstance(parsed, dict) and parsed.get("type") in {"error", "response.failed", "response.incomplete"}:
                rejected = prior_response_error(parsed) or prior_response_error(parsed.get("response"))
        except (ValueError, UnicodeDecodeError):
            pass
    async def combined():
        for line in prefix:
            yield line
        async for line in iterator:
            yield line
    return combined(), rejected


def create_app(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    if settings.continuation not in {"auto", "disabled"}:
        raise ValueError("continuation must be auto or disabled")
    model_routes = _model_routes(settings)
    app = FastAPI(
        title="Codex–Perplexity Adapter",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    budget_status = BudgetStatus()
    continuation = ContinuationManager(enabled=settings.continuation == "auto",
        policy=RESIZE_POLICY if settings.resize_images_on_limit else "images-unchanged")
    image_resizer = ImageResizer(settings.resize_images_on_limit)
    app.state.continuation = continuation
    app.add_middleware(StatusMiddleware, status=budget_status)

    @app.get("/v1/adapter/status")
    async def adapter_status(authorization: str | None = Header(default=None)) -> Response:
        if not settings.local_token or not _authorized(authorization, settings.local_token):
            return _error(401, "Invalid local adapter token", "authentication_error")
        return JSONResponse({**budget_status.snapshot(), **continuation.snapshot()}, headers={"Cache-Control": "no-store"})

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
                route = _route(requested_model, f"openai/{requested_model}")
        if route is None:
            supported = ", ".join(model_routes)
            return _error(
                400,
                f"Unsupported model {requested_model!r}. Supported models: {supported}",
                "invalid_request_error",
            )

        payload = _normalize_model_request(payload, route)
        upstream_payload, tool_context = transform_request(payload, route.upstream)
        full_payload = upstream_payload
        capture = ContinuationCapture(continuation, None, None, full_payload.get("tools", []), route.upstream, budget_status)
        request.state.continuation_outcome = capture
        key = continuation.session_key(request.headers.get("session-id"), request.headers.get("thread-id"))
        key = await continuation.acquire(key)
        if key is not None:
            request.state.continuation_lease = (continuation, key)
            if "previous_response_id" not in full_payload and isinstance(full_payload.get("input"), list):
                fingerprints = await asyncio.to_thread(continuation.fingerprints, full_payload)
                upstream_payload = continuation.select(key, full_payload, fingerprints)
                capture.key, capture.fingerprints = key, fingerprints
                capture.input_items = full_payload["input"]
                request.state.continuation_outcome = capture
            else:
                continuation.invalidate(key)
        optimized = upstream_payload is not full_payload
        full_size = await asyncio.to_thread(lambda: len(encode_request(full_payload))) if optimized else 0
        def size_error(exc):
            budget_status.failures += 1
            budget_status.active[request.state.budget_request_id] = exc.size
            return _context_limit_response({"message": str(exc), "type": "invalid_request_error",
                                            "code": "context_length_exceeded"}, stream=bool(upstream_payload.get("stream")))
        def account(body):
            budget_status.active[request.state.budget_request_id] = len(body)
            level = warning_level(len(body))
            if level:
                budget_status.warnings[level - 1] += 1
        resize_work = ResizeWorkBudget()
        async def prepare_images(body):
            # The request body has been consumed. Watch disconnects while a
            # worker is queued or in native code, before opening upstream.
            async def disconnected():
                while True:
                    if (await request.receive())["type"] == "http.disconnect":
                        return
            preparation = asyncio.create_task(image_resizer.prepare(body, resize_work))
            watcher = asyncio.create_task(disconnected())
            try:
                done, _ = await asyncio.wait((preparation, watcher), return_when=asyncio.FIRST_COMPLETED)
                if watcher in done:
                    raise asyncio.CancelledError()
                return await preparation
            finally:
                watcher.cancel()
                if not preparation.done():
                    preparation.cancel()
                await asyncio.gather(watcher, preparation, return_exceptions=True)
        try:
            prepared = await prepare_images(upstream_payload)
            upstream_body = prepared.body
        except RequestTooLarge as exc:
            return size_error(exc)
        account(upstream_body)
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout_seconds),
            transport=transport,
        )
        tool_replay_counted = False
        async def send(body):
            nonlocal tool_replay_counted
            upstream_request = client.build_request("POST", PERPLEXITY_RESPONSES_URL,
                headers={"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json",
                         "Accept": "text/event-stream" if upstream_payload.get("stream") else "application/json"}, content=body)
            for attempt in range(RATE_LIMIT_RETRIES + 1):
                if (not tool_replay_counted and upstream_payload is full_payload
                        and "previous_response_id" not in full_payload
                        and isinstance(full_payload.get("input"), list) and has_local_tools(full_payload["input"])):
                    continuation.tool_replays += 1
                    tool_replay_counted = True
                result = await client.send(upstream_request, stream=bool(upstream_payload.get("stream")))
                if result.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                    return result
                delay = _retry_delay(result.headers.get("retry-after"), attempt)
                if delay is None:
                    return result
                await result.aclose()
                await asyncio.sleep(delay)
            raise AssertionError("unreachable")
        try:
            if optimized:
                continuation.continuations += 1
            else:
                continuation.replays += 1
            upstream = await send(upstream_body)
            stream_lines = None
            rejected_prior = False
            if optimized and upstream.status_code in {400, 404, 422}:
                body = await upstream.aread()
                try:
                    rejected_prior = prior_response_error(json.loads(body))
                except (ValueError, UnicodeDecodeError):
                    rejected_prior = False
            elif optimized and upstream.status_code < 400 and upstream_payload.get("stream"):
                stream_lines, rejected_prior = await _continuation_stream_start(upstream)
            if rejected_prior:
                continuation.invalidate(key)
                continuation.fallbacks += 1
                upstream_payload = full_payload
                try:
                    prepared = await prepare_images(full_payload)
                    upstream_body = prepared.body
                except RequestTooLarge as exc:
                    await upstream.aclose()
                    await client.aclose()
                    return size_error(exc)
                account(upstream_body)
                continuation.replays += 1
                optimized = False
                await upstream.aclose()
                upstream = await send(upstream_body)
                stream_lines = None
            if optimized and upstream.status_code < 400 and capture is not None:
                capture.saved_bytes = max(0, full_size - prepared.original_size)
            capture.image_prepared = prepared
        except httpx.HTTPError:
            await client.aclose()
            return _error(502, "Could not reach Perplexity", "upstream_error")
        except BaseException:
            await client.aclose()
            raise

        if upstream.status_code >= 400:
            try:
                body = await upstream.aread()
            finally:
                await upstream.aclose()
                await client.aclose()
            content_type = upstream.headers.get("content-type", "application/json")
            retry_after = upstream.headers.get("retry-after")
            headers = {"content-type": content_type}
            if retry_after:
                headers["retry-after"] = retry_after
            if upstream.status_code in {400, 413}:
                try:
                    parsed_error = json.loads(body)
                except (ValueError, UnicodeDecodeError):
                    parsed_error = None
                if isinstance(parsed_error, dict):
                    normalized = normalize_transport_error(parsed_error)
                    if normalized is not parsed_error:
                        budget_status.failures += 1
                        return _context_limit_response(normalized["error"], stream=bool(upstream_payload.get("stream")))
            return Response(content=body, status_code=upstream.status_code, headers=headers)

        if upstream_payload.get("stream"):
            return StreamingResponse(
                _translate_sse(upstream, tool_context, client, budget_status, capture, stream_lines),
                status_code=upstream.status_code,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                    "X-Content-Type-Options": "nosniff",
                },
            )

        try:
            body = await upstream.aread()
        finally:
            await upstream.aclose()
            await client.aclose()
        content_type = upstream.headers.get("content-type", "application/json")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return Response(content=body, status_code=upstream.status_code,
                            headers={"content-type": content_type})
        if isinstance(parsed, dict):
            transformed = transform_response(parsed, tool_context)
            if capture is not None:
                capture.commit(transformed)
            if transformed == parsed:
                return Response(content=body, status_code=upstream.status_code,
                                headers={"content-type": content_type})
            parsed = transformed
        return JSONResponse(content=parsed, status_code=upstream.status_code)

    return app

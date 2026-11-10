"""Measure complete requests and expose transport limits as context errors."""

from __future__ import annotations

import json
import re
from typing import Any

# Leave headroom for the provider's downstream request conversion.
REQUEST_BUDGET_BYTES = 18_000_000
_TRANSPORT_LIMIT = re.compile(
    r"request size \d+ bytes exceeds the 20 MB downstream transport limit", re.IGNORECASE
)


class RequestTooLarge(ValueError):
    def __init__(self, message: str, size: int):
        super().__init__(message)
        self.size = size


def encode_request(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def warning_level(size: int) -> int:
    return sum(size >= threshold for threshold in (10_000_000, 12_000_000, 15_000_000))


def _content_bytes(payload: dict[str, Any]) -> tuple[int, int]:
    images = text = 0
    items = payload.get("input", [])
    if not isinstance(items, list):
        return images, text
    for item in items:
        if not isinstance(item, dict):
            continue
        tool = item.get("type") in {"function_call_output", "custom_tool_call_output"}
        parts = item.get("output" if tool else "content")
        if tool and isinstance(parts, str):
            text += len(encode_request(parts))
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            url = part.get("image_url")
            if part.get("type") == "input_image" and isinstance(url, str) and url.startswith("data:image/"):
                images += len(encode_request(url))
            elif tool and isinstance(part.get("text"), str):
                text += len(encode_request(part["text"]))
    return images, text


def with_budget_notice(payload: dict[str, Any], budget: int) -> dict[str, Any]:
    """Add request-local numeric guidance without touching client history."""
    items = payload.get("input", [])
    if not isinstance(items, (list, str)):
        return payload
    history = list(items) if isinstance(items, list) else [{"type": "message", "role": "user", "content": items}]
    images, text = _content_bytes(payload)
    advice = (
        "Budget snapshot only. Keep tool output bounded.",
        "Reduce image batches and tool-output sizes. Record findings in existing user-authorized project files.",
        "Finish the current review batch, save progress, and pause for compaction. If client compaction does not occur, ask the user to run /compact.",
        "Another large result may exceed the limit. Request no more than 1 MB (1000000 bytes) of new tool output before reassessing. Save progress and pause; if client compaction does not occur, ask the user to run /compact.",
    )
    def notice(size: int) -> str:
        value = (
            "Adapter request-byte budget (decimal MB; separate from model tokens). "
            f"current={size:012d}; remaining={max(0, budget-size):012d}; limit={budget:012d}; "
            f"inline_images={images:012d}; tool_text={text:012d}; other={max(0,size-images-text):012d}. "
            "Categories count serialized value bytes; other includes JSON overhead and this notice. "
            + advice[warning_level(size)]
            + " This is advisory; tools can return unexpectedly large results. No content has been stored or reduced."
        )
        assert len(value) <= 900
        return value.ljust(900)
    message = {"type": "message", "role": "developer", "content": notice(0)}
    # Append after client instructions; never reorder or remove client messages.
    history.append(message)
    result = {**payload, "input": history}
    size = len(encode_request(result))
    message["content"] = notice(size)
    assert len(encode_request(result)) == size
    return result


def prepare_request_body(payload: dict[str, Any], *, budget: int = REQUEST_BUDGET_BYTES, notices: bool = False) -> bytes:
    """Check the complete UTF-8 body without truncating or changing content."""
    body = encode_request(with_budget_notice(payload, budget) if notices else payload)
    if len(body) > budget:
        raise RequestTooLarge(
            f"Request size {len(body)} bytes exceeds the adapter's {budget}-byte "
            "budget for Perplexity's 20 MB downstream transport limit. "
            "Compaction (/compact) can shorten history; an individually oversized attachment, tool result, "
            "or tool definition must be reduced at its source. Retry after reducing the request. "
            "This is a serialized-byte limit, separate from model context tokens. "
            "No conversation content was discarded.", len(body)
        )
    return body


def normalize_transport_error(payload: dict[str, Any]) -> dict[str, Any]:
    """Make the known provider byte-limit failure recognizable to Codex."""
    result = payload
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        match = _TRANSPORT_LIMIT.search(error["message"])
        if match:
            result = {**payload, "error": {**error,
                "type": "invalid_request_error", "code": "context_length_exceeded",
                "message": match.group(0) + ". This is a serialized-byte limit, separate from model context tokens. "
                           "Compaction (/compact) can shorten history; an individually oversized attachment, tool result, "
                           "or tool definition must be reduced at its source. Retry after reducing the request.",
            }}
    response = result.get("response")
    if isinstance(response, dict):
        normalized = normalize_transport_error(response)
        if normalized is not response:
            result = {**result, "response": normalized}
    return result

"""Measure complete requests and expose transport limits as context errors."""

from __future__ import annotations

import json
import re
from typing import Any

# Leave headroom for the provider's downstream request conversion.
REQUEST_BUDGET_BYTES = 19_000_000
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


def prepare_request_body(payload: dict[str, Any], *, budget: int = REQUEST_BUDGET_BYTES) -> bytes:
    """Check the complete UTF-8 body without truncating or changing content."""
    body = encode_request(payload)
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

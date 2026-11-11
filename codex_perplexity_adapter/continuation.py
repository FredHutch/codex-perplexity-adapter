"""Bounded, process-local continuation checkpoints containing keyed hashes only."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable


LOCAL_TOOL_TYPES = frozenset({"function_call", "custom_tool_call",
                              "function_call_output", "custom_tool_call_output"})


def has_local_tools(items: list[Any]) -> bool:
    return any(isinstance(item, dict) and item.get("type") in LOCAL_TOOL_TYPES for item in items)


def outstanding_calls(items: list[Any]) -> bool:
    pending = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        kind, call_id = item.get("type"), item.get("call_id")
        if kind in {"function_call", "custom_tool_call"}:
            if not isinstance(call_id, str) or not call_id:
                return True
            pending.add(call_id)
        elif kind in {"function_call_output", "custom_tool_call_output"} and isinstance(call_id, str):
            pending.discard(call_id)
    return bool(pending)


def canonical_item(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    result = dict(item)
    kind = result.get("type", "message" if "role" in result else None)
    if kind in {"message", "function_call", "custom_tool_call", "reasoning"}:
        result.pop("id", None)
        result.pop("status", None)
    if kind == "message":
        result["type"] = "message"
        content = result.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") in {"text", "input_text", "output_text"}:
                    part = {**part, "type": "text"}
                    if part.get("annotations") == []:
                        part.pop("annotations")
                    if part.get("logprobs") == []:
                        part.pop("logprobs")
                parts.append(part)
            content = parts
        result["content"] = content
    return result


def prior_response_error(payload: Any) -> bool:
    """Only errors unambiguously attributing rejection to continuation qualify."""
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
        return False
    error = payload["error"]
    if error.get("param") == "previous_response_id":
        return True
    # Do not infer this from a generic 400, model failure, or context error.
    return error.get("code") in {
        "previous_response_not_found", "previous_response_not_completed",
        "previous_response_expired", "previous_response_id_not_supported",
    }


@dataclass(frozen=True)
class Checkpoint:
    response_id: str
    items: tuple[bytes, ...]
    config: bytes
    instructions: bytes
    touched: float

    @property
    def size(self) -> int:
        # Include Python object/container overhead, with a conservative map allowance.
        return (512 + sys.getsizeof(self) + sys.getsizeof(self.response_id)
                + sys.getsizeof(self.items) + sum(sys.getsizeof(x) for x in self.items)
                + sys.getsizeof(self.config) + sys.getsizeof(self.instructions))


@dataclass
class Gate:
    lock: asyncio.Lock
    users: int = 0


class ContinuationManager:
    def __init__(self, *, enabled: bool = True, max_chats: int = 128,
                 max_bytes: int = 16 * 1024 * 1024, ttl: float = 3600,
                 clock: Callable[[], float] = time.monotonic, policy: str = "images-unchanged"):
        self.enabled, self.max_chats, self.max_bytes, self.ttl = enabled, max_chats, max_bytes, ttl
        self.clock = clock
        self.policy = policy
        self.secret = secrets.token_bytes(32)
        self.entries: OrderedDict[bytes, Checkpoint] = OrderedDict()
        self.gates: dict[bytes, Gate] = {}
        self.cache_bytes = 0
        self.continuations = self.replays = self.fallbacks = self.saved_bytes = 0
        self.tool_replays = 0

    def digest(self, value: Any) -> bytes:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()
        return hmac.digest(self.secret, encoded, hashlib.sha256)

    def session_key(self, session: str | None, thread: str | None) -> bytes | None:
        if not self.enabled or not session or not thread:
            return None
        return self.digest([session, thread])

    async def acquire(self, key: bytes | None) -> bytes | None:
        if key is None:
            return None
        # Active gates belong to in-flight requests, not the checkpoint cache.
        # Never bypass serialization when the bounded cache is full.
        gate = self.gates.get(key)
        if gate is None:
            gate = self.gates[key] = Gate(asyncio.Lock())
        gate.users += 1
        try:
            await gate.lock.acquire()
        except BaseException:
            gate.users -= 1
            if gate.users == 0:
                self.gates.pop(key, None)
            raise
        return key

    def release(self, key: bytes) -> None:
        gate = self.gates[key]
        gate.lock.release()
        gate.users -= 1
        if gate.users == 0:
            del self.gates[key]

    def invalidate(self, key: bytes) -> None:
        entry = self.entries.pop(key, None)
        if entry:
            self.cache_bytes -= entry.size

    def expire(self) -> None:
        now = self.clock()
        for key, entry in list(self.entries.items()):
            if now - entry.touched >= self.ttl:
                self.invalidate(key)

    def fingerprints(self, payload: dict[str, Any]) -> tuple[tuple[bytes, ...], bytes, bytes]:
        items = payload["input"]
        fingerprints = tuple(self.digest(canonical_item(item)) for item in items)
        config = self.digest([self.policy, {k: v for k, v in payload.items()
                              if k not in {"input", "stream", "previous_response_id"}}])
        instructions = self.digest([canonical_item(item) for item in items
                                    if isinstance(item, dict) and item.get("role") in {"developer", "system"}])
        return fingerprints, config, instructions

    def select(self, key: bytes, payload: dict[str, Any], fingerprints: tuple[tuple[bytes, ...], bytes, bytes]) -> dict[str, Any]:
        self.expire()
        entry = self.entries.get(key)
        items, config, instructions = fingerprints
        if (entry and entry.config == config and entry.instructions == instructions
                and len(items) > len(entry.items) and items[:len(entry.items)] == entry.items):
            suffix = payload["input"][len(entry.items):]
            if not has_local_tools(suffix):
                self.entries.move_to_end(key)
                return {**payload, "previous_response_id": entry.response_id, "input": suffix}
        self.invalidate(key)
        return payload

    def commit(self, key: bytes, response: dict[str, Any], fingerprints: tuple[tuple[bytes, ...], bytes, bytes],
               output: list[Any]) -> None:
        response_id = response.get("id")
        self.invalidate(key)
        if (response.get("status") != "completed" or not isinstance(response_id, str)
                or not response_id or len(response_id) > 256 or has_local_tools(output)):
            return
        items, config, instructions = fingerprints
        entry = Checkpoint(response_id, items + tuple(self.digest(canonical_item(item)) for item in output),
                           config, instructions, self.clock())
        self.expire()
        if entry.size > self.max_bytes:
            return
        while self.entries and (len(self.entries) >= self.max_chats or self.cache_bytes + entry.size > self.max_bytes):
            self.invalidate(next(iter(self.entries)))
        self.entries[key] = entry
        self.cache_bytes += entry.size

    def snapshot(self) -> dict[str, int]:
        self.expire()
        return {"continuation_requests": self.continuations, "full_replay_requests": self.replays,
                "continuation_fallbacks": self.fallbacks, "continuation_cache_entries": len(self.entries),
                "tool_boundary_replays": self.tool_replays,
                "saved_request_bytes": self.saved_bytes}

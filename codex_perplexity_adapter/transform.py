"""Protocol translation between Codex and the Perplexity Agent API."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolContext:
    custom_names: set[str] = field(default_factory=set)
    namespace_tools: dict[str, tuple[str, str]] = field(default_factory=dict)

    def upstream_name(self, namespace: str, name: str) -> str:
        for upstream, original in self.namespace_tools.items():
            if original == (namespace, name):
                return upstream
        return f"{namespace}__{name}"


def stringify_tool_output(output: Any) -> str:
    """Flatten Codex content blocks into Perplexity's string output format."""
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        parts: list[str] = []
        for block in output:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
            else:
                parts.append(json.dumps(block, separators=(",", ":")))
        return "".join(parts)
    if output is None:
        return ""
    return str(output)


def _normalize_history(items: Any) -> tuple[Any, list[dict[str, Any]]]:
    if not isinstance(items, list):
        return items, []

    normalized: list[Any] = []
    additional_tools: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            normalized.append(item)
            continue

        item_type = item.get("type")
        if item_type == "additional_tools":
            tools = item.get("tools")
            if isinstance(tools, list):
                additional_tools.extend(tool for tool in tools if isinstance(tool, dict))
            continue
        if item_type == "custom_tool_call":
            converted = dict(item)
            converted["type"] = "function_call"
            converted["arguments"] = json.dumps(
                {"content": converted.pop("input", "")}, separators=(",", ":")
            )
            normalized.append(converted)
            continue
        if item_type == "custom_tool_call_output":
            converted = dict(item)
            converted["type"] = "function_call_output"
            converted["output"] = stringify_tool_output(converted.get("output"))
            normalized.append(converted)
            continue
        if "type" not in item:
            converted = dict(item)
            converted["type"] = "message"
            normalized.append(converted)
            continue
        normalized.append(item)
    return normalized, additional_tools


def _normalize_tools(tools: Any) -> tuple[list[Any], ToolContext]:
    normalized: list[Any] = []
    context = ToolContext()

    def add_tool(tool: Any, namespace: str | None = None) -> None:
        if not isinstance(tool, dict):
            normalized.append(tool)
            return
        tool_type = tool.get("type")
        if tool_type == "namespace":
            namespace_name = tool.get("name")
            if not isinstance(namespace_name, str) or not namespace_name:
                namespace_name = namespace
            for nested in tool.get("tools") or []:
                add_tool(nested, namespace_name)
            return
        original_name = tool.get("name")
        upstream_name = original_name
        if namespace and isinstance(original_name, str) and original_name:
            upstream_name = f"{namespace}__{original_name}"
            context.namespace_tools[upstream_name] = (namespace, original_name)
        if tool_type != "custom":
            normalized.append({**tool, "name": upstream_name} if upstream_name != original_name else tool)
            return

        name = upstream_name if isinstance(upstream_name, str) else ""
        if not name:
            normalized.append(tool)
            return
        context.custom_names.add(name)
        description = tool.get("description") if isinstance(tool.get("description"), str) else ""
        fmt = tool.get("format")
        if isinstance(fmt, dict) and isinstance(fmt.get("definition"), str) and fmt["definition"]:
            description += f"\n\nFormat:\n```{fmt.get('syntax', '')}\n{fmt['definition']}\n```"
        normalized.append(
            {
                "type": "function",
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": f"The {name} content following the specified format",
                        }
                    },
                    "required": ["content"],
                },
                "strict": True,
            }
        )

    if isinstance(tools, list):
        for tool in tools:
            add_tool(tool)
    return normalized, context


def transform_request(payload: dict[str, Any], upstream_model: str) -> tuple[dict[str, Any], ToolContext]:
    """Translate a Codex Responses request into a Perplexity request."""
    transformed = dict(payload)
    # Codex client context is not part of Perplexity's Responses request schema.
    transformed.pop("client_metadata", None)
    additional_tools: list[dict[str, Any]] = []
    if "input" in transformed:
        transformed["input"], additional_tools = _normalize_history(transformed["input"])

    context = ToolContext()
    tools = transformed.get("tools")
    if isinstance(tools, list) or additional_tools:
        normalized_tools, context = _normalize_tools((tools if isinstance(tools, list) else []) + additional_tools)
        if normalized_tools:
            transformed["tools"] = normalized_tools
        else:
            transformed.pop("tools", None)

    if isinstance(transformed.get("input"), list):
        remapped = []
        for item in transformed["input"]:
            if isinstance(item, dict) and item.get("type") == "function_call":
                namespace, name = item.get("namespace"), item.get("name")
                if isinstance(namespace, str) and isinstance(name, str):
                    converted = dict(item)
                    converted["name"] = context.upstream_name(namespace, name)
                    converted.pop("namespace", None)
                    item = converted
            remapped.append(item)
        transformed["input"] = remapped

    tool_choice = transformed.get("tool_choice")
    if isinstance(tool_choice, dict):
        namespace, name = tool_choice.get("namespace"), tool_choice.get("name")
        if isinstance(namespace, str) and isinstance(name, str):
            transformed["tool_choice"] = {
                **{key: value for key, value in tool_choice.items() if key != "namespace"},
                "name": context.upstream_name(namespace, name),
            }

    if upstream_model.startswith("preset/"):
        transformed["preset"] = upstream_model.removeprefix("preset/")
        transformed.pop("model", None)
    else:
        transformed["model"] = upstream_model
    return transformed, context


def _unwrap_custom_arguments(arguments: Any) -> str:
    if not isinstance(arguments, str):
        return ""
    try:
        parsed = json.loads(arguments)
        if isinstance(parsed, dict) and "content" in parsed:
            return str(parsed["content"])
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return arguments


def _restore_item(item: Any, context: ToolContext) -> Any:
    if not isinstance(item, dict):
        return item
    if item.get("type") == "function_call" and item.get("name") in context.custom_names:
        restored = dict(item)
        restored["type"] = "custom_tool_call"
        restored["input"] = _unwrap_custom_arguments(restored.pop("arguments", ""))
    else:
        restored = item
    original = context.namespace_tools.get(restored.get("name"))
    if original:
        restored = dict(restored)
        restored["namespace"], restored["name"] = original
    return restored


def transform_response(payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    """Translate Perplexity response objects and stream events back to Codex."""
    restored = dict(payload)
    if isinstance(restored.get("item"), dict):
        restored["item"] = _restore_item(restored["item"], context)
    if isinstance(restored.get("output"), list):
        restored["output"] = [_restore_item(item, context) for item in restored["output"]]
    response = restored.get("response")
    if isinstance(response, dict):
        response = transform_response(response, context)
        restored["response"] = response

    usage = restored.get("usage")
    if isinstance(usage, dict) and isinstance(usage.get("cost"), dict):
        usage = dict(usage)
        usage["cost"] = usage["cost"].get("total_cost")
        restored["usage"] = usage
    return restored

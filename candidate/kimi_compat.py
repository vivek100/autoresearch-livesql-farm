"""Compatibility helpers for Kimi text-emitted tool calls."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.messages.tool import tool_call as create_tool_call


TOOL_CALL_PATTERN = re.compile(
    r"<\|tool_call_begin\|>\s*functions\.(?P<name>[\w_]+):(?P<id>[\w\-]+)\s*"
    r"<\|tool_call_argument_begin\|>\s*(?P<args>\{.*?\})\s*<\|tool_call_end\|>",
    re.DOTALL,
)


def maybe_parse_kimi_tool_calls(message: AIMessage) -> AIMessage:
    """Convert Kimi text-emitted tool call markup into structured tool_calls."""
    if message.tool_calls:
        return message

    content = message.content if isinstance(message.content, str) else ""
    if not content or "tool_call_begin" not in content:
        return message

    matches = list(TOOL_CALL_PATTERN.finditer(content))
    if not matches:
        return message

    parsed_calls: list[dict[str, Any]] = []
    for match in matches:
        try:
            args = json.loads(match.group("args"))
        except json.JSONDecodeError:
            continue
        parsed_calls.append(
            create_tool_call(
                name=match.group("name"),
                args=args,
                id=match.group("id"),
            )
        )

    if not parsed_calls:
        return message

    cleaned = content
    cleaned = re.sub(r"<\|tool_calls_section_begin\|>.*?<\|tool_calls_section_end\|>", "", cleaned, flags=re.DOTALL)
    cleaned = cleaned.replace("</think>", "").strip()

    return AIMessage(
        content=cleaned,
        tool_calls=parsed_calls,
        additional_kwargs=message.additional_kwargs,
        response_metadata=message.response_metadata,
        id=message.id,
        name=message.name,
        usage_metadata=message.usage_metadata,
    )

from __future__ import annotations

"""
Tracing helpers for candidate execution.

Two sources are supported:
1) Callback events collected during runtime (preferred when available).
2) Message-based reconstruction from final LangGraph messages (fallback).
"""

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler


@dataclass
class ToolTraceEvent:
    event_id: int
    type: str
    step_index: int
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "step_index": self.step_index,
            "payload": self.payload,
        }


class LocalTraceCallbackHandler(BaseCallbackHandler):
    """
    Minimal callback handler that records tool start/end/error events.

    This is intentionally lightweight and local-file focused.
    """

    def __init__(self) -> None:
        self._events: list[ToolTraceEvent] = []
        self._event_id = 1
        self._step_index = 0
        self._tool_run_to_step: dict[UUID, int] = {}

    def _next_event_id(self) -> int:
        current = self._event_id
        self._event_id += 1
        return current

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        step = self._step_index
        self._tool_run_to_step[run_id] = step
        name = serialized.get("name") or serialized.get("id") or "unknown_tool"
        self._events.append(
            ToolTraceEvent(
                event_id=self._next_event_id(),
                type="tool_call",
                step_index=step,
                payload={
                    "tool_name": str(name),
                    "input_str": input_str,
                    "inputs": inputs or {},
                    "metadata": metadata or {},
                },
            )
        )

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        step = self._tool_run_to_step.pop(run_id, self._step_index)
        self._events.append(
            ToolTraceEvent(
                event_id=self._next_event_id(),
                type="tool_result",
                step_index=step,
                payload={"result": output},
            )
        )
        self._step_index += 1

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        step = self._tool_run_to_step.pop(run_id, self._step_index)
        self._events.append(
            ToolTraceEvent(
                event_id=self._next_event_id(),
                type="error",
                step_index=step,
                payload={"error": str(error)},
            )
        )
        self._step_index += 1

    def to_events(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._events]


def _tool_calls_from_ai(msg: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if isinstance(msg, dict):
        raw_calls = msg.get("tool_calls") or []
    else:
        raw_calls = getattr(msg, "tool_calls", None) or []

    for call in raw_calls:
        if isinstance(call, dict):
            call_id = call.get("id") or call.get("tool_call_id")
            name = call.get("name")
            args = call.get("args")
            if name is None and isinstance(call.get("function"), dict):
                name = call["function"].get("name")
                args = call["function"].get("arguments")
        else:
            call_id = getattr(call, "id", None) or getattr(call, "tool_call_id", None)
            name = getattr(call, "name", None)
            args = getattr(call, "args", None)
            fn = getattr(call, "function", None)
            if name is None and fn is not None:
                name = getattr(fn, "name", None)
                args = getattr(fn, "arguments", args)

        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if not isinstance(args, dict):
            args = {}
        calls.append({"id": call_id, "name": name or "unknown_tool", "args": args})
    return calls


def events_from_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """
    Fallback reconstruction when callback events are unavailable.
    """
    events: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    event_id = 1
    step = 0

    for msg in messages:
        if isinstance(msg, dict):
            kind = str(msg.get("type") or msg.get("role") or "")
        else:
            kind = str(getattr(msg, "type", None) or getattr(msg, "role", None) or "")

        if kind in ("ai", "assistant"):
            for call in _tool_calls_from_ai(msg):
                pending[str(call["id"])] = call
                events.append(
                    {
                        "event_id": event_id,
                        "type": "tool_call",
                        "step_index": step,
                        "payload": {"tool_name": call["name"], "args": call["args"]},
                    }
                )
                event_id += 1
        elif kind == "tool":
            tcid = str(msg.get("tool_call_id")) if isinstance(msg, dict) else str(getattr(msg, "tool_call_id", ""))
            call = pending.pop(tcid, None)
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
            events.append(
                {
                    "event_id": event_id,
                    "type": "tool_result",
                    "step_index": step,
                    "payload": {
                        "tool_name": call["name"] if call else "unknown_tool",
                        "result": content,
                    },
                }
            )
            event_id += 1
            step += 1
    return events


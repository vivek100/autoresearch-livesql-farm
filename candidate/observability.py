from __future__ import annotations

"""
Prebuilt observability setup for LangGraph/LangChain runs.

Default behavior enables OpenInference + OTLP export to Phoenix.
Set TRACE_BACKEND=none to disable.
"""

import os
import socket
from threading import Lock
from typing import Any
from urllib.parse import urlparse

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

_INIT_LOCK = Lock()
_STATE: dict[str, Any] = {
    "initialized": False,
    "backend": None,
}
_CAPTURE_EXPORTER: "_JsonCaptureSpanExporter | None" = None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return str(value)


def _hex(value: int, width: int) -> str:
    return format(int(value), f"0{width}x")


def _span_to_dict(span: ReadableSpan) -> dict[str, Any]:
    parent = span.parent
    parent_span_id = None
    if parent is not None and getattr(parent, "span_id", None) is not None:
        parent_span_id = _hex(parent.span_id, 16)

    events: list[dict[str, Any]] = []
    for ev in span.events:
        events.append(
            {
                "name": ev.name,
                "timestamp_ns": ev.timestamp,
                "attributes": _json_safe(dict(ev.attributes or {})),
            }
        )

    links: list[dict[str, Any]] = []
    for link in span.links:
        links.append(
            {
                "trace_id": _hex(link.context.trace_id, 32),
                "span_id": _hex(link.context.span_id, 16),
                "attributes": _json_safe(dict(link.attributes or {})),
            }
        )

    status = span.status
    status_payload = {
        "status_code": getattr(getattr(status, "status_code", None), "name", None),
        "description": getattr(status, "description", None),
    }

    scope = getattr(span, "instrumentation_scope", None)
    scope_payload = {
        "name": getattr(scope, "name", None),
        "version": getattr(scope, "version", None),
    }

    resource_attrs = {}
    if span.resource is not None:
        resource_attrs = dict(getattr(span.resource, "attributes", {}) or {})

    return {
        "name": span.name,
        "context": {
            "trace_id": _hex(span.context.trace_id, 32),
            "span_id": _hex(span.context.span_id, 16),
            "trace_flags": int(span.context.trace_flags),
        },
        "parent_span_id": parent_span_id,
        "kind": getattr(span.kind, "name", str(span.kind)),
        "start_time_ns": span.start_time,
        "end_time_ns": span.end_time,
        "attributes": _json_safe(dict(span.attributes or {})),
        "events": events,
        "links": links,
        "status": status_payload,
        "instrumentation_scope": _json_safe(scope_payload),
        "resource": _json_safe(resource_attrs),
    }


class _JsonCaptureSpanExporter(SpanExporter):
    """
    In-process span exporter that keeps JSON-safe span dictionaries.

    Used to persist Phoenix/OpenTelemetry spans into run artifacts.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._spans: list[dict[str, Any]] = []

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        payload = [_span_to_dict(span) for span in spans]
        with self._lock:
            self._spans.extend(payload)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    def mark(self) -> int:
        with self._lock:
            return len(self._spans)

    def get_since(self, offset: int) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._spans[offset:])


def _normalize_backend(value: str | None) -> str:
    raw = (value or "phoenix").strip().lower()
    if raw in {"off", "disabled"}:
        return "none"
    return raw


def configure_observability() -> dict[str, Any]:
    """
    Configure process-level tracing once.

    Returns a small state dict for logging/debugging.
    """
    with _INIT_LOCK:
        if _STATE["initialized"]:
            return dict(_STATE)

        backend = _normalize_backend(os.environ.get("TRACE_BACKEND"))
        if backend == "none":
            _STATE.update({"initialized": True, "backend": "none", "enabled": False})
            return dict(_STATE)

        if backend != "phoenix":
            raise RuntimeError(f"Unsupported TRACE_BACKEND='{backend}'. Expected 'phoenix' or 'none'.")

        endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "http://127.0.0.1:6006/v1/traces")
        service_name = os.environ.get("OTEL_SERVICE_NAME", "analytics-agent-autoresearch")
        parsed = urlparse(endpoint)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        collector_reachable = True
        try:
            with socket.create_connection((host, port), timeout=0.4):
                pass
        except OSError:
            collector_reachable = False

        # Configure OTLP exporter defaults for local Phoenix collector.
        os.environ.setdefault("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", endpoint)
        os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", endpoint.rsplit("/v1/traces", 1)[0])
        os.environ.setdefault("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
        os.environ.setdefault("OTEL_SERVICE_NAME", service_name)

        from openinference.instrumentation.langchain import LangChainInstrumentor
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        global _CAPTURE_EXPORTER
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        _CAPTURE_EXPORTER = _JsonCaptureSpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(_CAPTURE_EXPORTER))
        if collector_reachable:
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        LangChainInstrumentor().instrument()

        state_payload = {
            "initialized": True,
            "backend": "phoenix",
            "enabled": True,
            "collector_endpoint": endpoint,
            "service_name": service_name,
            "remote_export_enabled": collector_reachable,
        }
        if not collector_reachable:
            state_payload["remote_export_disabled_reason"] = "collector_unreachable"

        _STATE.update(
            state_payload
        )
        return dict(_STATE)


def mark_span_offset() -> int:
    if _CAPTURE_EXPORTER is None:
        return 0
    return _CAPTURE_EXPORTER.mark()


def spans_since(offset: int) -> list[dict[str, Any]]:
    if _CAPTURE_EXPORTER is None:
        return []
    return _CAPTURE_EXPORTER.get_since(offset)


def force_flush_observability(timeout_millis: int = 2_000) -> None:
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        flush = getattr(provider, "force_flush", None)
        if callable(flush):
            try:
                flush(timeout_millis=timeout_millis)
            except TypeError:
                flush()
    except Exception:
        # Tracing is best-effort and should never break agent execution.
        return

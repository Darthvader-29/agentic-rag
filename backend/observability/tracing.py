"""Phase 7: OpenTelemetry tracing bootstrap.

``init_tracing`` is idempotent and gated on ``Settings.OTEL_ENABLED``. When enabled it installs a
``TracerProvider`` (a ``service.name`` resource + ratio sampler) with a ``BatchSpanProcessor``
exporting to the configured OTLP endpoint, or a ``ConsoleSpanExporter`` fallback when no endpoint
is set — so a missing/unreachable collector never blocks startup or requests (batch export is async
and a console fallback can't fail a request).

``get_tracer`` returns a tracer from whatever provider is globally installed — the default no-op
``ProxyTracer`` when tracing is disabled — so the explicit spans sprinkled across the request path
(``chat.request``, ``agent.*``, ``memory.*``, ``ingest.document``) are always safe to create and
near-zero-cost when OTEL is off. Tests install their own ``TracerProvider`` + in-memory exporter and
assert those span names appear.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

from config import Settings

_INITIALIZED = False


def init_tracing(settings: Settings) -> None:
    """Install the global ``TracerProvider`` once, if ``OTEL_ENABLED``. No-op otherwise."""
    global _INITIALIZED
    if _INITIALIZED or not settings.OTEL_ENABLED:
        return

    resource = Resource.create({"service.name": settings.OTEL_SERVICE_NAME})
    provider = TracerProvider(
        resource=resource,
        sampler=TraceIdRatioBased(settings.OTEL_SAMPLE_RATIO),
    )
    if settings.OTEL_EXPORTER_ENDPOINT:
        # Imported lazily — the gRPC exporter is only needed when an endpoint is configured.
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        exporter: object = OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_ENDPOINT)
    else:
        exporter = ConsoleSpanExporter()  # safe fallback; never blocks requests
    provider.add_span_processor(BatchSpanProcessor(exporter))  # type: ignore[arg-type]
    trace.set_tracer_provider(provider)
    _INITIALIZED = True


def get_tracer(name: str = "agentic-rag") -> trace.Tracer:
    """Tracer from the globally-installed provider (no-op when tracing is disabled)."""
    return trace.get_tracer(name)

"""Phase 7: Celery trace-context propagation helpers + worker tracing init.

Eager-mode tests never start a real worker process, so the ``worker_process_init`` signal doesn't
fire; the inject/extract propagation contract and the disabled-init no-op are tested directly.
"""

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from worker.tracing import extract_trace_context, init_worker_tracing, inject_trace_context


def _ensure_provider() -> None:
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(TracerProvider())


def test_inject_then_extract_roundtrips():
    _ensure_provider()
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("parent"):
        headers: dict = {}
        inject_trace_context(headers)
        assert "traceparent" in headers  # active span → W3C context written

    ctx = extract_trace_context(headers)
    assert ctx is not None
    # The extracted context carries the parent's (non-zero) trace id.
    assert trace.get_current_span(ctx).get_span_context().trace_id != 0


def test_extract_handles_empty_or_none_carrier():
    assert extract_trace_context({}) is not None
    assert extract_trace_context(None) is not None


def test_init_worker_tracing_disabled_is_noop():
    from config import Settings

    # OTEL_ENABLED defaults False → no instrumentation, must not raise.
    init_worker_tracing(Settings())

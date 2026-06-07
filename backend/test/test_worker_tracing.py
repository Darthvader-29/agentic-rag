"""Phase 7: worker tracing init.

Eager-mode tests never start a real worker process, so the ``worker_process_init`` signal doesn't
fire; the disabled-init no-op is tested directly.
"""

from worker.tracing import init_worker_tracing


def test_init_worker_tracing_disabled_is_noop():
    from config import Settings

    # OTEL_ENABLED defaults False → no instrumentation, must not raise.
    init_worker_tracing(Settings())

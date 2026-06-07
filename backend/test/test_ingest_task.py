"""Phase 5: the Celery ingest task wrapper (eager mode — runs inline)."""

import pytest


def test_ingest_task_registered():
    import worker.tasks  # noqa: F401  (importing registers the task)
    from worker.celery_app import celery_app

    assert "worker.tasks.ingest_document" in celery_app.tasks


def test_ingest_runs_pipeline_with_args(monkeypatch):
    """The task forwards (s3_key, filename, session_id) to the pipeline and succeeds."""
    from worker.tasks import ingest_document

    called = {}

    async def fake_run(document_id, s3_key, filename, session_id, user_id, settings):
        called["args"] = (s3_key, filename, session_id)

    monkeypatch.setattr("worker.tasks._run_pipeline", fake_run)

    result = ingest_document.delay(
        document_id="d1",
        s3_key="uploads/u1/x.pdf",
        filename="x.pdf",
        session_id="s1",
    ).get()

    assert result is None
    assert called["args"] == ("uploads/u1/x.pdf", "x.pdf", "s1")


def test_ingest_marks_failed_and_reraises(monkeypatch):
    """A pipeline error marks the document FAILED (by s3_key) and re-raises."""
    from worker.tasks import ingest_document

    async def boom(*args, **kwargs):
        raise RuntimeError("parse error")

    marked = {}

    async def fake_mark(s3_key, settings):
        marked["s3_key"] = s3_key

    monkeypatch.setattr("worker.tasks._run_pipeline", boom)
    monkeypatch.setattr("worker.tasks._mark_failed", fake_mark)

    with pytest.raises(RuntimeError, match="parse error"):
        ingest_document.delay(
            document_id="d2",
            s3_key="uploads/u1/bad.pdf",
            filename="bad.pdf",
            session_id="s1",
        ).get()

    assert marked["s3_key"] == "uploads/u1/bad.pdf"

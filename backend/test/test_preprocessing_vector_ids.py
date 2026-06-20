"""R15: vector ids are per-document unique so same-named docs don't clobber each other.

The ingestion pipeline used to mint chunk ids as ``{session_id}_{filename}_{i}``. Two documents
with the SAME filename in one session therefore produced identical ids and silently overwrote each
other on upsert; a shorter re-ingest also left stale high-index chunks that still matched search.
Including the Document row UUID in the id fixes both. These tests drive the real
``process_file_pipeline`` with fake collaborators and assert the ids it hands to Pinecone.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from components.preprocessing import process_file_pipeline


def _fake_session_factory() -> object:
    """A session factory whose ``factory()`` is an async context manager (status writes are no-ops)."""

    @asynccontextmanager
    async def _factory():
        db = AsyncMock()
        yield db

    return _factory


def _fake_embedder(n_dims: int = 3) -> AsyncMock:
    emb = AsyncMock()
    # One embedding per chunk; embed_batch is called with the chunk list.
    emb.embed_batch.side_effect = lambda chunks, batch_size=32: [[0.1] * n_dims for _ in chunks]
    return emb


async def _run(*, session_id: str, filename: str, document_id: str | None, text: str) -> list[dict]:
    """Run the pipeline with fakes and return the vector list passed to pinecone.save_vectors."""
    s3 = AsyncMock()
    s3.download_to_temp.return_value = "/tmp/whatever"
    embedder = _fake_embedder()
    pinecone = AsyncMock()
    captured: dict = {}

    async def _save(vectors: list[dict]) -> None:
        captured["vectors"] = vectors

    pinecone.save_vectors.side_effect = _save

    with (
        patch("components.preprocessing.DocumentParser.extract_content", return_value=text),
        patch("components.preprocessing.os.path.exists", return_value=False),
    ):
        await process_file_pipeline(
            f"uploads/{filename}",
            filename,
            session_id,
            s3,
            embedder,
            pinecone,
            _fake_session_factory(),
            user_id="user-1",
            document_id=document_id,
        )
    return captured.get("vectors", [])


@pytest.mark.asyncio
async def test_vector_ids_include_document_id():
    """Each chunk id embeds the document UUID and stays prefixed with the session id."""
    vectors = await _run(
        session_id="sess1",
        filename="report.pdf",
        document_id="doc-aaaa",
        text="word " * 600,  # long enough to produce >1 chunk
    )
    assert len(vectors) >= 1
    for i, vec in enumerate(vectors):
        assert vec["id"] == f"sess1_doc-aaaa_{i:04d}"
        assert vec["id"].startswith("sess1_")  # by-session prefix cleanup still matches
        assert vec["metadata"]["document_id"] == "doc-aaaa"


@pytest.mark.asyncio
async def test_same_named_docs_keep_distinct_ids_and_dont_clobber():
    """Two same-named files in one session produce DISJOINT id sets — both stay retrievable."""
    text = "alpha beta gamma delta " * 100
    a = await _run(session_id="sess1", filename="same.pdf", document_id="doc-A", text=text)
    b = await _run(session_id="sess1", filename="same.pdf", document_id="doc-B", text=text)

    ids_a = {v["id"] for v in a}
    ids_b = {v["id"] for v in b}

    assert ids_a and ids_b
    # The old scheme would have made these identical (filename-only) → an upsert clobbered doc A.
    assert ids_a.isdisjoint(ids_b)
    assert all(i.startswith("sess1_doc-A_") for i in ids_a)
    assert all(i.startswith("sess1_doc-B_") for i in ids_b)


@pytest.mark.asyncio
async def test_by_session_prefix_still_covers_every_document():
    """Both documents' ids share the ``{session_id}_`` prefix the cleanup delete enumerates."""
    a = await _run(session_id="sess1", filename="a.pdf", document_id="doc-A", text="a " * 100)
    b = await _run(session_id="sess1", filename="b.pdf", document_id="doc-B", text="b " * 100)

    prefix = "sess1_"
    assert all(v["id"].startswith(prefix) for v in a + b)


@pytest.mark.asyncio
async def test_falls_back_to_filename_when_no_document_id():
    """Legacy/direct callers (no document_id) keep the filename-slug id — no metadata key added."""
    vectors = await _run(
        session_id="sess1", filename="my file.pdf", document_id=None, text="x " * 100
    )
    assert vectors
    for i, vec in enumerate(vectors):
        assert vec["id"] == f"sess1_my_file.pdf_{i:04d}"
        assert "document_id" not in vec["metadata"]

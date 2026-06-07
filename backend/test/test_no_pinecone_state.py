"""Static analysis gate: assert no dummy-vector state queries remain.

These tests always run (no DB required). They guard the Phase 2 exit criterion:
zero top_k=1000 dummy-vector scans and zero Pinecone state methods.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE_DIRS = ["app.py", "components", "database", "integrations", "dependencies.py"]


def _source_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for entry in SOURCE_DIRS:
        p = ROOT / entry
        if p.is_file():
            files.append(p)
        else:
            files.extend(p.rglob("*.py"))
    return files


def test_no_dummy_vector_state_queries():
    """No source file may contain top_k=1000 or [0.0]*DIMENSION patterns."""
    pattern = re.compile(r"top_k\s*=\s*1000|\[0\.0\]\s*\*\s*(DIMENSION|384)")
    offenders = [str(f) for f in _source_files() if pattern.search(f.read_text())]
    assert not offenders, "Dummy-vector state query still present:\n" + "\n".join(offenders)


def test_pinecone_client_has_no_state_methods():
    """PineconeClient must not expose has_session_documents or list_s3_keys_for_session."""
    from database.db_manager import PineconeClient

    assert not hasattr(PineconeClient, "has_session_documents"), (
        "has_session_documents must be removed from PineconeClient (Phase 2 moved it to repo)"
    )
    assert not hasattr(PineconeClient, "list_s3_keys_for_session"), (
        "list_s3_keys_for_session must be removed from PineconeClient (Phase 2 moved it to repo)"
    )

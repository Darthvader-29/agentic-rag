"""Phase 5: upload endpoints — multipart (legacy) + presigned PUT + confirm + status.

Celery is patched out (``app.ingest_document``) so enqueue never runs the eager task;
DB/S3/auth are DI-overridden, repo functions patched.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import app
from auth.dependencies import get_current_user
from database.db_manager import PineconeClient
from database.models import DocumentStatus, User
from dependencies import get_db_session, get_s3_client


@pytest.fixture
def fake_user():
    u = MagicMock(spec=User)
    u.id = uuid.uuid4()
    u.email = "up@example.com"
    u.username = "upuser"
    return u


@pytest.fixture
def up_client(fake_user):
    fake_s3 = AsyncMock()
    fake_s3.upload_fileobj.return_value = "uploads/legacy_x.pdf"
    fake_s3.generate_presigned_url.return_value = "https://s3.example/put?sig=1"
    fake_s3.object_exists.return_value = True
    # make_user_key is a sync staticmethod — must return a str, not a coroutine
    fake_s3.make_user_key = MagicMock(side_effect=lambda uid, fn: f"uploads/{uid}/uuid_{fn}")
    fake_db = AsyncMock()

    async def _db_override():
        yield fake_db

    app.dependency_overrides[get_s3_client] = lambda: fake_s3
    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_current_user] = lambda: fake_user

    with patch.object(PineconeClient, "ensure_index", new_callable=AsyncMock):
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, fake_s3

    app.dependency_overrides.clear()


def _doc(**over):
    d = MagicMock()
    d.id = over.get("id", "doc-123")
    d.filename = over.get("filename", "x.pdf")
    d.session_id = over.get("session_id", "s1")
    d.s3_key = over.get("s3_key", "uploads/u/uuid_x.pdf")
    d.status = over.get("status", DocumentStatus.PENDING)
    return d


# ── presigned path (application/json) ──────────────────────────────────────────


def test_upload_presign_returns_url_and_document_id(up_client, fake_user):
    client, fake_s3 = up_client
    with (
        patch("app.repo.get_session", new_callable=AsyncMock, return_value=None),
        patch("app.repo.create_session", new_callable=AsyncMock),
        patch("app.repo.create_document", new_callable=AsyncMock, return_value=_doc()),
        patch.object(app_module, "ingest_document") as task,
    ):
        resp = client.post(
            "/api/upload",
            json={"filename": "x.pdf", "content_type": "application/pdf", "session_id": "s1"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["document_id"] == "doc-123"
    assert body["upload_url"] == "https://s3.example/put?sig=1"
    assert body["s3_key"].endswith("uuid_x.pdf")
    assert body["session_id"] == "s1"
    fake_s3.generate_presigned_url.assert_awaited_once()
    task.delay.assert_not_called()  # presign does NOT enqueue — confirm does


def test_presign_malformed_json_returns_422(up_client):
    """B17: a request defect (invalid JSON) on the presign path is a 422, not a blanket 500."""
    client, _ = up_client
    resp = client.post(
        "/api/upload",
        content="{not valid json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422


def test_presign_overlong_session_id_returns_422(up_client):
    """B16+B17: an over-long session_id fails PresignRequest validation → surfaced as 422."""
    client, _ = up_client
    resp = client.post(
        "/api/upload",
        json={"filename": "x.pdf", "content_type": "application/pdf", "session_id": "x" * 65},
    )
    assert resp.status_code == 422


# ── multipart legacy path (multipart/form-data) ────────────────────────────────


def test_upload_multipart_uploads_and_enqueues(up_client, fake_user):
    client, fake_s3 = up_client
    with (
        patch("app.repo.get_session", new_callable=AsyncMock, return_value=None),
        patch("app.repo.create_session", new_callable=AsyncMock),
        patch("app.repo.create_document", new_callable=AsyncMock, return_value=_doc()),
        patch.object(app_module, "ingest_document") as task,
    ):
        resp = client.post(
            "/api/upload",
            files={"file": ("x.pdf", b"%PDF-1.4 data", "application/pdf")},
            data={"session_id": "s1"},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "processing"
    fake_s3.upload_fileobj.assert_awaited_once()
    task.delay.assert_called_once()  # legacy path enqueues ingestion immediately


# ── confirm ────────────────────────────────────────────────────────────────────


def test_confirm_enqueues_when_object_present(up_client, fake_user):
    client, fake_s3 = up_client
    owned_session = MagicMock()
    owned_session.user_id = fake_user.id
    with (
        patch("app.repo.get_document", new_callable=AsyncMock, return_value=_doc()),
        patch("app.repo.get_session", new_callable=AsyncMock, return_value=owned_session),
        patch.object(app_module, "ingest_document") as task,
    ):
        resp = client.post(
            "/api/upload/confirm",
            json={"document_id": "doc-123", "s3_key": "uploads/u/uuid_x.pdf"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"document_id": "doc-123", "status": "queued"}
    task.delay.assert_called_once()


def test_confirm_409_when_object_missing(up_client, fake_user):
    client, fake_s3 = up_client
    fake_s3.object_exists.return_value = False
    owned_session = MagicMock()
    owned_session.user_id = fake_user.id

    # B03 regression: the FAILED status must be committed BEFORE the 409 unwinds. Otherwise
    # get_db_session's ``except: rollback()`` erases the UPDATE and the document stays "pending".
    # Re-override the request db with a recorder so we can assert the commit happened, after the
    # status write.
    order: list[str] = []

    rec_db = AsyncMock()
    rec_db.commit.side_effect = lambda: order.append("commit")

    async def _rec_db_override():
        yield rec_db

    app.dependency_overrides[get_db_session] = _rec_db_override

    with (
        patch("app.repo.get_document", new_callable=AsyncMock, return_value=_doc()),
        patch("app.repo.get_session", new_callable=AsyncMock, return_value=owned_session),
        patch(
            "app.repo.set_document_status_by_id",
            new_callable=AsyncMock,
            side_effect=lambda *a, **k: order.append("mark_failed"),
        ) as mark,
        patch.object(app_module, "ingest_document") as task,
    ):
        resp = client.post(
            "/api/upload/confirm",
            json={"document_id": "doc-123", "s3_key": "uploads/u/uuid_x.pdf"},
        )

    assert resp.status_code == 409
    mark.assert_awaited_once()  # document marked FAILED
    task.delay.assert_not_called()
    # the FAILED status was committed, strictly after it was written → survives the 409 rollback
    assert order == ["mark_failed", "commit"], f"order={order}"


def test_confirm_404_when_document_missing(up_client):
    client, _ = up_client
    with patch("app.repo.get_document", new_callable=AsyncMock, return_value=None):
        resp = client.post(
            "/api/upload/confirm",
            json={"document_id": "nope", "s3_key": "k"},
        )
    assert resp.status_code == 404


def test_confirm_404_when_not_owner(up_client):
    client, _ = up_client
    other_session = MagicMock()
    other_session.user_id = uuid.uuid4()  # a different user
    with (
        patch("app.repo.get_document", new_callable=AsyncMock, return_value=_doc()),
        patch("app.repo.get_session", new_callable=AsyncMock, return_value=other_session),
    ):
        resp = client.post(
            "/api/upload/confirm",
            json={"document_id": "doc-123", "s3_key": "k"},
        )
    assert resp.status_code == 404


def test_confirm_ignores_client_s3_key_and_uses_owned_doc(up_client, fake_user):
    """C2: the existence probe + ingest derive from the OWNED document's s3_key, never the
    client-supplied one — a forged s3_key cannot probe/ingest another tenant's object."""
    client, fake_s3 = up_client
    owned_session = MagicMock()
    owned_session.user_id = fake_user.id
    doc = _doc(s3_key="uploads/owner/real.pdf")
    with (
        patch("app.repo.get_document", new_callable=AsyncMock, return_value=doc),
        patch("app.repo.get_session", new_callable=AsyncMock, return_value=owned_session),
        patch.object(app_module, "ingest_document") as task,
    ):
        resp = client.post(
            "/api/upload/confirm",
            json={"document_id": "doc-123", "s3_key": "uploads/victim/secret.pdf"},
        )

    assert resp.status_code == 200
    # the forged body s3_key is ignored; doc.s3_key is authoritative
    fake_s3.object_exists.assert_awaited_once_with("uploads/owner/real.pdf")
    assert task.delay.call_args.kwargs["s3_key"] == "uploads/owner/real.pdf"
    assert task.delay.call_args.kwargs["user_id"] == str(fake_user.id)


# ── document status poll ───────────────────────────────────────────────────────


def test_get_document_status_returns_normalized_status(up_client, fake_user):
    client, _ = up_client
    owned_session = MagicMock()
    owned_session.user_id = fake_user.id
    with (
        patch(
            "app.repo.get_document",
            new_callable=AsyncMock,
            return_value=_doc(status=DocumentStatus.READY),
        ),
        patch("app.repo.get_session", new_callable=AsyncMock, return_value=owned_session),
    ):
        resp = client.get("/api/documents/doc-123")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "doc-123"
    assert body["status"] == "ready"  # lowercase enum value the frontend M8 expects
    assert body["filename"] == "x.pdf"


def test_get_document_status_404_when_missing(up_client):
    client, _ = up_client
    with patch("app.repo.get_document", new_callable=AsyncMock, return_value=None):
        resp = client.get("/api/documents/nope")
    assert resp.status_code == 404

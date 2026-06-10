"""C1 (tenant isolation): session-ownership predicates in app.py.

Offline — exercises the pure predicate + the resolve path with repo calls patched, so no DB is
needed. Verifies that unowned (user_id IS NULL) and other-owner sessions are refused and never
auto-claimed (the pre-fix behavior that let a guessed/leaked session_id be adopted by any caller).
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import app as app_module


def _user(uid=None):
    u = MagicMock()
    u.id = uid or uuid.uuid4()
    return u


def _sess(user_id):
    s = MagicMock()
    s.user_id = user_id
    return s


def test_session_accessible_only_for_exact_owner():
    user = _user()
    assert app_module._session_accessible(_sess(user.id), user) is True
    assert app_module._session_accessible(_sess(uuid.uuid4()), user) is False  # another owner
    assert (
        app_module._session_accessible(_sess(None), user) is False
    )  # unowned: no longer accessible
    assert app_module._session_accessible(None, user) is False  # missing session


@pytest.mark.asyncio
async def test_resolve_session_refuses_unowned_and_other_owner():
    user = _user()
    db = AsyncMock()
    # legacy unowned row: must 403 (NOT silently claimed)
    with patch("app.repo.get_session", new_callable=AsyncMock, return_value=_sess(None)):
        with pytest.raises(HTTPException) as ei:
            await app_module._resolve_session(db, "sess-x", user)
        assert ei.value.status_code == 403
    # another user's row: 403
    with patch("app.repo.get_session", new_callable=AsyncMock, return_value=_sess(uuid.uuid4())):
        with pytest.raises(HTTPException) as ei:
            await app_module._resolve_session(db, "sess-x", user)
        assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_resolve_session_creates_owned_when_absent():
    user = _user()
    db = AsyncMock()
    with (
        patch("app.repo.get_session", new_callable=AsyncMock, return_value=None),
        patch("app.repo.create_session", new_callable=AsyncMock) as create,
    ):
        sid = await app_module._resolve_session(db, "new-sess", user)
    assert sid == "new-sess"
    create.assert_awaited_once_with(db, "new-sess", user.id)

"""Phase 7: Langfuse init gating (no network; the client is stubbed)."""

import sys
import types

from config import Settings
from observability.langfuse import init_langfuse


def test_init_langfuse_disabled_returns_false():
    assert init_langfuse(Settings(LANGFUSE_ENABLED=False)) is False


def test_init_langfuse_enabled_without_keys_returns_false():
    # enabled flag but no public/secret key → cannot activate
    assert init_langfuse(Settings(LANGFUSE_ENABLED=True)) is False


def test_init_langfuse_enabled_with_keys_activates(monkeypatch):
    called: dict = {}
    fake = types.ModuleType("langfuse")
    fake.get_client = lambda: called.setdefault("client", True)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", fake)

    s = Settings(LANGFUSE_ENABLED=True, LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="sk")
    assert init_langfuse(s) is True
    assert called["client"] is True


def test_init_langfuse_swallows_client_errors(monkeypatch):
    def _boom():
        raise RuntimeError("langfuse down")

    fake = types.ModuleType("langfuse")
    fake.get_client = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", fake)

    s = Settings(LANGFUSE_ENABLED=True, LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="sk")
    assert init_langfuse(s) is False  # error is logged, never raised

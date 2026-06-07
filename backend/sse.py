"""Server-Sent Events framing for the Phase 6 streaming chat endpoint.

One tiny, unit-testable helper: :func:`sse_event` turns a named event + JSON payload into a
single SSE frame (``event:``/``data:`` lines terminated by a blank line). The chat endpoint
yields these for ``status`` / ``token`` / ``component`` / ``done`` / ``error`` events.
"""

from __future__ import annotations

import json
from typing import Any


def sse_event(event: str, data: dict[str, Any]) -> str:
    """Frame one Server-Sent Event: ``event: <name>\\ndata: <json>\\n\\n``."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

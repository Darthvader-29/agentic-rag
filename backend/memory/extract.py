"""Phase 7: entity/relation extraction for the knowledge-graph layer.

Runs inside the Celery ingestion task (off the request path), so it uses the OPERATOR's fallback
(Gemini) key — a worker has no per-user BYOK key. Tries an ordered fallback chain of models
(``ENTITY_EXTRACTION_MODELS``): on a transient/quota failure it falls through to the next model; if
every model fails (or extraction is disabled / no fallback key) it returns no triples rather than
failing ingestion — entity extraction is best-effort enrichment, never a hard dependency.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import structlog

from config import Settings
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)

Triple = tuple[str, str, str]
# A "completion" is (model, prompt) -> text. Injected in tests; defaults to a Gemini client.
Complete = Callable[[str, str], Awaitable[str]]

_EXTRACT_PROMPT = """Extract the key entities and their relationships from the text below.
Return ONLY a JSON array of [subject, relation, object] triples (each a 3-element array of short
strings). No prose, no markdown. Use concise canonical entity names; skip pronouns and filler.
Return at most 30 triples.

TEXT:
{text}
"""


def _build_gemini_complete(api_key: str) -> Complete:
    """Default completion: the operator's Gemini key via google-genai (run off the event loop)."""
    import anyio
    from google import genai

    client = genai.Client(api_key=api_key)

    async def _complete(model: str, prompt: str) -> str:
        resp = await anyio.to_thread.run_sync(
            lambda: client.models.generate_content(model=model, contents=prompt)
        )
        return (resp.text or "").strip()

    return _complete


def _parse_triples(raw: str) -> list[Triple]:
    """Parse a model response into clean (s, rel, o) triples; tolerant of code fences / stray text."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        nl = text.find("\n")
        if nl != -1 and text[:nl].strip().lower() in {"json", ""}:
            text = text[nl + 1 :]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        arr = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    out: list[Triple] = []
    for item in arr if isinstance(arr, list) else []:
        if isinstance(item, (list, tuple)) and len(item) == 3:
            s, rel, o = (str(x).strip() for x in item)
            if s and rel and o:
                out.append((s, rel, o))
    return out


async def extract_triples(
    text: str,
    settings: Settings,
    *,
    complete: Complete | None = None,
) -> list[Triple]:
    """Extract (subject, relation, object) triples via the operator-key Gemini fallback chain.

    Returns ``[]`` when extraction is disabled, no fallback key is set, the text is empty, or every
    model in the chain fails. A model that succeeds with zero triples is a valid (empty) result.
    """
    if not text.strip() or not settings.entity_extraction_active:
        return []
    if complete is None:
        key = settings.LLM_FALLBACK_API_KEY.get_secret_value()
        if not key:
            return []
        complete = _build_gemini_complete(key)

    prompt = _EXTRACT_PROMPT.format(text=text[:12000])  # cap input to bound cost/latency
    with get_tracer().start_as_current_span("memory.extract") as span:
        for model in settings.ENTITY_EXTRACTION_MODELS:
            try:
                raw = await complete(model, prompt)
            except Exception:
                logger.warning("entity_extraction_model_failed", model=model, exc_info=True)
                continue
            triples = _parse_triples(raw)
            span.set_attribute("extract.model", model)
            span.set_attribute("extract.triples", len(triples))
            return triples
        logger.error("entity_extraction_all_models_failed")
        return []

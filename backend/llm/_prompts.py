"""Shared prompt builders used by all LLM adapters.

Prompts are preserved verbatim from the original router/generation modules so routing vocabulary
and generation style remain unchanged after Phase 4.

**Prompt-caching structure (Phase 6, docs/09 §6 + Decision 9).** Each prompt is split into a
*stable prefix* (the routing rubric / the synthesis format contract — byte-identical on every
request) and a *variable suffix* (the query, retrieved context, doc/web availability — different
every call). Adapters place the stable prefix in the cache-eligible position (Anthropic ``system``
with ``cache_control``; OpenAI/Gemini as the leading prefix) and the variable suffix in the
trailing/user position. **Never** move per-request data into a stable-prefix builder — that nukes
the cache hit rate (caching skill, "prefix changes" sharp edge).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Single source of truth lives in llm.base; imported here only for annotations
    # (runtime import would be circular: base.py imports the builders from this module).
    from llm.base import Route


# ── Routing ───────────────────────────────────────────────────────────────────

# Stable, cacheable prefix: the classifier rubric. Contains NO per-request data — the query and
# the doc/web availability flags live in the variable suffix below so this text is byte-identical
# on every routing call (a prerequisite for an Anthropic/OpenAI/Gemini cache hit).
ROUTING_SYSTEM = """You are a routing classifier for a Retrieval-Augmented Generation system.

Classify the user query into EXACTLY ONE of these categories:

- RAG: Requires information that is likely to be found ONLY in the user's PRIVATE DOCUMENTS
(contracts, policies, internal reports, PDFs, local notes).
- WEB: Asks about GENERAL KNOWLEDGE, PUBLIC FACTS, DEFINITIONS, NEWS, PEOPLE, COMPANIES, OR TECHNOLOGY.
- DIRECT: Simple chat, opinions, greetings, or coding questions that do NOT require either
documents or the web (you can answer from general model knowledge alone).

IMPORTANT:
- If the question is about a programming language, framework, library, famous person, company,
or public concept, choose WEB (if web is allowed), otherwise DIRECT.
- ONLY choose RAG when the question clearly refers to "my document", "the PDF", "the contract",
"this report", or similar private content.
- NEVER choose RAG for generic trivia or public facts.

Respond with ONLY one word: RAG, WEB, or DIRECT."""


def routing_user(query: str, has_documents: bool, web_allowed: bool) -> str:
    """Variable per-request suffix for routing: the query + availability flags."""
    doc_status = "YES (user uploaded documents)" if has_documents else "NO"
    web_status = "ALLOWED" if web_allowed else "DISABLED"
    return (
        f'Query: "{query}"\n'
        f"Documents available: {doc_status}\n"
        f"Web search: {web_status}\n\n"
        "Respond with ONLY one word: RAG, WEB, or DIRECT."
    )


_LABEL_RE = re.compile(r"\b(RAG|WEB|DIRECT)\b")


def normalize_decision(text: str) -> Route:
    """Normalize a provider's routing response to one of RAG/WEB/DIRECT.

    Tolerant of decoration the model sometimes wraps around its one-word answer — markdown
    (``**WEB**``), code fences, quotes (``"WEB"``), or a prefix (``Answer: WEB``). The old raw
    ``startswith`` saw those as non-matching and silently collapsed to ``DIRECT`` (a *recognized*
    label, so the downstream defensive default never fired) — e.g. ``**WEB**`` with no docs skipped
    web search and hallucinated. Match the first recognized label as a WHOLE WORD; only fall back to
    ``DIRECT`` when no known label is present.
    """
    match = _LABEL_RE.search(text.upper())
    if match is None:
        return "DIRECT"
    label = match.group(1)
    if label == "RAG":
        return "RAG"
    if label == "WEB":
        return "WEB"
    return "DIRECT"


# ── Generation ────────────────────────────────────────────────────────────────

# Stable, cacheable per-route prefixes: the role + the answer-format contract. These never embed
# the query or retrieved context, so they are byte-identical across requests of the same route.
_RAG_SYSTEM = (
    "You are a helpful assistant answering questions about PRIVATE DOCUMENTS.\n"
    "Answer ONLY based on the document context provided in the user message. "
    "If the answer isn't in the context, say "
    '"I don\'t have that information in the uploaded documents." '
    "Format naturally, cite section/chunk numbers when possible."
)
_WEB_SYSTEM = (
    "You are a helpful assistant using WEB SEARCH RESULTS.\n"
    "Answer using ONLY the web results provided in the user message. Summarize key facts. "
    "If results don't answer the question, say "
    '"Web results don\'t contain this information." Be concise and factual.'
)
_DIRECT_SYSTEM = "You are a helpful AI assistant."


def generation_system(decision: str) -> str:
    """Stable, cacheable system prefix for the decided route (no per-request data)."""
    d = decision.upper()
    if "RAG" in d:
        return _RAG_SYSTEM
    if "WEB" in d:
        return _WEB_SYSTEM
    return _DIRECT_SYSTEM


def generation_user(decision: str, query: str, context: str) -> str:
    """Variable per-request user content: the retrieved context + the question."""
    d = decision.upper()
    if "RAG" in d:
        return (
            f"CONTEXT FROM USER DOCUMENTS:\n{context}\n\n"
            f"USER QUESTION: {query}\n\n"
            "Answer ONLY based on the document context above."
        )
    if "WEB" in d:
        return (
            f"WEB SEARCH RESULTS:\n{context}\n\n"
            f"USER QUESTION: {query}\n\n"
            "Answer using ONLY the web results above."
        )
    return f"USER: {query}\n\nAnswer naturally and helpfully."


def generation_system_user(decision: str, query: str, context: str) -> tuple[str, str]:
    """Split generation prompt into (stable system, variable user).

    Adapters that take one combined prompt (Gemini) join these as ``f"{system}\\n\\n{user}"``,
    which keeps the stable role/format contract leading so implicit prefix caching matches it.
    """
    return generation_system(decision), generation_user(decision, query, context)

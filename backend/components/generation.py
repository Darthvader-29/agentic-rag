"""Generation module: final-answer synthesis via the injected LLM provider.

Phase 4: the Gemini process-global and GoogleAPIError ladder are removed. Error mapping lives in
the provider adapter; neutral LLM errors bubble to app_exception_handler in exceptions.py.

Phase 6: synthesis is *rich* — the model is asked for Markdown prose plus zero or more fenced
``json`` component blocks from the fixed catalog (table / chart / citation / code / callout / media,
agents/schemas.py). The format contract is folded into the query string so it reaches the model
regardless of route, without touching the provider adapters in ``llm/``. ``synthesize`` is the
non-streaming entry (generate → parse_components); the streaming entry lives in the synthesis node,
which feeds ``provider.stream`` deltas to a ``ComponentStreamSplitter``. The legacy
``generate_final_response`` (plain answer, combined label) is preserved for existing callers.
"""

from __future__ import annotations

import structlog

from agents.schemas import parse_components
from components.retrieval import format_context
from llm.base import LLMProvider, Route

logger = structlog.get_logger(__name__)


# The stable synthesis format contract — a prompt-cacheable prefix (docs/09 Decision 9). It instructs
# rich Markdown plus an OPTIONAL fenced ```json component block from the trusted catalog. No
# model-authored executable markup is ever requested (no XSS; fully streamable). An invalid block is
# dropped downstream, never fatal.
SYNTHESIS_FORMAT_CONTRACT = """\
You are the synthesis step of an agentic RAG assistant. Write a clear, well-structured answer in \
GitHub-Flavored **Markdown** (headings, lists, **bold**, tables, and `inline code` where helpful).

When — and only when — structured data would help the reader, you MAY append one or more UI \
component blocks as fenced ```json code blocks. Each block must be a single JSON object whose \
"type" is one of the catalog below. Emit plain prose for everything else; do not wrap ordinary \
text in a component. Never invent component types or fields, and never emit HTML/script.

Component catalog:
- table:    {"type":"table","columns":["..."],"rows":[["...","..."]]}
- chart:    {"type":"chart","chart":"bar|line|pie","x":["..."],"series":[{"name":"...","y":[1,2]}]}
- citation: {"type":"citation","items":[{"label":"file · p.N","source_id":"...","snippet":"..."}]}
- code:     {"type":"code","language":"python","code":"..."}
- callout:  {"type":"callout","level":"info|warning|tip","text":"..."}
- media:    {"type":"media","items":[{"url":"https://...","alt":"..."}]}
"""


def build_synthesis_query(user_query: str) -> str:
    """Wrap the user question with the rich-output format contract.

    Passed as the provider's ``query`` so the format instruction reaches the model on every route
    (DIRECT included, where the provider's own prompt is just the question). The contract is a stable
    prefix → prompt-cacheable; the trailing question is the per-request tail.
    """
    return f"{SYNTHESIS_FORMAT_CONTRACT}\n\nUser question: {user_query}"


async def synthesize(
    provider: LLMProvider,
    query: str,
    context: str,
    decision: Route,
) -> tuple[str, list[dict]]:
    """Non-streaming rich synthesis: one ``generate`` call, then split prose / components.

    Returns ``(prose, components)`` where ``prose`` has any recognized component blocks stripped and
    ``components`` is the list of validated component dicts. Malformed/unknown blocks stay in the
    prose (never a 500), mirroring the defensive ``parse_components`` contract.
    """
    raw = await provider.generate(build_synthesis_query(query), context, decision)
    prose, components = parse_components(raw)
    logger.info(
        "synthesis_complete",
        decision=decision,
        prose_chars=len(prose),
        components=len(components),
    )
    return prose, components


async def generate_final_response(
    provider: LLMProvider,
    query: str,
    context: list[str],
    decision: Route,
) -> str:
    """Generate the final answer using the injected provider. Legacy linear-path helper."""
    answer = await provider.generate(query, format_context(context), decision)
    logger.info("generation_complete", decision=decision, response_chars=len(answer))
    return answer

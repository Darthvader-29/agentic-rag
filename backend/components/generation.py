"""Generation module: the rich-synthesis format contract + query builder.

Phase 4: the Gemini process-global and GoogleAPIError ladder are removed. Error mapping lives in
the provider adapter; neutral LLM errors bubble to app_exception_handler in exceptions.py.

Phase 6: synthesis is *rich* — the model is asked for Markdown prose plus zero or more fenced
``json`` component blocks from the fixed catalog (table / chart / citation / code / callout / media,
agents/schemas.py). The format contract is folded into the query string so it reaches the model
regardless of route, without touching the provider adapters in ``llm/``. The streaming synthesis
entry lives in the synthesis node, which feeds ``provider.stream`` deltas to a
``ComponentStreamSplitter`` and calls ``build_synthesis_query`` to wrap the user question.
"""

from __future__ import annotations

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

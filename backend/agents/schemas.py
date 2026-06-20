"""Pydantic schemas for the synthesis node's structured component output.

The synthesis node returns Markdown prose plus zero or more fenced ``json`` blocks, each
describing a UI component from a fixed catalog (docs/09_Phase6_Agentic_Architecture.md
Appendix C). Every block is validated against the discriminated union below; an invalid block is
**dropped** (the prose still renders) — never a 500, mirroring the defensive
``decide_combined_route`` pattern. The frontend renders rich UI from these trusted specs, so no
model-authored executable markup is ever emitted (no XSS, fully streamable).
"""

import json
import re
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator


def _is_safe_http_url(url: str | None) -> bool:
    """True only for an absolute http(s) URL (R06).

    Rejects javascript:/data:/blob:/relative — any of which would execute or mislead if rendered as
    an href/src. Defense-in-depth with the frontend's own allowlist (lib/url.ts).
    """
    if not url:
        return False
    try:
        return urlsplit(url).scheme.lower() in ("http", "https")
    except ValueError:
        return False


# ── Inner item models ────────────────────────────────────────────────────────


class ChartSeries(BaseModel):
    name: str
    y: list[float]


class CitationItem(BaseModel):
    label: str
    source_id: str
    snippet: str = ""
    # Optional, preserved for the UI: a source link-out and the Phase-7 retrieval-layer provenance
    # (vector|graph|web|memory). The frontend tolerantly narrows `layer` and guards `url`; dropping
    # them here (the old model omitted both) made clickable citations + provenance badges dead.
    url: str | None = None
    layer: str | None = None

    @field_validator("url")
    @classmethod
    def _disarm_unsafe_url(cls, v: str | None) -> str | None:
        # R06: disarm a non-http(s) url to None — keep the citation, drop only the unsafe link.
        return v if _is_safe_http_url(v) else None


class MediaItem(BaseModel):
    url: str
    alt: str = ""
    caption: str | None = None  # optional figcaption rendered under the media


# ── Component catalog ────────────────────────────────────────────────────────


class TableComponent(BaseModel):
    type: Literal["table"]
    columns: list[str]
    rows: list[list[Any]]
    caption: str | None = None  # optional <caption> the table renderer shows


class ChartComponent(BaseModel):
    type: Literal["chart"]
    chart: Literal["bar", "line", "pie"]
    x: list[Any]
    series: list[ChartSeries]
    title: str | None = None  # optional figure title the chart renderer shows


class CitationComponent(BaseModel):
    type: Literal["citation"]
    items: list[CitationItem]


class CodeComponent(BaseModel):
    type: Literal["code"]
    language: str = ""
    code: str


class CalloutComponent(BaseModel):
    type: Literal["callout"]
    level: Literal["info", "warning", "tip"] = "info"
    text: str
    title: str | None = None  # optional bold title above the callout text


class MediaComponent(BaseModel):
    type: Literal["media"]
    items: list[MediaItem]

    @field_validator("items")
    @classmethod
    def _drop_unsafe_items(cls, items: list[MediaItem]) -> list[MediaItem]:
        # R06: drop media whose url isn't http(s) (keep the safe ones; the frontend re-filters too).
        return [it for it in items if _is_safe_http_url(it.url)]


Component = Annotated[
    TableComponent
    | ChartComponent
    | CitationComponent
    | CodeComponent
    | CalloutComponent
    | MediaComponent,
    Field(discriminator="type"),
]

_ADAPTER: TypeAdapter[Any] = TypeAdapter(Component)

# A fenced ```json { ... } ``` block (non-greedy, dot matches newlines).
_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def validate_component(obj: Any) -> dict | None:
    """Validate a raw object against the component union; return a normalized dict, or None."""
    if not isinstance(obj, dict) or "type" not in obj:
        return None
    try:
        model = _ADAPTER.validate_python(obj)
    except ValidationError:
        return None
    # exclude_none so absent optionals (url/layer/caption/title) don't emit nulls — the wire shape
    # is unchanged when they aren't present, and the frontend treats absent ⇒ no badge/caption.
    return model.model_dump(exclude_none=True)


def parse_components(text: str) -> tuple[str, list[dict]]:
    """Split synthesis text into (prose without component blocks, [validated components]).

    Fenced ``json`` blocks that validate as a known component are removed from the prose and
    returned as component dicts; malformed or unrecognized blocks are left in the prose
    untouched. Used by the non-streaming/JSON path and tests; the streaming path emits each
    component as a whole ``component`` SSE event once its fence closes.
    """
    components: list[dict] = []

    def _replace(match: re.Match[str]) -> str:
        try:
            obj = json.loads(match.group(1))
        except json.JSONDecodeError:
            return match.group(0)  # leave malformed JSON in the prose
        validated = validate_component(obj)
        if validated is None:
            return match.group(0)
        components.append(validated)
        return ""  # strip the recognized component block from the prose

    prose = _FENCE_RE.sub(_replace, text)
    prose = re.sub(r"\n{3,}", "\n\n", prose).strip()  # collapse blank lines left behind
    return prose, components


# ── Incremental streaming splitter ───────────────────────────────────────────


_FENCE_OPEN = "```json"
_BACKTICK = "`"


class ComponentStreamSplitter:
    """Split a *streamed* synthesis answer into prose tokens and whole component blocks.

    Feed raw provider deltas via :meth:`feed`; it returns ``("token", str)`` events for prose and
    ``("component", dict)`` events for each completed, validated ```json block. Recognized
    component fences are never surfaced as prose; malformed or non-JSON fences (e.g. ```python)
    pass through verbatim as prose. Call :meth:`flush` once the stream ends to drain trailing
    text. This powers true token-by-token SSE while still emitting each component block whole — a
    half-open chart can't be rendered. See docs/09_Phase6_Agentic_Architecture.md §5.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._in_block = False

    def feed(self, delta: str) -> list[tuple[str, Any]]:
        self._buf += delta
        out: list[tuple[str, Any]] = []
        while self._buf:
            if self._in_block:
                close = self._buf.find("```")
                if close == -1:
                    break  # wait for the closing fence
                raw, self._buf = self._buf[:close], self._buf[close + 3 :]
                self._in_block = False
                comp = self._try_component(raw)
                if comp is not None:
                    out.append(("component", comp))
                else:
                    out.append(("token", _FENCE_OPEN + raw + "```"))  # not valid → verbatim prose
                continue
            tick = self._buf.find(_BACKTICK)
            if tick == -1:
                out.append(("token", self._buf))
                self._buf = ""
                break
            if tick > 0:
                out.append(("token", self._buf[:tick]))
                self._buf = self._buf[tick:]
            # buffer now starts with a backtick
            if len(self._buf) < len(_FENCE_OPEN):
                if _FENCE_OPEN.startswith(self._buf):
                    break  # possibly a partial opener — wait for more deltas
                out.append(("token", self._buf[:1]))  # lone/other backtick → prose
                self._buf = self._buf[1:]
                continue
            if self._buf.startswith(_FENCE_OPEN):
                self._buf = self._buf[len(_FENCE_OPEN) :]
                self._in_block = True
                continue
            out.append(("token", self._buf[:1]))  # ``` not followed by "json" → prose
            self._buf = self._buf[1:]
        return out

    def flush(self) -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        if self._in_block:  # unterminated block → emit verbatim as prose
            out.append(("token", _FENCE_OPEN + self._buf))
        elif self._buf:
            out.append(("token", self._buf))
        self._buf = ""
        self._in_block = False
        return out

    @staticmethod
    def _try_component(raw: str) -> dict | None:
        try:
            obj = json.loads(raw.strip())
        except json.JSONDecodeError:
            return None
        return validate_component(obj)

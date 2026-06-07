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

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

# ── Inner item models ────────────────────────────────────────────────────────


class ChartSeries(BaseModel):
    name: str
    y: list[float]


class CitationItem(BaseModel):
    label: str
    source_id: str
    snippet: str = ""


class MediaItem(BaseModel):
    url: str
    alt: str = ""


# ── Component catalog ────────────────────────────────────────────────────────


class TableComponent(BaseModel):
    type: Literal["table"]
    columns: list[str]
    rows: list[list[Any]]


class ChartComponent(BaseModel):
    type: Literal["chart"]
    chart: Literal["bar", "line", "pie"]
    x: list[Any]
    series: list[ChartSeries]


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


class MediaComponent(BaseModel):
    type: Literal["media"]
    items: list[MediaItem]


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
    return model.model_dump()


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

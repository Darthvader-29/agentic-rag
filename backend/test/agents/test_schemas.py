"""Contract tests for agents/schemas.py — the structured-output catalog.

These guard the foundation pieces the synthesis node and BE-2's SSE endpoint depend on:
``validate_component`` (valid → normalized dict, invalid → None), ``parse_components`` (strips
valid blocks, leaves malformed ones in the prose), and ``ComponentStreamSplitter`` fed awkward
chunks (a fence split across deltas) yields the right ``token`` / ``component`` events.
"""

from agents.schemas import (
    ComponentStreamSplitter,
    parse_components,
    validate_component,
)

# ── validate_component ────────────────────────────────────────────────────────


def test_validate_component_valid_table_returns_dict():
    obj = {"type": "table", "columns": ["A", "B"], "rows": [["1", "2"]]}
    out = validate_component(obj)
    assert out == {"type": "table", "columns": ["A", "B"], "rows": [["1", "2"]]}


def test_validate_component_valid_callout_fills_default_level():
    out = validate_component({"type": "callout", "text": "heads up"})
    assert out == {"type": "callout", "level": "info", "text": "heads up"}


def test_validate_component_preserves_optional_ui_fields():
    """B08: optional UI fields (citation url/layer, media caption, callout/chart title, table
    caption) must SURVIVE validation — the old models dropped them, killing link-outs + badges."""
    cit = validate_component(
        {
            "type": "citation",
            "items": [
                {
                    "label": "doc.pdf",
                    "source_id": "s1",
                    "url": "https://example.com/doc",
                    "layer": "vector",
                }
            ],
        }
    )
    assert cit is not None
    assert cit["items"][0]["url"] == "https://example.com/doc"
    assert cit["items"][0]["layer"] == "vector"

    media = validate_component(
        {"type": "media", "items": [{"url": "https://cdn/x.png", "caption": "Figure 1"}]}
    )
    assert media is not None
    assert media["items"][0]["caption"] == "Figure 1"

    callout = validate_component({"type": "callout", "text": "body", "title": "Heads up"})
    assert callout is not None and callout["title"] == "Heads up"

    chart = validate_component(
        {"type": "chart", "chart": "bar", "x": ["a"], "series": [{"name": "s", "y": [1.0]}],
         "title": "Quarterly"}
    )
    assert chart is not None and chart["title"] == "Quarterly"

    table = validate_component(
        {"type": "table", "columns": ["A"], "rows": [["1"]], "caption": "Metrics"}
    )
    assert table is not None and table["caption"] == "Metrics"


def test_validate_component_omits_absent_optional_fields():
    """Absent optionals stay omitted (exclude_none) — the wire shape is unchanged when unused."""
    out = validate_component({"type": "citation", "items": [{"label": "d", "source_id": "s"}]})
    assert out == {"type": "citation", "items": [{"label": "d", "source_id": "s", "snippet": ""}]}
    assert "url" not in out["items"][0] and "layer" not in out["items"][0]


def test_validate_component_unknown_type_returns_none():
    assert validate_component({"type": "banana", "text": "x"}) is None


def test_validate_component_missing_type_returns_none():
    assert validate_component({"columns": ["A"], "rows": []}) is None


def test_validate_component_wrong_shape_returns_none():
    # chart requires x + series; a bare type fails the union validation.
    assert validate_component({"type": "chart"}) is None


def test_validate_component_non_dict_returns_none():
    assert validate_component("not a dict") is None
    assert validate_component(None) is None


# ── parse_components ──────────────────────────────────────────────────────────


def test_parse_components_strips_valid_block_and_returns_it():
    text = (
        "Here is the table.\n\n"
        '```json\n{"type": "table", "columns": ["A"], "rows": [["1"]]}\n```\n\n'
        "That is all."
    )
    prose, comps = parse_components(text)
    assert "```json" not in prose
    assert "Here is the table." in prose
    assert "That is all." in prose
    assert comps == [{"type": "table", "columns": ["A"], "rows": [["1"]]}]


def test_parse_components_leaves_malformed_json_in_prose():
    text = 'Prefix.\n```json\n{"type": "table", "columns": [oops}\n```\nSuffix.'
    prose, comps = parse_components(text)
    assert comps == []
    assert "```json" in prose  # malformed block left untouched in the prose


def test_parse_components_leaves_unknown_component_in_prose():
    text = '```json\n{"type": "banana", "text": "x"}\n```'
    prose, comps = parse_components(text)
    assert comps == []
    assert "banana" in prose


def test_parse_components_no_blocks_returns_text_unchanged():
    prose, comps = parse_components("Just prose, no components.")
    assert prose == "Just prose, no components."
    assert comps == []


# ── ComponentStreamSplitter ───────────────────────────────────────────────────


def _drain(events):
    """Split a list of (kind, value) events into (joined_token_text, [components])."""
    tokens = "".join(v for k, v in events if k == "token")
    comps = [v for k, v in events if k == "component"]
    return tokens, comps


def test_splitter_plain_prose_passthrough():
    sp = ComponentStreamSplitter()
    events = sp.feed("Hello world")
    events += sp.flush()
    tokens, comps = _drain(events)
    assert tokens == "Hello world"
    assert comps == []


def test_splitter_emits_whole_component_when_fence_arrives_in_one_chunk():
    sp = ComponentStreamSplitter()
    events = sp.feed('Intro ```json\n{"type": "callout", "text": "hi"}\n``` outro')
    events += sp.flush()
    tokens, comps = _drain(events)
    assert comps == [{"type": "callout", "level": "info", "text": "hi"}]
    assert "Intro " in tokens
    assert "outro" in tokens
    assert "```json" not in tokens  # recognized fence never leaks as prose


def test_splitter_fence_split_across_deltas():
    """The opener, body, and closing fence each arrive in separate awkward deltas."""
    sp = ComponentStreamSplitter()
    events: list = []
    for delta in [
        "Here ",
        "``",
        "`js",
        "on\n",
        '{"type": "call',
        'out", "text": ',
        '"x"}',
        "\n``",
        "` done",
    ]:
        events += sp.feed(delta)
    events += sp.flush()
    tokens, comps = _drain(events)
    assert comps == [{"type": "callout", "level": "info", "text": "x"}]
    assert tokens.startswith("Here ")
    assert tokens.endswith(" done")
    assert "```json" not in tokens


def test_splitter_non_json_fence_passes_through_as_prose():
    """A ```python fence is not a component → it streams verbatim as prose."""
    sp = ComponentStreamSplitter()
    events = sp.feed("```python\nprint(1)\n```")
    events += sp.flush()
    tokens, comps = _drain(events)
    assert comps == []
    assert "print(1)" in tokens


def test_splitter_malformed_json_fence_passes_through_verbatim():
    sp = ComponentStreamSplitter()
    events = sp.feed("```json\n{not valid}\n```")
    events += sp.flush()
    tokens, comps = _drain(events)
    assert comps == []
    assert "```json" in tokens
    assert "{not valid}" in tokens


def test_splitter_unterminated_block_flushes_as_prose():
    sp = ComponentStreamSplitter()
    events = sp.feed('```json\n{"type": "table"')  # never closes
    events += sp.flush()
    tokens, comps = _drain(events)
    assert comps == []
    assert "```json" in tokens

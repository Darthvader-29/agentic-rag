"""B15: the routing normalizer tolerates decoration around the model's one-word label.

The old startswith() collapsed **WEB**, "WEB", ```WEB```, and "Answer: WEB" to DIRECT — a
recognized label, so the defensive default never fired and (no docs, web allowed) routed DIRECT,
skipping web search and hallucinating.
"""

import pytest

from llm._prompts import normalize_decision


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("WEB", "WEB"),
        ("RAG", "RAG"),
        ("DIRECT", "DIRECT"),
        ("web", "WEB"),  # case-insensitive
        ("**WEB**", "WEB"),  # markdown bold
        ("```WEB```", "WEB"),  # code fence
        ('"WEB"', "WEB"),  # quoted
        ("Answer: WEB", "WEB"),  # prefixed
        ("- WEB", "WEB"),  # bullet
        ("**RAG**", "RAG"),
        ("Answer: DIRECT", "DIRECT"),
        ("  WEB \n", "WEB"),  # surrounding whitespace
    ],
)
def test_normalize_decision_tolerates_decoration(reply, expected):
    assert normalize_decision(reply) == expected


@pytest.mark.parametrize("reply", ["", "I'm not sure", "banana", "WEBSITE", "DIRECTION"])
def test_normalize_decision_unknown_falls_back_to_direct(reply):
    # No recognized WHOLE-WORD label → DIRECT (and downstream's defensive default can still apply).
    assert normalize_decision(reply) == "DIRECT"

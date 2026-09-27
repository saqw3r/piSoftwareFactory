"""Brief rendering and verdict parsing."""

from __future__ import annotations

from pisoftwarefactory.pipeline.brief import render_brief
from pisoftwarefactory.pipeline.graph import Node
from pisoftwarefactory.pipeline.review import parse_verdict


def test_brief_contains_contract_and_feedback():
    node = Node(id="n009", title="t", task="do the thing", files=["a.py"], lane="L2")
    text = render_brief(node, lane="L2", main_branch="main", memories=["remember this"], gate_feedback="pytest failed")
    assert "1 element = 1 commit" in text
    assert "@a.py" in text
    assert "remember this" in text
    assert "pytest failed" in text
    assert "lane `L2`" in text


def test_parse_verdict_variants():
    assert parse_verdict("Verdict: clean") == "clean"
    assert parse_verdict("**Result:** ✅ **CLEAN** — done") == "clean"
    assert parse_verdict("nothing wrong except a nit\nVerdict: nits") == "nits"
    assert parse_verdict("no verdict here") == ""

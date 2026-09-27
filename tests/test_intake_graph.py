"""Intake heuristics (ADR-0004) and graph model (ADR-0007)."""

from __future__ import annotations

from pisoftwarefactory.config import FactoryConfig
from pisoftwarefactory.pipeline.graph import Graph, Node
from pisoftwarefactory.pipeline.intake import heuristic_intake, run_intake


def test_bug_classification():
    d = heuristic_intake("fix the login crash on empty password", FactoryConfig())
    assert d.kind == "bug" and d.provider == "heuristic"


def test_high_risk_detection():
    d = heuristic_intake("rotate the auth token secret", FactoryConfig())
    assert d.risk == "high"


def test_language_hint_from_extension():
    d = heuristic_intake("refactor main.rs parsing", FactoryConfig(languages=["python", "rust"]))
    assert d.language == "rust"


def test_run_intake_offline_uses_heuristic():
    d = run_intake("add a feature", FactoryConfig())
    assert d.provider == "heuristic"


def test_graph_ready_respects_blockers():
    g = Graph()
    a = g.add(Node(id="n001", title="a", task="a", status="done"))
    b = g.add(Node(id="n002", title="b", task="b", blockers=["n001"]))
    c = g.add(Node(id="n003", title="c", task="c", blockers=["n002"]))
    ready = g.ready()
    assert [n.id for n in ready] == ["n002"]
    b.status = "done"
    assert [n.id for n in g.ready()] == ["n003"]

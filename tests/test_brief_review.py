"""Brief rendering and verdict parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from pisoftwarefactory import gitcmd
from pisoftwarefactory.pipeline import review
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


def test_brief_demands_a_file_not_a_chat_answer():
    """Measured: a worker spent 950 s streaming its deliverable as chat text and
    was cut off by the budget with nothing written. The brief said where the work
    goes nowhere, so the model answered instead of producing the file."""
    node = Node(id="n010", title="architecture view, output should be .md",
                task="Generate me architecture view in C4 model. Output should be .md file")
    text = render_brief(node, lane="L1", main_branch="main")

    assert "The deliverable is a file you wrote and committed" in text
    assert "never an answer pasted into\n   chat" in text


def test_parse_verdict_variants():
    assert parse_verdict("Verdict: clean") == "clean"
    assert parse_verdict("**Result:** ✅ **CLEAN** — done") == "clean"
    assert parse_verdict("nothing wrong except a nit\nVerdict: nits") == "nits"
    assert parse_verdict("no verdict here") == ""


def _node() -> Node:
    return Node(id="n010", title="architecture view", task="write it", language="csharp", lane="L1")


def test_a_refused_diff_is_not_reported_as_an_empty_one(monkeypatch):
    """`sfactory review` must stop with git's words, not hand the reviewer nothing."""
    def refusing(root, *args, **_kw):
        raise gitcmd.GitRefused("git diff main...HEAD failed (rc=128): fatal: detected dubious ownership")

    monkeypatch.setattr(review.gitcmd, "run", refusing)
    with pytest.raises(gitcmd.GitRefused, match="dubious ownership"):
        review.lane_diff(Path("."), _node(), "main")


def test_an_uncommitted_deliverable_still_reaches_the_reviewer(monkeypatch, tmp_path):
    """The written file is the work; `git diff base...HEAD` alone would hide it."""
    (tmp_path / "architecture.md").write_text("# C4\n\nContext diagram\n" + "x\n" * 200, encoding="utf-8")
    (tmp_path / ".factory" / "briefs").mkdir(parents=True)
    (tmp_path / ".factory" / "briefs" / "n010.md").write_text("the brief", encoding="utf-8")

    def fake_git(_root, *args, **_kw):
        if args[0] == "diff":  # nothing committed yet
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        return type("R", (), {"returncode": 0,
                              "stdout": "?? .factory/briefs/n010.md\n?? architecture.md\n",
                              "stderr": ""})()

    monkeypatch.setattr(review.gitcmd, "run", fake_git)
    diff = review.lane_diff(tmp_path, _node(), "main")

    assert "--- new file: architecture.md" in diff
    assert "… truncated …" in diff  # context discipline (ADR-0010), not silence
    assert "briefs/n010.md" not in diff  # the worker's own brief is not the change


def test_a_committed_change_is_not_duplicated_as_a_new_file(monkeypatch, tmp_path):
    def fake_git(_root, *args, **_kw):
        if args[0] == "diff":
            return type("R", (), {"returncode": 0, "stdout": "diff --git a/arch.md b/arch.md\n+hi\n",
                                  "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(review.gitcmd, "run", fake_git)
    diff = review.lane_diff(tmp_path, _node(), "main")
    assert "--- new file" not in diff


def test_the_reviewer_streams_instead_of_looking_hung(monkeypatch, tmp_path):
    """Live: `sfactory review n002` printed one line, then four minutes of nothing.

    The reviewer ran `subprocess.run(capture_output=True)`, so every event the
    agent emitted stayed in a pipe until the turn ended.
    """
    from pisoftwarefactory.config import FactoryConfig
    from pisoftwarefactory.integrations import pi_agent

    def fake_run_reviewer(prompt, config, cwd, timeout_min=15, log=None):
        assert log is not None, "the reviewer must get the operator's log"
        log("    │ 12s assistant said 0 chars (2 tool call(s): read)")
        return pi_agent.PiRun(returncode=0, final_text="Verdict: clean", duration_s=12.0)

    monkeypatch.setattr(review, "run_reviewer", fake_run_reviewer)
    monkeypatch.setattr(review, "lane_diff", lambda *a, **k: "diff body")
    msgs: list[str] = []
    node = review.review_node(tmp_path, _node(), FactoryConfig(), cwd=tmp_path, log=msgs.append)

    assert node.verdict == "clean"
    text = "\n".join(msgs)
    assert "reviewer turn 1" in text and "9 chars of diff" in text
    assert "assistant said 0 chars" in text


def test_a_review_without_a_verdict_is_not_a_successful_command(monkeypatch, tmp_path):
    """It printed `review n010: no verdict parsed` and exited 0 — an unreviewed
    change that `sfactory release` would ship as if a reviewer had cleared it."""
    from typer.testing import CliRunner

    from pisoftwarefactory.cli import app
    from pisoftwarefactory.pipeline.graph import Graph, Node, save_graph
    from pisoftwarefactory.scaffold import run_init

    (tmp_path / "pyproject.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")
    run_init(target=tmp_path, auto=True, langs="python", force=False, log=print)
    graph = Graph()
    graph.add(Node(id="n010", title="the thing", task="do it", status="done", lane="L1"))
    save_graph(tmp_path, graph)
    monkeypatch.setattr("pisoftwarefactory.cli._review_or_die",
                        lambda root, node, config, wt: None)

    result = CliRunner().invoke(app, ["review", "n010", "--root", str(tmp_path)])

    assert result.exit_code == 1
    assert "unreviewed" in result.output

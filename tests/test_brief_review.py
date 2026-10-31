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

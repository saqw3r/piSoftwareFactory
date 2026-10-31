"""Dispatch must not report success for work that never happened.

Live incident: a worker died on "No API key found for llamacpp" (rc=1, 0
tokens), the gates ran over an unchanged lane and passed, and `sfactory run`
printed `done … verdict=n/a`. These tests pin the two guards that close that
hole, plus the command shape each harness needs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pisoftwarefactory.config import FactoryConfig
from pisoftwarefactory.integrations import pi_agent
from pisoftwarefactory.pipeline import dispatch
from pisoftwarefactory.pipeline.graph import Node


def _config(harness: str = "pi") -> FactoryConfig:
    return FactoryConfig(harness={"name": harness})


def _node() -> Node:
    return Node(id="n001", title="do the thing", task="do the thing", kind="feature",
                  risk="standard", language="python")


@pytest.mark.parametrize(
    "harness,expected",
    [
        # pi needs the provider spelled out and the key on the command line.
        ("pi", "--provider llamacpp --model qwen3.5-9b --api-key none"),
        # omp selects provider/id and takes its key from its own registry.
        ("omp", "--model llamacpp/qwen3.5-9b"),
    ],
)
def test_worker_command_matches_the_chosen_harness(monkeypatch, harness, expected):
    monkeypatch.setattr(pi_agent, "installed", lambda name: True)
    monkeypatch.setattr(pi_agent, "binary", lambda name: name)
    cmd = " ".join(pi_agent._base_command(_config(harness), "json"))
    assert cmd.startswith(f"{harness} --no-session ")
    assert expected in cmd
    assert cmd.endswith("--mode json")


def test_missing_harness_names_the_switch_command(monkeypatch):
    monkeypatch.setattr(pi_agent, "installed", lambda name: False)
    monkeypatch.setattr(pi_agent, "binary", lambda name: name)
    with pytest.raises(pi_agent.PiError, match="sfactory backend --harness omp"):
        pi_agent._base_command(_config("pi"), "json")


def _patch_dispatch(monkeypatch, tmp_path, run_worker_result):
    """Isolate dispatch_node: no git, no gates, no hindsight — just the decision."""
    monkeypatch.setattr(dispatch, "ensure_lane", lambda root, config, lane: tmp_path)
    monkeypatch.setattr(dispatch, "_memories_for", lambda root, config, node: [])
    monkeypatch.setattr(dispatch, "write_brief", lambda dirpath, node, brief: Path("n001.md"))
    monkeypatch.setattr(dispatch, "run_worker", lambda brief, config, cwd: run_worker_result)
    monkeypatch.setattr(dispatch, "hindsight", type("H", (), {"recall": staticmethod(lambda *a, **k: []),
                                                              "retain": staticmethod(lambda *a, **k: True)})())
    gates_called: list[bool] = []

    def _gates(*a, **k):
        gates_called.append(True)
        return []

    monkeypatch.setattr(dispatch, "run_gates", _gates)
    return gates_called


def test_dead_worker_is_blocked_with_its_own_error_and_never_reaches_gates(monkeypatch, tmp_path):
    dead = pi_agent.PiRun(returncode=1, final_text="No API key found for llamacpp.\nUse /login")
    gates_called = _patch_dispatch(monkeypatch, tmp_path, dead)
    msgs: list[str] = []
    result = dispatch.dispatch_node(tmp_path, _node(), _config(), log=msgs.append)

    assert gates_called == []
    assert result.node.status == "blocked"
    text = "\n".join(msgs)
    assert "the agent died before the model answered" in text
    assert "No API key found for llamacpp" in text  # the cause, inline — no log file to open
    assert "fix:" in text and "/login" in text


def test_clean_worker_with_an_empty_lane_is_not_done(monkeypatch, tmp_path):
    from pisoftwarefactory.gates import ProfileResult, StepResult

    ok = pi_agent.PiRun(returncode=0, input_tokens=10, output_tokens=5)
    _patch_dispatch(monkeypatch, tmp_path, ok)
    passing = [ProfileResult(language="python", steps=[StepResult(name="detect", status="passed")])]
    monkeypatch.setattr(dispatch, "run_gates", lambda wt, config, **k: passing)
    monkeypatch.setattr(dispatch, "_lane_produced_change", lambda wt, base: False)
    msgs: list[str] = []
    result = dispatch.dispatch_node(tmp_path, _node(), _config(), log=msgs.append)

    assert result.node.status == "blocked"
    assert any("no commit and no edit" in m for m in msgs)


def test_lane_change_is_measured_by_commits_or_edits(monkeypatch, tmp_path):
    def fake_git(_root, *args, **_k):
        return type("R", (), {"stdout": {"rev-list": "0\n", "status": ""}[args[0]]})()

    monkeypatch.setattr(dispatch, "_git", fake_git)
    assert dispatch._lane_produced_change(tmp_path, "main") is False

    monkeypatch.setattr(dispatch, "_git", lambda _root, cmd, *a, **k: type("R", (), {"stdout": "1\n"})())
    assert dispatch._lane_produced_change(tmp_path, "main") is True

    # No commit, but the agent left edits behind — still work worth reviewing.
    monkeypatch.setattr(dispatch, "_git", lambda _root, cmd, *a, **k: type(
        "R", (), {"stdout": "0\n" if cmd == "rev-list" else " M file.cs\n"})())
    assert dispatch._lane_produced_change(tmp_path, "main") is True

"""Dispatch must not report success for work that never happened.

Live incident: a worker died on "No API key found for llamacpp" (rc=1, 0
tokens), the gates ran over an unchanged lane and passed, and `sfactory run`
printed `done … verdict=n/a`. These tests pin the two guards that close that
hole, plus the command shape each harness needs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pisoftwarefactory import gitcmd
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
    monkeypatch.setattr(dispatch, "run_worker", lambda brief, config, cwd, log=None: run_worker_result)
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
    # …and the reason survives the scrollback, for `sfactory status` later.
    assert "No API key found for llamacpp" in result.node.evidence
    assert "No API key found for llamacpp" in result.evidence


def test_a_real_change_with_passing_gates_is_done_and_holds_no_reason(monkeypatch, tmp_path):
    from pisoftwarefactory.gates import ProfileResult, StepResult

    ok = pi_agent.PiRun(returncode=0, input_tokens=10, output_tokens=5)
    _patch_dispatch(monkeypatch, tmp_path, ok)
    passing = [ProfileResult(language="python", steps=[StepResult(name="detect", status="passed")])]
    monkeypatch.setattr(dispatch, "run_gates", lambda wt, config, **k: passing)
    monkeypatch.setattr(dispatch, "_lane_state",
                        lambda wt, base: dispatch.LaneState(edits=("thing.py",), committed=True))
    result = dispatch.dispatch_node(tmp_path, _node(), _config(), log=lambda _m: None)

    assert result.node.status == "done"
    assert (result.node.evidence, result.evidence) == ("", "")


def test_an_uncommitted_deliverable_is_not_done_because_release_merges_commits(monkeypatch, tmp_path):
    """The file exists, the gates pass, the reviewer sees it — and it still gets dropped.

    ``release`` merges the lane *branch*, so work the agent never committed vanishes
    from the merge with no error. `done` has to mean "this survives a release", not
    "something is sitting on disk".
    """
    from pisoftwarefactory.gates import ProfileResult, StepResult

    ok = pi_agent.PiRun(returncode=0, input_tokens=10, output_tokens=5)
    _patch_dispatch(monkeypatch, tmp_path, ok)
    passing = [ProfileResult(language="python", steps=[StepResult(name="detect", status="passed")])]
    monkeypatch.setattr(dispatch, "run_gates", lambda wt, config, **k: passing)
    monkeypatch.setattr(dispatch, "_lane_state",
                        lambda wt, base: dispatch.LaneState(edits=("ARCHITECTURE.md",)))
    msgs: list[str] = []
    result = dispatch.dispatch_node(tmp_path, _node(), _config(), log=msgs.append)

    assert result.node.status == "blocked"
    text = "\n".join(msgs)
    assert "committed none of them" in text
    assert text.count("ARCHITECTURE.md") >= 2  # the reason, and the command that saves it
    assert f'git -C "{tmp_path}" add --' in text
    assert 'commit -m "n001: do the thing"' in text
    assert "ARCHITECTURE.md" in result.node.evidence


def test_clean_worker_with_an_empty_lane_is_not_done(monkeypatch, tmp_path):
    from pisoftwarefactory.gates import ProfileResult, StepResult

    ok = pi_agent.PiRun(returncode=0, input_tokens=10, output_tokens=5)
    _patch_dispatch(monkeypatch, tmp_path, ok)
    passing = [ProfileResult(language="python", steps=[StepResult(name="detect", status="passed")])]
    monkeypatch.setattr(dispatch, "run_gates", lambda wt, config, **k: passing)
    monkeypatch.setattr(dispatch, "_lane_state", lambda wt, base: dispatch.LaneState())
    msgs: list[str] = []
    result = dispatch.dispatch_node(tmp_path, _node(), _config(), log=msgs.append)

    assert result.node.status == "blocked"
    assert any("no commit and no edit" in m for m in msgs)


def test_silent_model_is_named_instead_of_left_mystery(monkeypatch, tmp_path):
    """rc=0, empty lane, no text at all: say which model produced nothing."""
    from pisoftwarefactory.gates import ProfileResult, StepResult

    ok = pi_agent.PiRun(returncode=0)
    _patch_dispatch(monkeypatch, tmp_path, ok)
    passing = [ProfileResult(language="python", steps=[StepResult(name="detect", status="passed")])]
    monkeypatch.setattr(dispatch, "run_gates", lambda wt, config, **k: passing)
    monkeypatch.setattr(dispatch, "_lane_state", lambda wt, base: dispatch.LaneState())
    msgs: list[str] = []
    config = FactoryConfig(harness={"name": "pi"}, backend={"model": "tiny-local-model"})
    dispatch.dispatch_node(tmp_path, _node(), config, log=msgs.append)

    text = "\n".join(msgs)
    assert "empty message" in text
    assert "tiny-local-model" in text
    assert "sfactory backend --model" in text


def test_refused_git_inspection_blocks_without_claiming_an_empty_lane(monkeypatch, tmp_path):
    """A git that wouldn't run proves nothing; the node must not be called done."""
    from pisoftwarefactory.gates import ProfileResult, StepResult

    ok = pi_agent.PiRun(returncode=0)
    _patch_dispatch(monkeypatch, tmp_path, ok)
    passing = [ProfileResult(language="python", steps=[StepResult(name="detect", status="passed")])]
    monkeypatch.setattr(dispatch, "run_gates", lambda wt, config, **k: passing)
    refusal = "fatal: detected dubious ownership in repository at 'Z:/work/L1'"
    monkeypatch.setattr(dispatch, "_lane_state", lambda wt, base: dispatch.LaneState(refusal=refusal))
    msgs: list[str] = []
    result = dispatch.dispatch_node(tmp_path, _node(), _config(), log=msgs.append)

    assert result.node.status == "blocked"
    assert "dubious ownership" in "\n".join(msgs)
    assert "no commit and no edit" not in "\n".join(msgs)  # we never proved it was empty


def test_lane_change_is_measured_by_commits_or_edits(monkeypatch, tmp_path):
    def fake_git(_root, cmd, *a, **_k):
        return type("R", (), {"returncode": 0,
                              "stdout": {"rev-list": "0\n", "status": ""}[cmd]})()

    monkeypatch.setattr(dispatch, "_git", fake_git)
    state = dispatch._lane_state(tmp_path, "main")
    assert (state.produced, state.committed, state.refusal) == (False, False, "")

    monkeypatch.setattr(dispatch, "_git", lambda _root, cmd, *a, **k: type(
        "R", (), {"returncode": 0, "stdout": "1\n"})())
    state = dispatch._lane_state(tmp_path, "main")
    assert (state.produced, state.committed) == (True, True)

    # No commit, but the agent left edits behind — reviewable, not releasable.
    monkeypatch.setattr(dispatch, "_git", lambda _root, cmd, *a, **k: type(
        "R", (), {"returncode": 0, "stdout": "0\n" if cmd == "rev-list" else " M file.cs\n"})())
    state = dispatch._lane_state(tmp_path, "main")
    assert (state.produced, state.committed, state.edits) == (True, False, ("file.cs",))


def test_the_brief_in_the_lane_is_not_counted_as_work(monkeypatch, tmp_path):
    """The worker is handed .factory/briefs/<node>.md inside its own lane.

    Untracked, always present — counting it would bless an untouched lane as a
    change and put the false `done` right back.
    """
    monkeypatch.setattr(dispatch, "_git", lambda _root, cmd, *a, **k: type(
        "R", (), {"returncode": 0,
                  "stdout": "0\n" if cmd == "rev-list" else "?? .factory/briefs/n004.md\n"})())
    assert dispatch._lane_state(tmp_path, "main").produced is False


def test_git_refusal_reports_its_own_cause(monkeypatch, tmp_path):
    def refusing(_root, *a, **_k):
        raise gitcmd.GitRefused("git rev-list failed (rc=128): fatal: detected dubious ownership")

    monkeypatch.setattr(dispatch, "_git", refusing)
    state = dispatch._lane_state(tmp_path, "main")
    assert state.produced is False
    assert "dubious ownership" in state.refusal

"""Live progress: an agent run must never look like a hang.

A local GGUF spends minutes reading pi's ~47k-token system prompt before the
first event lands. With nothing on the terminal, the operator cannot tell
"working" from "wedged" — and `sfactory` is meant to be run on machines nobody
is watching. So events stream by, a heartbeat says when the stream goes quiet,
and running out of time is reported as a budget problem, not a mystery.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest

from pisoftwarefactory.config import FactoryConfig
from pisoftwarefactory.integrations import pi_agent
from pisoftwarefactory.integrations.pi_agent import _describe, stream_agent
from pisoftwarefactory.pipeline import dispatch
from pisoftwarefactory.pipeline.graph import Node


class FakeProc:
    """Stands in for Popen: emits *lines* after a delay, then reports rc."""

    def __init__(self, lines=(), stderr_text="", delay=0.0, returncode=0):
        self._lines = list(lines)
        self._delay = delay
        self.stderr = io.StringIO(stderr_text)
        self.returncode = returncode
        self.killed = False

    @property
    def stdout(self):
        if self._delay:
            time.sleep(self._delay)
        return iter(self._lines)

    def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True


def _install(monkeypatch, proc):
    monkeypatch.setattr(pi_agent, "_POPEN", lambda *a, **k: proc)
    return proc


def event(**fields) -> str:
    return json.dumps(fields) + "\n"


def test_each_streamed_event_becomes_one_progress_line(monkeypatch):
    _install(monkeypatch, FakeProc([
        event(type="session", version=3),
        event(type="agent_start"),
        event(type="turn_start"),
        event(type="message_start", message={"role": "user", "content": "do the thing"}),
        event(type="message_end", message={
            "role": "assistant",
            "content": [{"type": "text", "text": "reading the solution"},
                        {"type": "toolCall", "name": "read", "arguments": {}}],
            "usage": {"input": 47000, "output": 96, "totalTokens": 47096},
        }),
        event(type="tool_execution_end", name="read"),
        event(type="agent_end"),
    ]))
    msgs: list[str] = []
    rc, out, _err = stream_agent(["pi"], Path("."), 30, "qwen3.5-9b", log=msgs.append)

    assert rc == 0
    assert "assistant said 20 chars (1 tool call(s): read), 47096 tok" in "\n".join(msgs)
    assert "tool execution end: read" in "\n".join(msgs)
    assert not any("session" in m for m in msgs)  # bookkeeping events stay silent
    assert out.count("\n") == 7


def test_quiet_stream_says_what_it_is_waiting_for(monkeypatch):
    proc = _install(monkeypatch, FakeProc([], delay=5))
    msgs: list[str] = []
    rc, _out, err = stream_agent(["pi"], Path("."), 1, "qwen3.5-9b", log=msgs.append, quiet_s=0.1)

    assert rc == 124
    assert proc.killed
    assert "timed out after 1s waiting for qwen3.5-9b" in err
    assert any("with no event" in m and "qwen3.5-9b" in m for m in msgs)


def test_plain_text_answers_are_echoed_too(monkeypatch):
    """`sfactory backend` verifies with --print: the answer is not JSON."""
    _install(monkeypatch, FakeProc(["OK\n"]))
    msgs: list[str] = []
    rc, out, _err = stream_agent(["pi"], None, 30, "pi", log=msgs.append, echo_text=True)

    assert (rc, out.strip()) == (0, "OK")
    assert "│ OK" in msgs[0]


def test_partial_updates_are_folded_into_one_writing_line(monkeypatch):
    """24 partial chunks must not become 24 lines.

    Measured live: pi emits a `message_update` two or three times a second while
    the model writes, so the terminal filled with identical text and the operator
    could read nothing in it. Writing gets one line per cadence, carrying the count.
    """
    monkeypatch.setattr(pi_agent, "_WRITE_REPORT_SEC", -1.0)
    _install(monkeypatch, FakeProc([
        event(type="message_update", message={"content": [{"type": "text", "text": "he"}]}),
        event(type="message_update", message={"content": [{"type": "text", "text": "hello"}]}),
        event(type="message_update", message={"content": [{"type": "text", "text": "hello w"}]}),
        event(type="agent_end"),
    ]))
    msgs: list[str] = []
    stream_agent(["pi"], None, 30, "qwen3.5-9b", log=msgs.append)

    assert sum("is writing" in m for m in msgs) == 3
    assert "3 partial update(s)" in msgs[-2]
    assert not any(m.strip().endswith("message update") for m in msgs)


def test_describe_survives_an_unknown_event():
    assert "weird thing" in _describe({"type": "weird_thing"}, 12.0)


def test_tool_calls_are_named_not_just_counted():
    """Measured live: a worker made 26 tool calls and the terminal never said which.

    "3 tool call(s)" cannot answer the only question that matters while the lane
    stays empty — is it writing the deliverable, or reading the repo again?
    """
    line = _describe({"type": "message_end", "message": {
        "role": "assistant",
        "content": [{"type": "toolCall", "name": "read"}, {"type": "toolCall", "name": "write"},
                    {"type": "toolCall", "name": "read"}],
    }}, 480.0)
    assert "3 tool call(s): read, write" in line

    # A schema that carries no names still reports the count it always did.
    assert "1 tool call(s)" in _describe({"type": "message_end", "message": {
        "role": "assistant", "content": [{"type": "toolCall"}]}}, 10.0)


def test_worker_timeout_blocks_with_a_budget_fix(monkeypatch, tmp_path):
    """The operator gets the cause and the knob, not an unexplained rc=124."""
    config = FactoryConfig(harness={"name": "pi"}, backend={"model": "qwen3.5-9b",
                                                            "base_url": "http://127.0.0.1:8080/v1"})
    run = pi_agent.PiRun(returncode=124, final_text="timed out after 40 min waiting for qwen3.5-9b")
    monkeypatch.setattr(dispatch, "ensure_lane", lambda root, config, lane: tmp_path)
    monkeypatch.setattr(dispatch, "_memories_for", lambda root, config, node: [])
    monkeypatch.setattr(dispatch, "write_brief", lambda dirpath, node, brief: Path("n001.md"))
    monkeypatch.setattr(dispatch, "run_worker", lambda brief, config, cwd, log=None: run)
    monkeypatch.setattr(dispatch, "hindsight", type("H", (), {"recall": staticmethod(lambda *a, **k: []),
                                                              "retain": staticmethod(lambda *a, **k: True)})())
    monkeypatch.setattr(dispatch, "_lane_state", lambda wt, base: dispatch.LaneState())
    msgs: list[str] = []
    node = Node(id="n001", title="t", task="t", kind="feature", risk="standard", language="csharp")
    result = dispatch.dispatch_node(tmp_path, node, config, log=msgs.append)

    assert result.node.status == "blocked"
    text = "\n".join(msgs)
    assert "worker_timeout_min" in text  # the knob to turn
    assert "qwen3.5-9b" in result.node.evidence


@pytest.mark.parametrize("quiet_s", [0.05, 0.5])
def test_heartbeat_repeats_while_the_model_is_silent(monkeypatch, quiet_s):
    _install(monkeypatch, FakeProc([], delay=0.6))
    msgs: list[str] = []
    stream_agent(["pi"], None, 2, "slow-model", log=msgs.append, quiet_s=quiet_s)
    assert sum("with no event" in m for m in msgs) >= 1

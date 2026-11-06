"""pi event parsing against the documented schema (docs/json.md) — no live runs."""

from __future__ import annotations

import json

from pisoftwarefactory.integrations.pi_agent import _parse_events


def json_line(event: dict) -> str:
    return json.dumps(event)

STREAM = "\n".join(
    [
        '{"type":"session","version":3,"id":"x","cwd":"."}',
        '{"type":"agent_start"}',
        '{"type":"turn_start"}',
        '{"type":"message_start","message":{"role":"user","content":"hi"}}',
        '{"type":"message_update","usage":{"input":100,"output":5,"cacheRead":0,"cacheWrite":0,"totalTokens":105}}',
        '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"All done, verdict inside."}],"stopReason":"end"}}',
        '{"type":"turn_end"}',
        '{"type":"agent_end","messages":[]}',
    ]
)


def test_parse_tokens_and_final_text():
    run = _parse_events(STREAM)
    assert run.input_tokens == 100 and run.output_tokens == 5
    assert run.final_text == "All done, verdict inside."


def test_parse_ignores_noise():
    run = _parse_events("not json\n{broken\n" + STREAM)
    assert run.final_text == "All done, verdict inside."


def test_tokens_come_from_the_message_where_pi_puts_them():
    """Live streams nest usage inside the message; reading only the event root
    printed `~0in/0out tok` for runs that had actually spent tokens."""
    real = json_line({
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "wrote architecture.md"}],
            "usage": {"input": 8123, "output": 402, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 8525},
            "stopReason": "stop",
        },
    })
    run = _parse_events(json_line({"type": "agent_start"}) + "\n" + real)
    assert (run.input_tokens, run.output_tokens) == (8123, 402)
    assert run.final_text == "wrote architecture.md"


def test_an_empty_assistant_message_is_reported_as_no_text():
    """The incident: rc=0, 125 KB of events, and the assistant said nothing."""
    run = _parse_events(json_line({
        "type": "message_end",
        "message": {"role": "assistant", "content": [], "usage": {"input": 0, "output": 0}},
    }))
    assert run.final_text == ""
    assert (run.input_tokens, run.output_tokens) == (0, 0)


def test_zcode_json_schema_captured_live():
    """zcode's --json output, verbatim shape from a real verify run (2026-11-05):
    response + usage with real token counts — no more zero accounting."""
    from pisoftwarefactory.integrations.pi_agent import PiRun, _zcode_parse

    raw = json.dumps({
        "sessionId": "sess_9894510e",
        "turnId": "turn_0698cd30",
        "response": "OK",
        "usage": {"source": "provider", "modelRequestCount": 1, "inputTokens": 18249,
                  "outputTokens": 2, "totalTokens": 18251, "cacheReadTokens": 18248,
                  "cacheWriteTokens": 0, "reasoningTokens": 0},
        "eventCount": 11,
        "projection": {"status": "idle", "turnCount": 1, "totalTokenCount": 18251,
                       "contextUsed": 18251, "contextWindow": 200000},
    })
    run = PiRun(returncode=0)
    _zcode_parse(raw, run)
    assert run.final_text == "OK"
    assert (run.input_tokens, run.output_tokens) == (18249, 2)


def test_zcode_plain_text_and_garbage_fall_back():
    from pisoftwarefactory.integrations.pi_agent import PiRun, _zcode_parse

    run = PiRun(returncode=0)
    _zcode_parse("plain answer, no json", run)
    assert run.final_text == "plain answer, no json"
    _zcode_parse("{not json", run)
    assert run.final_text == "{not json"

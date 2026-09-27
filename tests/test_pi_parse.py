"""pi event parsing against the documented schema (docs/json.md) — no live runs."""

from __future__ import annotations

from pisoftwarefactory.integrations.pi_agent import _parse_events

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

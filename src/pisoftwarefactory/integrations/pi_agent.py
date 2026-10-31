"""coding-agent wrapper (ADR-0002/ADR-0017): pi or omp, one JSONL reader.

Workers run ``<agent> --mode json`` (JSONL events → orchestrator parses progress
and token usage); reviewers run ``<agent> --print --tools read,grep,find,ls``
(fresh-context, read-only). Events are parsed defensively: the orchestrator
never depends on the agent's internal event schema beyond ``type`` and ``usage``.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..config import FactoryConfig
from ..harnesses import binary, installed, selectable_model_id


class PiError(RuntimeError):
    pass


# Test seam: `stream_agent` spawns through this, so progress and timeouts can be
# exercised without a real coding agent on PATH.
_POPEN = subprocess.Popen


@dataclass
class PiRun:
    returncode: int
    final_text: str = ""
    events: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _tokens_from(usage: dict) -> tuple[int, int]:
    """Best-effort token extraction from any usage-shaped dict.

    pi's schema (docs/json.md): ``{"input":100,"output":1,"cacheRead":0,
    "cacheWrite":0,"totalTokens":101,...}``; OpenAI-style keys are accepted
    as a fallback.
    """
    inp = usage.get("input") or usage.get("input_tokens") or usage.get("inputTokens") or usage.get("prompt_tokens") or 0
    out = usage.get("output") or usage.get("output_tokens") or usage.get("outputTokens") or usage.get("completion_tokens") or 0
    total = usage.get("totalTokens") or usage.get("total_tokens") or 0
    inp = inp or (total - out if total else 0)
    return int(inp), int(out)


def _usage_of(event: dict) -> dict | None:
    """pi nests token counts inside the message (``event.message.usage``); older
    shapes carried them at the event root. One source per event, never both."""
    for candidate in (event.get("usage"), (event.get("message") or {}).get("usage")):
        if isinstance(candidate, dict):
            return candidate
    return None


def _content_text(content: object) -> str:
    """pi message.content is a string or a list of blocks ({type:'text',text:...})."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def _parse_events(stdout: str) -> PiRun:
    run = PiRun(returncode=0)
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        run.events.append(event)

        usage = _usage_of(event)
        if usage:
            inp, out = _tokens_from(usage)
            run.input_tokens += inp
            run.output_tokens += out

        etype = str(event.get("type") or "")
        if etype == "message_end":
            message = event.get("message") or {}
            if message.get("role") == "assistant":
                text = _content_text(message.get("content"))
                if text:
                    run.final_text = text
    return run


def _base_command(config: FactoryConfig, mode: str) -> list[str]:
    """Build the agent command line for the configured harness (ADR-0017).

    Worker (headless JSONL stream): ``--mode json``. Reviewer (one-shot,
    read-only): the ``--print`` flag — print is a flag in pi, not a mode
    (``--mode`` accepts only text|json|rpc).

    Where the harnesses genuinely differ: pi needs ``--provider`` plus the key on
    the command line (for a custom endpoint it ignores models.json's apiKey and
    the provider env var, and a local llama.cpp wants the mocked ``"none"``),
    while omp selects ``provider/id`` and reads its key from its own registry.
    """
    name = config.harness.name
    if not installed(name):
        other = "omp" if name == "pi" else "pi"
        raise PiError(
            f"{name} not found on PATH — install it, or switch with `sfactory backend --harness {other}`"
        )
    cmd = [binary(name), "--no-session"]
    if name == "omp":
        cmd += ["--model", selectable_model_id(name, config.backend)]
    else:
        cmd += ["--provider", config.backend.provider, "--model", config.backend.model,
                "--api-key", config.backend.resolve_api_key()]
    if mode == "json":
        cmd += ["--mode", "json"]
    else:
        cmd += ["--print", "--tools", ",".join(config.harness.reviewer_tools)]
    return cmd


_SILENT_EVENTS = frozenset({"session", "message_start", "turn_start", "entry_appended", "agent_settled"})


def _describe(event: dict, elapsed_s: float) -> str:
    """One line per meaningful agent event, so a long turn is not confused with a hang.

    Rendered defensively from whatever the event carries: the agent's schema is
    its own, and an unknown event type should still show up rather than vanish.
    """
    etype = str(event.get("type") or "event")
    if etype in _SILENT_EVENTS:
        return ""
    stamp = f"{elapsed_s:.0f}s"
    message = event.get("message") or {}
    if etype == "message_end":
        role = str(message.get("role") or "?")
        if role == "assistant":
            text = _content_text(message.get("content"))
            usage = _usage_of(event) or {}
            tokens = usage.get("totalTokens") or 0
            calls = [b for b in (message.get("content") or []) if isinstance(b, dict) and b.get("type") != "text"]
            return (f"[dim]{stamp}[/] assistant said {len(text)} chars"
                    f"{f' ({len(calls)} tool call(s))' if calls else ''}{f', {tokens} tok' if tokens else ''}")
        return f"[dim]{stamp}[/] {role} message"
    if "tool" in etype:
        name = event.get("name") or (event.get("tool") or {}).get("name") or message.get("name") or ""
        return f"[dim]{stamp}[/] {etype.replace('_', ' ')}{f': {name}' if name else ''}"
    return f"[dim]{stamp}[/] {etype.replace('_', ' ')}"


def stream_agent(cmd: list[str], cwd: Path | None, timeout_s: int, model: str,
                 log=None, quiet_s: float = 45.0, echo_text: bool = False) -> tuple[int, str, str]:
    """Run an agent command, echoing progress as events land; returns (rc, stdout, stderr).

    Shared with `sfactory backend`, which asks the agent one question and must
    not look like it hung while a local GGUF loads — there, the answer is plain
    text, so ``echo_text`` passes every line through instead of only the JSON
    ones. Both pipes are drained on their own threads: pi writes warnings to
    stderr, and a full stderr pipe would deadlock a worker that is otherwise fine.
    """
    import queue
    import threading
    import time

    started = time.monotonic()
    proc = _POPEN(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, errors="replace", bufsize=1, encoding="utf-8",
    )
    lines: queue.Queue[str | None] = queue.Queue()
    err: list[str] = []

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    def drain_stderr() -> None:
        assert proc.stderr is not None
        err.extend(proc.stderr.readlines())

    threading.Thread(target=pump, daemon=True).start()
    threading.Thread(target=drain_stderr, daemon=True).start()

    out: list[str] = []
    last_sight = started
    poll = min(5.0, max(0.05, quiet_s / 2))
    while True:
        now = time.monotonic()
        if now - started > timeout_s:
            proc.kill()
            proc.wait()
            minutes = timeout_s / 60
            spent = f"{minutes:.0f} min" if minutes >= 1 else f"{timeout_s}s"
            return (124, "".join(out), f"timed out after {spent} waiting for {model}")
        if log and now - last_sight > quiet_s:
            log(f"    │ … {now - started:.0f}s with no event — [bold]{model}[/] is "
                "still reading the prompt; a local model spends most of the turn here")
            last_sight = now
        try:
            line = lines.get(timeout=poll)
        except queue.Empty:
            continue
        if line is None:
            break
        out.append(line)
        last_sight = time.monotonic()
        if not log:
            continue
        if not line.startswith("{"):
            if echo_text and line.strip():
                log(f"  │ {line.rstrip()[:300]}")
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        text = _describe(event, last_sight - started)
        if text:
            log(f"    │ {text}")
    proc.wait()
    return (proc.returncode or 0, "".join(out), "".join(err))


def run_worker(brief_path: Path, config: FactoryConfig, cwd: Path, log=None) -> PiRun:
    """Execute one element from a rendered brief; JSON events stream to us, live."""
    cmd = _base_command(config, config.harness.worker_mode)
    cmd.append(f"@{brief_path}")
    started = __import__("time").monotonic()
    rc, stdout, stderr = stream_agent(cmd, cwd, config.harness.worker_timeout_min * 60,
                                      config.backend.model, log=log)
    run = _parse_events(stdout)
    run.returncode = rc
    if not run.final_text and stdout and not stdout.lstrip().startswith("{"):
        run.final_text = stdout  # print-mode fallback
    if rc != 0 and stderr:
        run.final_text = (run.final_text + "\n" + stderr).strip()
    run.duration_s = __import__("time").monotonic() - started
    return run


def run_reviewer(prompt: str, config: FactoryConfig, cwd: Path, timeout_min: int = 15) -> PiRun:
    """Fresh-context read-only review (verdict expected in the final text)."""
    cmd = _base_command(config, config.harness.review_mode)
    cmd.append(prompt)
    started = __import__("time").monotonic()
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout_min * 60,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    run = _parse_events(proc.stdout or "")
    run.returncode = proc.returncode
    run.final_text = proc.stdout.strip() if proc.stdout else (proc.stderr or "").strip()
    run.duration_s = __import__("time").monotonic() - started
    return run

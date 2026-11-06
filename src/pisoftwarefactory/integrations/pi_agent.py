"""coding-agent wrapper (ADR-0002/ADR-0017/ADR-0019): pi, omp and zcode.

Workers run ``<agent> --mode json`` (JSONL events → orchestrator parses progress
and token usage); reviewers run ``<agent> --print --tools read,grep,find,ls``
(fresh-context, read-only). Events are parsed defensively: the orchestrator
never depends on the agent's internal event schema beyond ``type`` and ``usage``.

zcode (ADR-0019) has no ``--model``/``--provider``/``--api-key`` flags at all —
it serves whatever model its own settings or ``zcode login`` define. Workers
run ``-p … --json --mode yolo`` with the brief attached; reviewers run
``-p … --mode plan`` (read-only by construction). Its ``--json`` schema is not
pinned yet, so the parser only makes a best-effort final-text extraction and
leaves token/tool accounting to a follow-up once captured live.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..config import FactoryConfig
from ..harnesses import HARNESSES, binary, installed, no_window, selectable_model_id, spawn_argv


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

    @property
    def tool_calls(self) -> tuple[str, ...]:
        """Every tool the agent called across the whole run, first use first.

        ``final_text`` is only the last thing it *said*; a run that worked and then
        went quiet looks identical to one that never started unless the calls are
        counted over all messages, not the final one.
        """
        names: list[str] = []
        for event in self.events:
            if event.get("type") != "message_end":
                continue
            message = event.get("message") or {}
            if message.get("role") != "assistant":
                continue
            for name in _tool_names([b for b in (message.get("content") or [])
                                     if isinstance(b, dict) and b.get("type") != "text"]).split(", "):
                if name and name not in names:
                    names.append(name)
        return tuple(names)


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


_ZCODE_WORKER_PROMPT = ("Work on the task described in the attached brief, in this working "
                        "directory, following all of its instructions.")

_ZCODE_QUIET_NOTE = ("zcode's configured model is not answering — the CLI retries with "
                     "backoff until the budget; check the model/provider in the ZCode app "
                     "settings, or `zcode login`")

# A refused connection (or a missing provider config) is definitive: nothing is
# listening on the other end, and the harness's own retry loop can burn the whole
# budget before it gives up. Watching stderr for the marker turns a 12-minute
# silent wait into a fast failure with the remedy attached.
_FATAL_STDERR_MARKERS = ("ECONNREFUSED", "无法定位 CLI")


def brain_label(config: FactoryConfig) -> str:
    """What actually thinks, per harness — never the nominal [backend] record.

    pi/omp are pointed at an endpoint by the factory, so provider/model is the
    truth. zcode serves whatever its own settings (or `zcode login`) define;
    printing the nominal [backend] there would name a model the run never
    touches (observed: qwen3.5-9b @ llamacpp while Nemotron served the turn).
    """
    if config.harness.name == "zcode":
        return "zcode CLI's own configured model"
    b = config.backend
    serves = b.provider if b.agent_credential else b.base_url
    return f"{b.model} @ {serves}"


def _zcode_command(config: FactoryConfig, mode: str) -> list[str]:
    """zcode argv base (ADR-0019): the model flags simply do not exist here.

    The worker gets ``--mode yolo`` (unattended, full tools — the ``-p`` default,
    made explicit) plus ``--json``; the reviewer gets ``--mode plan``, read-only
    by construction. There is no ``--no-session`` equivalent to pass.
    """
    cmd = [*spawn_argv("zcode"), "--mode", "yolo" if mode == "json" else "plan"]
    if mode == "json":
        cmd += ["--json"]
    return cmd


def _zcode_parse(stdout: str, run: PiRun) -> None:
    """Fill a PiRun from zcode's ``--json`` output — schema captured live.

    One JSON object per run: ``response`` (the answer), ``usage`` with real
    token counts (inputTokens/outputTokens/totalTokens/cacheReadTokens), and a
    ``projection`` block. Non-JSON output (plain-text runs) falls back to the
    raw tail; malformed JSON keeps whatever the event parser already found.
    """
    try:
        payload = json.loads(stdout)
    except ValueError:
        run.final_text = stdout[-4000:]
        return
    if not isinstance(payload, dict):
        run.final_text = stdout[-4000:]
        return
    response = payload.get("response")
    if isinstance(response, str) and response.strip():
        run.final_text = response
    else:
        run.final_text = stdout[-4000:]
    usage = payload.get("usage")
    if isinstance(usage, dict):
        for field, key in (("input_tokens", "inputTokens"), ("output_tokens", "outputTokens")):
            try:
                setattr(run, field, int(usage.get(key) or 0))
            except (TypeError, ValueError):
                pass


def _base_command(config: FactoryConfig, mode: str) -> list[str]:
    """Build the agent command line for the configured harness (ADR-0017).

    Worker (headless JSONL stream): ``--mode json``. Reviewer (one-shot,
    read-only): the ``--print`` flag — print is a flag in pi, not a mode
    (``--mode`` accepts only text|json|rpc).

    Where the harnesses genuinely differ: pi needs ``--provider`` plus the key on
    the command line (for a custom endpoint it ignores models.json's apiKey and
    the provider env var, and a local llama.cpp wants the mocked ``"none"``),
    while omp selects ``provider/id`` and reads its key from its own registry.
    An ``agent_credential`` backend gets no ``--api-key`` at all — that is the
    only way a keyless provider such as freeflow answers instead of 401.
    """
    name = config.harness.name
    if not installed(name):
        others = [h for h in HARNESSES if h != name]
        other = next((h for h in others if installed(h)), others[0])
        raise PiError(
            f"{name} not found on PATH — install it, or switch with `sfactory backend --harness {other}`"
        )
    cmd = [binary(name), "--no-session"]
    if name == "omp":
        cmd += ["--model", selectable_model_id(name, config.backend)]
    else:
        cmd += ["--provider", config.backend.provider, "--model", config.backend.model]
        key = config.backend.resolve_api_key()
        if key:
            cmd += ["--api-key", key]
    if mode == "json":
        cmd += ["--mode", "json"]
    else:
        cmd += ["--print", "--tools", ",".join(config.harness.reviewer_tools)]
    return cmd


_SILENT_EVENTS = frozenset({"session", "message_start", "turn_start", "entry_appended", "agent_settled"})

# pi emits a `message_update` for every partial chunk — two or three per second
# while it writes. A line for each is a wall of identical text that says less
# than the one line it replaces, so writing is reported on this cadence instead.
_WRITE_REPORT_SEC = 15.0


def _tool_names(blocks: list) -> str:
    """The tools a turn called, deduped — "" when the event carries no names.

    "3 tool call(s)" does not tell the operator why the lane is still empty; the
    names do. ``write``/``edit`` in there means the deliverable is being produced,
    ``read``/``grep`` in there means the model is still orienting itself.
    """
    names: list[str] = []
    for block in blocks:
        name = str(block.get("name") or "")
        if name and name not in names:
            names.append(name)
    return ", ".join(names)


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
            shown = f"{len(calls)} tool call(s)"
            named = _tool_names(calls)
            if calls and named:
                shown = f"{shown}: {named}"
            tail = f" ({shown})" if calls else ""
            if tokens:
                tail = f"{tail}, {tokens} tok"
            return f"[dim]{stamp}[/] assistant said {len(text)} chars{tail}"
        return f"[dim]{stamp}[/] {role} message"
    if "tool" in etype:
        name = event.get("name") or (event.get("tool") or {}).get("name") or message.get("name") or ""
        return f"[dim]{stamp}[/] {etype.replace('_', ' ')}{f': {name}' if name else ''}"
    return f"[dim]{stamp}[/] {etype.replace('_', ' ')}"


def _quiet_reason(model: str, agent_credential: bool, provider: str) -> str:
    """Why the stream went silent — which is a different story per backend.

    A local GGUF is reading a 12k-token prompt; a cloud relay that has already
    answered twice and then stops is throttling or dropping the turn. Printing
    the local excuse for a cloud model sends the operator to the wrong box.
    """
    if agent_credential:
        return (f"{model} is quiet — it answered earlier, so this is {provider or 'the provider'} "
                "free-tier throttling or a dropped relay turn, not a prompt to reload")
    return f"{model} is still reading the prompt; a local model spends most of the turn here"


def stream_agent(cmd: list[str], cwd: Path | None, timeout_s: int, model: str,
                 log=None, quiet_s: float = 45.0, echo_text: bool = False,
                 agent_credential: bool = False, provider: str = "",
                 quiet_note: str = "") -> tuple[int, str, str]:
    """Run an agent command, echoing progress as events land; returns (rc, stdout, stderr).

    Shared with `sfactory backend`, which asks the agent one question and must
    not look like it hung while a local GGUF loads — there, the answer is plain
    text, so ``echo_text`` passes every line through instead of only the JSON
    ones. Both pipes are drained on their own threads: pi writes warnings to
    stderr, and a full stderr pipe would deadlock a worker that is otherwise fine.
    ``quiet_note`` replaces the automatic quiet-line reason where the caller
    knows better (zcode's nominal [backend] model would be printed otherwise).
    """
    import queue
    import threading
    import time

    started = time.monotonic()
    proc = _POPEN(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, errors="replace", bufsize=1, encoding="utf-8",
        creationflags=no_window(),
    )
    lines: queue.Queue[str | None] = queue.Queue()
    err: list[str] = []
    dead = threading.Event()

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    def drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            err.append(line)
            if any(marker in line for marker in _FATAL_STDERR_MARKERS):
                dead.set()

    threading.Thread(target=pump, daemon=True).start()
    threading.Thread(target=drain_stderr, daemon=True).start()

    out: list[str] = []
    last_sight = started
    writes = 0
    last_write_report = started
    poll = min(5.0, max(0.05, quiet_s / 2))
    while True:
        now = time.monotonic()
        if now - started > timeout_s:
            proc.kill()
            proc.wait()
            minutes = timeout_s / 60
            spent = f"{minutes:.0f} min" if minutes >= 1 else f"{timeout_s}s"
            return (124, "".join(out), f"timed out after {spent} waiting for {model}")
        if dead.is_set():
            proc.kill()
            proc.wait()
            return (1, "".join(out), "".join(err))
        if log and now - last_sight > quiet_s:
            note = quiet_note or _quiet_reason(model, agent_credential, provider)
            log(f"    │ … {now - started:.0f}s with no event — [bold]{note}[/]")
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
        if event.get("type") == "message_update":
            writes += 1
            if last_sight - last_write_report > _WRITE_REPORT_SEC:
                log(f"    │ {last_sight - started:.0f}s [bold]{model}[/] is writing — "
                    f"{writes} partial update(s)")
                last_write_report = last_sight
            continue
        text = _describe(event, last_sight - started)
        if text:
            log(f"    │ {text}")
    proc.wait()
    return (proc.returncode or 0, "".join(out), "".join(err))


def _worker_command(config: FactoryConfig, brief_path: Path) -> list[str]:
    """Full worker argv per harness: pi/omp take the brief as an @-include."""
    if config.harness.name == "zcode":
        cmd = _zcode_command(config, config.harness.worker_mode)
        cmd += ["-p", _ZCODE_WORKER_PROMPT, "--attach", str(brief_path)]
        return cmd
    cmd = _base_command(config, config.harness.worker_mode)
    cmd.append(f"@{brief_path}")
    return cmd


def _reviewer_command(config: FactoryConfig, prompt: str) -> list[str]:
    """Full reviewer argv per harness (fresh context, read-only)."""
    if config.harness.name == "zcode":
        return [*_zcode_command(config, config.harness.review_mode), "-p", prompt]
    cmd = _base_command(config, config.harness.review_mode)
    cmd.append(prompt)
    return cmd


def run_worker(brief_path: Path, config: FactoryConfig, cwd: Path, log=None) -> PiRun:
    """Execute one element from a rendered brief; JSON events stream to us, live."""
    cmd = _worker_command(config, brief_path)
    zcode = config.harness.name == "zcode"
    started = __import__("time").monotonic()
    rc, stdout, stderr = stream_agent(cmd, cwd, config.harness.worker_timeout_min * 60,
                                      config.backend.model, log=log, agent_credential=config.backend.agent_credential,
                                      provider=config.backend.provider,
                                      quiet_note=_ZCODE_QUIET_NOTE if zcode else "")
    run = _parse_events(stdout)
    run.returncode = rc
    if not run.final_text and stdout and not stdout.lstrip().startswith("{"):
        run.final_text = stdout  # print-mode fallback
    if config.harness.name == "zcode" and not run.final_text and stdout.strip():
        _zcode_parse(stdout, run)  # --json schema captured live 2026-11-05
    if rc != 0 and stderr:
        run.final_text = (run.final_text + "\n" + stderr).strip()
    run.duration_s = __import__("time").monotonic() - started
    return run


def run_reviewer(prompt: str, config: FactoryConfig, cwd: Path, timeout_min: int = 15,
                 log=None) -> PiRun:
    """Fresh-context read-only review (verdict expected in the final text).

    Streams through the same pump as the worker: a review that prints nothing for
    four minutes is indistinguishable from a hang, and the reviewer is the one
    stage nobody can inspect, so it has to narrate itself.
    """
    cmd = _reviewer_command(config, prompt)
    started = __import__("time").monotonic()
    rc, stdout, stderr = stream_agent(cmd, cwd, timeout_min * 60, config.backend.model,
                                      log=log, echo_text=True,
                                      agent_credential=config.backend.agent_credential,
                                      provider=config.backend.provider,
                                      quiet_note=_ZCODE_QUIET_NOTE
                                      if config.harness.name == "zcode" else "")
    run = _parse_events(stdout)
    run.returncode = rc
    run.final_text = stdout.strip() if stdout else (stderr or "").strip()
    run.duration_s = __import__("time").monotonic() - started
    return run

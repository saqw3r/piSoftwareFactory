"""pi coding agent wrapper (ADR-0002).

Workers run ``pi --mode json`` (JSONL events → orchestrator parses progress
and token usage); reviewers run ``pi --print --tools read,grep,find,ls``
(fresh-context, read-only). Events are parsed defensively: the orchestrator
never depends on pi's internal event schema beyond ``type`` and ``usage``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..config import FactoryConfig


class PiError(RuntimeError):
    pass


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

        usage = event.get("usage")
        if isinstance(usage, dict):
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
    """Build a pi command line.

    Worker (headless JSONL stream): ``--mode json``. Reviewer (one-shot,
    read-only): the ``--print`` flag — print is a flag in pi, not a mode
    (``--mode`` accepts only text|json|rpc).
    """
    binary = shutil.which("pi")
    if binary is None:
        raise PiError("pi not found on PATH — install with: npm install -g @earendil-works/pi-coding-agent")
    cmd = [
        binary,
        "--no-session",
        "--provider", config.backend.provider,
        "--model", config.backend.model,
    ]
    if mode == "json":
        cmd += ["--mode", "json"]
    else:
        cmd += ["--print", "--tools", ",".join(config.harness.reviewer_tools)]
    return cmd


def run_worker(brief_path: Path, config: FactoryConfig, cwd: Path) -> PiRun:
    """Execute one element from a rendered brief; JSON events stream to us."""
    cmd = _base_command(config, config.harness.worker_mode)
    cmd.append(f"@{brief_path}")
    started = __import__("time").monotonic()
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=config.harness.worker_timeout_min * 60,
        encoding="utf-8",
        errors="replace",
    )
    run = _parse_events(proc.stdout or "")
    run.returncode = proc.returncode
    if not run.final_text and proc.stdout and not proc.stdout.lstrip().startswith("{"):
        run.final_text = proc.stdout  # print-mode fallback
    if proc.returncode != 0 and proc.stderr:
        run.final_text = (run.final_text + "\n" + proc.stderr).strip()
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
        encoding="utf-8",
        errors="replace",
    )
    run = _parse_events(proc.stdout or "")
    run.returncode = proc.returncode
    run.final_text = proc.stdout.strip() if proc.stdout else (proc.stderr or "").strip()
    run.duration_s = __import__("time").monotonic() - started
    return run

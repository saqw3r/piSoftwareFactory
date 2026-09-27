"""Deterministic gate runner (ADR-0013).

Every step is recorded as ``passed``, ``failed``, or ``skipped:<reason>``
— a skip is evidence, never a silent pass. Output is truncated head+tail
(ADR-0010) so gate feedback can be attached to worker briefs without
blowing the context window. Evidence JSON lands in ``.factory/run/``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import FactoryConfig
from .gate_profiles import PROFILES, CONDITION_CHECKERS, GateProfile

PASSED = "passed"
FAILED = "failed"

_HEAD_LINES = 40
_TAIL_LINES = 40


@dataclass
class StepResult:
    name: str
    status: str  # "passed" | "failed" | "skipped:<reason>"
    detail: str = ""
    duration_s: float = 0.0


@dataclass
class ProfileResult:
    language: str
    steps: list[StepResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(step.status == PASSED or step.status.startswith("skipped:") for step in self.steps)

    def to_dict(self) -> dict:
        return {"language": self.language, "passed": self.passed, "steps": [asdict(s) for s in self.steps]}


def truncate_output(text: str, head: int = _HEAD_LINES, tail: int = _TAIL_LINES) -> str:
    """Head+tail truncation with an elision marker (ADR-0010)."""
    lines = text.splitlines()
    if len(lines) <= head + tail + 1:
        return text
    elided = len(lines) - head - tail
    return "\n".join([*lines[:head], f"... [{elided} lines elided] ...", *lines[-tail:]])


def _condition_met(profile: GateProfile, condition: str | None, root: Path) -> tuple[bool, str]:
    if condition is None:
        return True, ""
    if condition == "lockfile_absent":
        return not CONDITION_CHECKERS["js"]("lockfile_present", root), ""
    checker = CONDITION_CHECKERS.get(profile.language)
    if checker and checker(condition, root):
        return True, ""
    return False, condition


def _run_step(step_argv: tuple[str, ...], root: Path, timeout: int) -> StepResult:
    name = " ".join(step_argv)
    binary = shutil.which(step_argv[0])
    if binary is None:
        return StepResult(name, f"skipped:tool '{step_argv[0]}' not found")
    argv = (binary, *step_argv[1:])
    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        detail = (proc.stdout or "") + (proc.stderr or "")
        status = PASSED if proc.returncode == 0 else FAILED
        return StepResult(name, status, truncate_output(detail).strip(), time.monotonic() - started)
    except subprocess.TimeoutExpired as exc:
        out = ((exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""))
        return StepResult(name, FAILED, truncate_output(f"TIMEOUT after {timeout}s\n{out}"), time.monotonic() - started)


def run_profile(profile: GateProfile, root: Path, config: FactoryConfig) -> ProfileResult:
    """Run every step of *profile* in *root* with skip-with-evidence semantics."""
    result = ProfileResult(language=profile.language)
    extra = config.gates.extra.get(profile.language, [])
    all_steps: list[tuple[tuple[str, ...], str | None]] = [
        (step.argv, step.condition) for step in profile.steps
    ] + [(tuple(cmd), None) for cmd in extra]

    for argv, condition in all_steps:
        met, why = _condition_met(profile, condition, root)
        if not met:
            result.steps.append(StepResult(" ".join(argv), f"skipped:{why}"))
            continue
        result.steps.append(_run_step(argv, root, config.gates.step_timeout_sec))
    return result


def run_gates(root: Path, config: FactoryConfig, only_lang: str | None = None) -> list[ProfileResult]:
    """Run all active language gates (or one with *only_lang*); persist evidence."""
    active = only_lang.split(",") if only_lang else list(config.languages)
    results: list[ProfileResult] = []
    for lang in active:
        profile = PROFILES.get(lang)  # type: ignore[arg-type]
        if profile is None:
            raise SystemExit(f"unknown language {lang!r}; valid: {list(PROFILES)}")
        if not profile.applies_to(root) and not only_lang:
            results.append(ProfileResult(language=lang, steps=[StepResult("detect", "skipped:no marker files")]))
            continue
        results.append(run_profile(profile, root, config))

    run_dir = root / ".factory" / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    evidence = run_dir / f"gates-{stamp}.json"
    evidence.write_text(
        json.dumps({"results": [r.to_dict() for r in results]}, indent=2) + "\n", encoding="utf-8"
    )
    return results

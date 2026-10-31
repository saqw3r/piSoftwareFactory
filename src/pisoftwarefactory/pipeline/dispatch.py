"""Dispatch (ADR-0007): node → lane worktree → pi worker → gates loop.

The gate result decides. On failure the truncated gate output is fed back
into a fresh worker run (bounded by ``harness.max_retries``); a node that
still fails is ``blocked`` with evidence — never marked done on agent claims.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import FactoryConfig, load_config
from ..gates import run_gates
from ..integrations import hindsight
from ..integrations.pi_agent import PiRun, run_worker
from .brief import render_brief, write_brief
from .graph import Graph, Node

WORKTREES = ".factory/worktrees"


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ("git", "-C", str(root), *args), capture_output=True, text=True, check=check
    )


def _resolve_base_ref(root: Path, config: FactoryConfig, log) -> str:
    """Configured base ref, or the repo's real default when it's missing.

    Repos default to `main` or `master` (and scaffolded ones to whatever was
    current at init); insisting on exactly the configured name turns a fresh
    checkout into a support call. Fall back visibly, never silently.
    """
    wanted = config.lanes.base_ref
    if _git(root, "rev-parse", "--verify", wanted, check=False).returncode == 0:
        return wanted
    candidates = ["main", "master"]
    current = _git(root, "branch", "--show-current", check=False).stdout.strip()
    if current and current not in candidates:
        candidates.append(current)
    for candidate in candidates:
        if candidate != wanted and _git(root, "rev-parse", "--verify", candidate, check=False).returncode == 0:
            log(f"  [yellow]base ref {wanted!r} missing — using {candidate!r} instead[/] "
                "(set [lanes] base_ref to silence this)")
            return candidate
    return wanted  # nothing valid: let worktree add fail with the clear error below


def ensure_lane(root: Path, config: FactoryConfig, lane: str, log=print) -> Path:
    """Create the lane worktree + ``lane/<name>`` branch if missing; returns its path.

    Git failures are re-raised with stderr attached: a bare CalledProcessError
    hides the real cause (dubious ownership, missing base ref, stale branch).
    """
    wt = root / WORKTREES / lane
    if not (wt / ".git").exists():
        (root / WORKTREES).mkdir(parents=True, exist_ok=True)
        base_ref = _resolve_base_ref(root, config, log)
        try:
            _git(root, "worktree", "add", str(wt), "-b", f"lane/{lane}", base_ref)
        except subprocess.CalledProcessError as exc:
            err = ((exc.stderr or "") + "\n" + (exc.stdout or "")).strip()
            if f"lane/{lane}" in err and "already exists" in err:
                # Leftover branch from an earlier run — attach to it instead.
                _git(root, "worktree", "add", str(wt), f"lane/{lane}")
                return wt
            hint = ""
            if "dubious ownership" in err:
                # Forward slashes: git matches safe.directory literally and prints
                # the path that way — a backslash entry may not match on Windows.
                hint = ("\ngit distrusts this directory (network drive / copied dir). "
                        f"Trust it once: git config --global --add safe.directory '{root.as_posix()}'")
            elif "invalid reference" in err or "unknown revision" in err or "no such branch" in err:
                hint = (f"\nbase ref {config.lanes.base_ref!r} doesn't exist here — set the real "
                        "branch in factory.toml [lanes] base_ref (see `git branch --list`).")
            raise RuntimeError(f"cannot create lane worktree {lane}:\n{err}{hint}") from exc
    return wt


@dataclass
class DispatchResult:
    node: Node
    worker_runs: list[PiRun]
    gate_pass: bool
    verdict: str  # pass | blocked
    evidence: str = ""

    @property
    def tokens(self) -> tuple[int, int]:
        return (
            sum(r.input_tokens for r in self.worker_runs),
            sum(r.output_tokens for r in self.worker_runs),
        )


def _memories_for(root: Path, config: FactoryConfig, node: Node) -> list[str]:
    bank = config.memory.bank_id or root.name.lower().replace(" ", "-")
    return hindsight.recall(
        config.memory.hindsight_url, bank, f"{node.title} {node.kind} {node.language}", config.memory.recall_top_k
    )


def dispatch_node(root: Path, node: Node, config: FactoryConfig | None = None, log=print) -> DispatchResult:
    """Run one node through Execute → Gates (with bounded retries)."""
    root = root.resolve()
    config = config or load_config(root)
    lane = node.lane or "L1"
    wt = ensure_lane(root, config, lane)

    memories = _memories_for(root, config, node)
    worker_runs: list[PiRun] = []
    gate_feedback = ""
    gate_pass = False

    for attempt in range(config.harness.max_retries + 1):
        brief = render_brief(
            node, lane=lane, main_branch=config.release.main_branch, memories=memories, gate_feedback=gate_feedback
        )
        brief_path = write_brief(wt / ".factory" / "briefs", node, brief)
        log(f"  [bold]{node.id}[/] attempt {attempt + 1}: pi worker on lane {lane} (brief={brief_path.name})")
        run = run_worker(brief_path, config, cwd=wt)
        worker_runs.append(run)
        log(f"    pi finished rc={run.returncode} in {run.duration_s:.0f}s (~{run.input_tokens}in/{run.output_tokens}out tok)")

        log(f"    gates running in {wt.name}…")
        results = run_gates(wt, config)
        gate_pass = all(r.passed for r in results)
        evidence = "; ".join(
            f"{r.language}:{'pass' if r.passed else 'BLOCKED'}" for r in results
        )
        if gate_pass:
            break
        gate_feedback = "\n".join(
            f"[{r.language}] " + "\n".join(f"{s.name}: {s.status}\n{s.detail}" for s in r.steps if s.status != "passed")
            for r in results
            if not r.passed
        )
        log(f"    gates BLOCKED ({evidence}) — feeding output back to worker")

    node.gate_status = "pass" if gate_pass else "blocked"
    node.status = "done" if gate_pass else "blocked"
    node.touch()
    if gate_pass:
        hindsight.retain(
            config.memory.hindsight_url,
            config.memory.bank_id or root.name.lower().replace(" ", "-"),
            f"Element {node.id} ({node.kind}, lane {lane}) completed and passed gates.",
            context="factory dispatch",
        )
    return DispatchResult(node=node, worker_runs=worker_runs, gate_pass=gate_pass, verdict=node.status, evidence=evidence if not gate_pass else "")


def dispatch_wave(root: Path, graph: Graph, config: FactoryConfig, log=print) -> list[DispatchResult]:
    """Dispatch all ready nodes, round-robin across lanes."""
    results: list[DispatchResult] = []
    for i, node in enumerate(graph.ready()):
        node.lane = node.lane or f"L{i % config.lanes.count + 1}"
        node.status = "dispatched"
        node.touch()
        log(f"dispatching {node.id}: {node.title}")
        results.append(dispatch_node(root, node, config, log))
    return results

"""Dispatch (ADR-0007): node → lane worktree → pi worker → gates loop.

The gate result decides. On failure the truncated gate output is fed back
into a fresh worker run (bounded by ``harness.max_retries``); a node that
still fails is ``blocked`` with evidence — never marked done on agent claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import FactoryConfig, load_config
from ..gates import run_gates
from ..gitcmd import GitRefused
from ..gitcmd import run as _git
from ..harnesses import remedy
from ..integrations import hindsight
from ..integrations.pi_agent import PiRun, run_worker
from .brief import render_brief, write_brief
from .graph import Graph, Node

WORKTREES = ".factory/worktrees"


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
        except GitRefused as exc:
            err = str(exc)
            if f"lane/{lane}" in err and "already exists" in err:
                # Leftover branch from an earlier run — attach to it instead.
                _git(root, "worktree", "add", str(wt), f"lane/{lane}")
                return wt
            hint = ""
            if "invalid reference" in err or "unknown revision" in err or "no such branch" in err:
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


def _is_bookkeeping(status_line: str) -> bool:
    """The worker's own brief lives in the lane (``.factory/briefs/…``), so an
    untouched lane always reports ``?? .factory/`` — that is scaffolding, not work."""
    path = status_line[2:].strip().split(" -> ", 1)[0].strip('"')
    return path.startswith(".factory/")


@dataclass(frozen=True)
class LaneState:
    """What the lane actually holds after the agent stopped — and whether git said so."""

    edits: tuple[str, ...] = ()   # paths changed or left untracked, the brief excluded
    committed: bool = False       # commits ahead of the base ref
    refusal: str = ""             # git's own words when it declined to answer

    @property
    def produced(self) -> bool:
        return self.committed or bool(self.edits)


def _lane_state(wt: Path, base_ref: str) -> LaneState:
    """Ask git what the lane holds.

    Gates passing on a lane identical to its base prove nothing, but neither does
    a blank ``git status`` when git actually declined to run; only the first is a
    fact, so the second case travels with its own cause.

    ``committed`` is a separate fact from ``produced`` on purpose. An agent can
    write the deliverable and never commit it, and ``release`` merges the *branch*
    — so an uncommitted file passes review, gets called done, and then vanishes
    from the merge without a word.
    """
    try:
        ahead = _git(wt, "rev-list", "--count", f"{base_ref}..HEAD").stdout.strip()
        status = _git(wt, "status", "--porcelain", "--untracked-files=all").stdout
    except GitRefused as exc:
        return LaneState(refusal=str(exc))
    edits = tuple(line[2:].strip().strip('"') for line in status.splitlines()
                  if line.strip() and not _is_bookkeeping(line))
    return LaneState(edits=edits, committed=ahead.isdigit() and int(ahead) > 0)


def _why_nothing_changed(run: PiRun, config: FactoryConfig, log) -> None:
    """rc=0 with an empty lane: show what the model actually said, right here."""
    text = (run.final_text or "").strip()
    if not text:
        log(f"    [yellow]{config.harness.name} streamed no text and made no tool call[/] — "
            f"{config.backend.model!r} answered with an empty message")
        log("    that model cannot do the task. Pick one that can: `sfactory backend --model <id>`")
        return
    log("    what the agent said instead of changing the lane:")
    for line in text.splitlines()[-8:]:
        log(f"      │ {line[:300]}")


def _commit_feedback(edits: tuple[str, ...], node: Node) -> str:
    """The worker did the work and forgot the commit — say only that, with the command."""
    quoted = " ".join(f'"{p}"' for p in edits[:8])
    return (f"the gates PASSED and the files are already written ({quoted}); the lane holds "
            f"no commit, so release would drop this work. Do nothing except commit exactly "
            f"these files: git add -- {quoted} && GIT_EDITOR=true git commit -m "
            f'"{node.id}: {node.title[:60]}" && git show --stat HEAD. '
            "Do not rewrite, extend or add any other file.")


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
    worker_died = False
    evidence = ""
    base_ref = _resolve_base_ref(root, config, log)
    # Gates passing with the deliverable still uncommitted is one fixable mistake,
    # not a verdict — so the worker gets exactly one attempt to make the commit.
    commit_pinged = False

    for attempt in range(config.harness.max_retries + 1):
        brief = render_brief(
            node, lane=lane, main_branch=config.release.main_branch, memories=memories, gate_feedback=gate_feedback
        )
        brief_path = write_brief(wt / ".factory" / "briefs", node, brief)
        # Naming base_url for a provider the agent authenticates itself would
        # point the operator at an endpoint the worker never calls.
        serves = config.backend.provider if config.backend.agent_credential else config.backend.base_url
        log(f"  [bold]{node.id}[/] attempt {attempt + 1}: {config.harness.name} worker "
              f"({config.backend.model} @ {serves}) on lane {lane} "
              f"(brief={brief_path.name})")
        run = run_worker(brief_path, config, cwd=wt, log=log)
        worker_runs.append(run)
        log(f"    {config.harness.name} finished rc={run.returncode} in {run.duration_s:.0f}s "
            f"(~{run.input_tokens}in/{run.output_tokens}out tok)")
        if not run.ok:
            # A dead agent changed nothing, so the gates would pass on an empty
            # lane and the node would be marked done without a line of work.
            worker_died = True
            log("    [red]the agent died before the model answered[/] — this lane has no change")
            for line in (run.final_text or "").strip().splitlines()[-6:]:
                log(f"      │ {line[:300]}")
            fix = remedy(run.final_text or "", config.backend, config.harness.name)
            if fix:
                log(f"    fix: {fix}")
            break

        log(f"    gates running in {wt.name}…")
        results = run_gates(wt, config)
        gate_pass = all(r.passed for r in results)
        evidence = "; ".join(
            f"{r.language}:{'pass' if r.passed else 'BLOCKED'}" for r in results
        )
        if gate_pass:
            state = _lane_state(wt, base_ref)
            if state.committed or state.refusal or not state.edits or commit_pinged:
                break
            commit_pinged = True
            gate_feedback = _commit_feedback(state.edits, node)
            log(f"    gates passed, but {len(state.edits)} file(s) sit uncommitted "
                f"({', '.join(state.edits[:8])}) — release merges commits, so the worker "
                "gets one attempt to make it")
            continue
        gate_feedback = "\n".join(
            f"[{r.language}] " + "\n".join(f"{s.name}: {s.status}\n{s.detail}" for s in r.steps if s.status != "passed")
            for r in results
            if not r.passed
        )
        log(f"    gates BLOCKED ({evidence}) — feeding output back to worker")

    state = _lane_state(wt, base_ref)
    if worker_died:
        last = worker_runs[-1]
        # Its own words are the diagnosis; "rc=1" alone sends the operator guessing.
        evidence = evidence or (f"{config.harness.name} exited rc={last.returncode}"
                                f" with no change: {(last.final_text or '').strip()[:400] or 'it said nothing'}")
    elif state.refusal:
        evidence = f"the lane could not be inspected: {state.refusal}"
        log(f"    [red]cannot tell whether this lane holds work[/] — git refused, so nothing is "
            f"being called done:\n      {state.refusal}")
    elif gate_pass and not state.produced:
        evidence = (f"the agent finished cleanly but left no commit and no edit in the lane "
                    f"({config.harness.name} + {config.backend.model})")
        log(f"    [red]{evidence}[/] — not marking it done")
        _why_nothing_changed(worker_runs[-1], config, log)
    elif gate_pass and not state.committed:
        left = ", ".join(state.edits[:8])
        asked = " (after being asked once to commit them)" if commit_pinged else ""
        evidence = (f"the agent wrote {len(state.edits)} file(s) and committed none of them{asked}"
                    f" ({left}) — release merges commits, so this would be dropped without a trace")
        log(f"    [red]{evidence}[/] — not marking it done")
        add = " ".join(f'"{p}"' for p in state.edits[:8])
        log(f'    to keep the work: git -C "{wt}" add -- {add} && '
            f'git -C "{wt}" commit -m "{node.id}: {node.title[:60]}"')
    elif not gate_pass and gate_feedback:
        # The summary says which language blocked; the operator also needs what
        # the gate said, and the worker's retry already got this text.
        evidence = f"{evidence}\n{gate_feedback[:1000]}"
    node.gate_status = "pass" if gate_pass else "blocked"
    node.status = "done" if (gate_pass and state.committed and not worker_died and not state.refusal) else "blocked"
    # The reason outlives the terminal: `sfactory status` and `sfactory wave`
    # both read it off the graph days later, when nobody has the scrollback.
    node.evidence = "" if node.status == "done" else (evidence or "blocked without a recorded reason")
    node.touch()
    if gate_pass and node.status == "done":
        hindsight.retain(
            config.memory.hindsight_url,
            config.memory.bank_id or root.name.lower().replace(" ", "-"),
            f"Element {node.id} ({node.kind}, lane {lane}) completed and passed gates.",
            context="factory dispatch",
        )
    return DispatchResult(node=node, worker_runs=worker_runs, gate_pass=gate_pass, verdict=node.status,
                          evidence="" if node.status == "done" else node.evidence)


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

# ADR-0012: Scaffold file set written by `sfactory init`

- **Status:** Proposed
- **Date:** 2026-09-27
- **Deciders:** Serhii Surnin (owner), ZCode (facilitator)
- **Depends on:** ADR-0001 (concept), ADR-0002 (pi), ADR-0005 (hindsight), ADR-0007 (pipeline)

## Context

`sfactory init --auto` is the one-liner's payload: it must create everything
a target project needs to run the factory, and nothing it doesn't (no
opinionated rewrites of the project's own build system).

## Decision (proposed)

`sfactory init` writes into the target project (detected languages activate
matching gate profiles; all files below except `.pi/` are committed to the
target repo):

| Path | Purpose |
|---|---|
| `AGENTS.md` | factory constitution: pipeline stages, "1 element = 1 commit", "done ≠ verified ≠ works", verdict enums, "orchestrator never decides for the user", git hygiene rules (`GIT_EDITOR=true`, `git show --stat HEAD`) |
| `factory.toml` | backend (llama.cpp URL/model/ctx — autofilled from live probe), lanes, languages, gate profiles, memory bank id, `harness="pi"`, `auto` flag |
| `launch.graph.json` | intake DAG: nodes, criticality tiers, blocker edges, wave state |
| `.pi/models.json` | pi provider → `llamacpp` → owner's endpoint (per-project; merged into pi's agent dir at implementation if that's the documented location) |
| `.factory/gates/` | `gate_runner.py` + per-language profiles + `preflight.py` + `interlock_check.py` |
| `.factory/lanes/` | worktree lane manager (`lane.py`: create/list/clean) |
| `.factory/briefs/` | rendered worker briefs (≤2k tokens) per dispatched node |
| `.factory/run/` | per-run evidence (JSONL events, gate output, verdicts) — **gitignored** |
| `memory/reflections.json` | wave/incident reflections (append-only log) |
| `memory/proposals.md` | process-improvement proposals; accepted ones become gates |
| `memory/decisions.md` | project-level decision log (ADR-lite for the target repo) |
| `memory/handover/CURRENT.md` | cross-session handover state |
| `memory/open_questions.md` | questions parked for the human |

Plus two **bootstrap integrations** (files written, services not started
without the owner's consent):

- `hindsight.bootstrap.md` — exact commands + env vars to run Hindsight
  locally against the same llama.cpp, and the bank's MCP URL for ZCode.
- `paperclip.company.json` — company seed: Architect → per-lane Workers →
  Reviewer; applied by `sfactory paperclip` once Paperclip is onboarded.

`--auto` additionally: sets `auto = true` in `factory.toml` (unattended
waves, human gate only at Release), prints the Paperclip onboarding and
Hindsight start commands at the end.

## Alternatives considered

- Fewer files (config only, no `memory/`) — loses the saintnikopol
  reflection loop and esf evidence trail.
- Everything in one `.factory/` folder — hides `AGENTS.md`/`factory.toml`
  from both humans and agent harnesses that scan repo root.

## Consequences

- The scaffold is additive; existing projects are never restructured.
- `.factory/run/` gitignored from day one (session evidence must not leak
  into commits).
- Idempotent re-runs: `init` refuses to overwrite an existing file without
  `--force` (per file).

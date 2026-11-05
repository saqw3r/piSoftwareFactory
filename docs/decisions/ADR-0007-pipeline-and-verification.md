# ADR-0007: Pipeline stages and verification discipline

- **Status:** Accepted
- **Date:** 2026-09-27
- **Deciders:** Serhii Surnin (owner, approved synthesized plan), ZCode (facilitator)
- **Origin:** synthesis of the three references (see ADR-0001) + approved implementation plan

## Context

The three references converge on the same discipline from different angles:
saintnikopol proved it in production (88 days, 3 024 commits, 889 PRs, one
human), Factory.ai productizes it as SDLC coverage, esf encodes it as
"deterministic verification gates; agent success alone is not factory
success". Incident lessons from the talk (barrel-file outage, skipped checks
looking like passes, headless rebase hangs) all became **mechanical gates**.

## Decision

The factory runs an **8-stage pipeline**; every stage is machine-checkable
except the ones explicitly reserved for the human:

1. **Intake** — task → `IntakeDecision` (kind, risk tier, lane, size, blockers)
   via Jev or heuristic fallback (ADR-0004); appended to `launch.graph.json`
   DAG with criticality tiers and blocker edges.
2. **Brief** — render a ≤2k-token worker brief: node description, explicit
   `@file` context, hindsight recall top-k (ADR-0005), gate expectations,
   "1 element = 1 commit" contract.
3. **Dispatch** — sort ready nodes into **waves**; one git worktree + branch
   per lane; interlock check (file-set diff vs open PRs/lanes) before start.
4. **Execute** — `pi --mode json` headless run per node (ADR-0002); JSONL
   events parsed for progress, token budget enforced; docs committed with
   code.
5. **Gates** — deterministic, per-language profiles (dotnet / uv+ruff+pytest /
   npm / cargo+clippy / cmake+ctest / go vet+build+test). "Compiling is not
   building, and building is not working." Gate failures loop back to the
   worker with the gate output as feedback (bounded retries).
6. **Review** — fresh-context, **read-only** reviewer (`pi --print --tools
   read,grep,find,ls`); verdict enum `clean | nits | blockers`; blockers
   loop back to Execute. Cross-check on high-risk lanes.
7. **Release** — waves accumulate on `stage`; `main` moves only via
   **human-approved merge** (Paperclip board approval or `sfactory release
   --approve`), followed by smoke check and dated tag.
8. **Reflect** — mandatory after every wave/incident: hindsight `retain`,
   `memory/reflections.json` entry, `proposals.md` process-improvement
   suggestions; accepted proposals become new gates (each incident → a
   script).

Operating rules baked into `AGENTS.md` and the orchestrator:

- "A prompt is forgotten on the fortieth turn. A script is not." — every
  incident becomes a gate script, not a memo.
- "Done ≠ verified ≠ works." — only gates advance state.
- "The orchestrator never decides for the user" — human gates are
  unconditional; auto mode never merges to `main`.
- "1 element = 1 commit"; interruption granularity is one commit;
  `git show --stat HEAD` after every commit; `GIT_EDITOR=true` on git ops.

## Consequences

- `launch.graph.json` + wave scheduler is the heart of `sfactory run`/`wave`.
- Review verdicts and gate results are recorded per node (evidence trail,
  esf-style) in `.factory/run/` (gitignored).
- The pipeline is resumable: state lives in the graph file + git, not in
  agent conversations.

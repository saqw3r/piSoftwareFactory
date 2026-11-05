# ADR-0006: Multi-agent management = Paperclip company

- **Status:** Accepted
- **Date:** 2026-09-27
- **Deciders:** Serhii Surnin (owner, named paperclip in the original request), ZCode (facilitator)
- **Origin:** owner request "set up paperclip for multiagents"; discovery confirmed fit

## Context

The owner asked for multi-agent orchestration via **Paperclip**. Research:

**Paperclip** (`paperclipai/paperclip`, MIT, ~89k★) is a self-hosted
Node.js server + React UI that manages teams of AI agents as a "company":

- Org chart with roles, titles, reporting lines, permissions, budgets.
- **Bring Your Own Agent** — "if it can receive a heartbeat, it's hired";
  adapters for Claude Code, Codex, Cursor, **Bash/HTTP bots** (wraps any CLI),
  plus an adapter-plugin spec.
- Issues/tickets linked to goals with blocker dependencies; atomic task
  checkout with execution locks; heartbeats (DB-backed wakeups); budget caps
  that pause overspending agents; governance (approval gates, pause/
  terminate); full audit logs.
- Workspace resolution uses **git worktrees** — matching ADR-0007's lane model.
- Install: `npx paperclipai onboard --yes` (Node.js 24.11+); server on
  `http://localhost:3100` with embedded PostgreSQL; docs at
  docs.paperclip.ing.

## Decision

Use **Paperclip as the management layer** over the factory's execution:

- `sfactory paperclip` provisions a **company** for the project:
  **Architect** (design/briefs) → per-lane **Workers** → **Reviewer**
  (verdicts), with the Release merge as a **board approval gate**.
- Graph nodes (see ADR-0007) are created as Paperclip **issues**; dispatch
  consumes checked-out issues; completion + verdicts flow back as comments.
- Worker agents execute via the **pi harness** (ADR-0002) — wired through
  Paperclip's Bash/HTTP adapter (exact adapter details verified against
  docs.paperclip.ing during implementation).
- `sfactory` remains the **pipeline engine** (gates, waves, worktrees,
  releases); Paperclip is the **governance/visibility layer** (who works,
  budgets, approvals, audit).

## Consequences

- Requires Node.js 24.11+; adds one local service (server on :3100).
- `sfactory doctor` checks the Paperclip server and prints onboarding
  instructions when absent.
- If Paperclip is absent, the factory still runs headless via `sfactory`
  directly (management layer is optional, engine is not).
- Human approval at Release happens in the Paperclip UI (or `sfactory
  release --approve` CLI equivalent for headless setups).

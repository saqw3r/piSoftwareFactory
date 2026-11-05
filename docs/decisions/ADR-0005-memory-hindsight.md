# ADR-0005: Memory = Hindsight (self-hosted, llamacpp-backed)

- **Status:** Accepted
- **Date:** 2026-09-27
- **Deciders:** Serhii Surnin (owner, named hindsight in the original request), ZCode (facilitator)
- **Origin:** owner request "leveraging Jev and hindsight"; discovery confirmed fit

## Context

Cross-session memory is what turns a set of scripted agents into a factory
that learns: past decisions, incident-derived gates, wave reflections, and
handover state must survive context resets. The saintnikopol factory keeps
this in repo files (`reflections.json`, `decisions.md`, `handover/CURRENT.md`);
the owner explicitly asked to leverage **Hindsight**.

**Hindsight** (`vectorize-io/hindsight`, MIT, ~37k★) is a self-hosted memory
server for agents:

- Four memory types: world facts, experiences, observations, mental models.
- Isolated **memory banks** (one per user/agent/project).
- Three operations: `retain` (store + fact extraction), `recall` (4 parallel
  retrieval strategies, rank-fused + reranked), `reflect` (deep analysis).
- Ships a **built-in MCP endpoint per bank**: `http://localhost:8888/mcp/{bank_id}/`
  — any MCP client (including ZCode) gets retain/recall/reflect as tools.
- Supports 25+ LLM providers **including llama.cpp** via `HINDSIGHT_API_LLM_*`
  env vars; local embeddings via `HINDSIGHT_API_EMBEDDINGS_PROVIDER=local`.
- Windows supported (pip install or Docker; embedded PostgreSQL via pg0).
- `npx @vectorize-io/hindsight-coding-agents install ...` wires coding agents.

## Decision

Adopt **self-hosted Hindsight** as the factory's long-term memory:

- One memory bank per factory project (`bank_id` derived from the repo).
- The **Reflect** stage retains wave/incident learnings; **Brief** stage
  recalls top-k relevant memories into each worker brief.
- Expose the bank's MCP endpoint to interactive agents (ZCode config) so
  human-driven sessions share the same memory as factory runs.
- The server runs with `HINDSIGHT_API_LLM_PROVIDER=llamacpp` pointed at the
  same llama.cpp endpoint as the workers (exact env var names verified at
  implementation; `sfactory doctor` validates).
- Canonical durable state still lives in repo files (`memory/` folder —
  see ADR-0012); Hindsight is the semantic index over it, not the only copy.

## Consequences

- One more local service to run (pip install `hindsight-api`, or Docker);
  `sfactory doctor` checks it and prints the exact start command.
- Memory quality depends on reflection discipline — the pipeline makes
  `retain` mandatory after every wave/incident (saintnikopol's Reflection
  stage, adapted).
- Repo files remain the source of truth; losing the Hindsight DB degrades
  recall, not the factory's ability to run.

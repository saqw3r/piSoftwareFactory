# ADR-0010: Model backend = owner's llama.cpp (Qwen 3.5, 65 536 ctx) + context discipline

- **Status:** Accepted
- **Date:** 2026-09-27
- **Deciders:** Serhii Surnin (owner, "let's use my running llama.cpp with Qwen 3.5 model and max context size: 65536"), ZCode (facilitator)
- **Origin:** owner request

## Context

The owner runs a local **llama.cpp** server (`llama-server`, OpenAI-compatible
API at `http://127.0.0.1:8080/v1` by default) serving a **Qwen 3.5** model
with **max context 65 536** tokens. All factory inference (workers,
reviewers, intake, memory extraction) should run on it — local-first (ADR-0001).

A 65k window is generous but not infinite: pipeline design must respect it,
because a mid-size repo plus tool outputs can overflow quickly, and local
serving degrades sharply past the window.

## Decision

1. `factory.toml` carries the backend config (endpoint URL, model id,
   context window); defaults: `http://127.0.0.1:8080/v1`, ctx `65536`.
   `sfactory init` **probes the live server** (`/v1/models`) to autofill the
   real model id and port instead of guessing.
2. **Context discipline rules** (enforced by the orchestrator):
   - Worker briefs ≤ ~2k tokens: node text + explicit `@file` includes only —
     never "read the repo".
   - Hindsight `recall` top-k injects only relevant memory (ADR-0005).
   - Reviewers are fresh-context per node (no cross-node accumulation).
   - JSONL event streams let the orchestrator track per-run token usage and
     enforce budgets (pi `--mode json`, ADR-0002).
   - Elements are sized 20–30 min of work ("1 element = 1 commit",
     ADR-0007) so no single run needs a huge window.
   - Long artifacts (logs, diffs) are truncated to head+tail with the middle
     elided when fed back as gate feedback.
3. Hindsight's LLM provider is pointed at the same llama.cpp endpoint
   (`HINDSIGHT_API_LLM_*` env vars) so memory extraction is local too.

## Consequences

- `sfactory doctor` verifies: endpoint reachable, model id matches config,
  server reports ≥ configured context (65 536), and warns on mismatch.
- Small-model reality is respected by design: narrow briefs, mechanical
  gates, structured verdicts — the model is never asked to be its own judge
  (esf principle).
- If the owner swaps models/ports later, only `factory.toml` changes.

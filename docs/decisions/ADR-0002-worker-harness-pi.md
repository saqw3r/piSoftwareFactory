# ADR-0002: Worker harness = pi coding agent

- **Status:** Accepted
- **Date:** 2026-09-27
- **Deciders:** Serhii Surnin (owner) — explicit choice among OpenCode / Claude Code / both; ZCode (facilitator)
- **Origin:** user answer to harness question; corroborated by discovery

## Context

Headless workers need an agent CLI that (a) runs non-interactively, (b) can be
pointed at a custom OpenAI-compatible endpoint (the owner's llama.cpp server),
(c) supports tool restriction (read-only reviewers), and (d) emits machine-
parsable output for the orchestrator.

Candidates evaluated during discovery:

- **OpenCode**: first-class llama.cpp provider in `opencode.json`
  (`@ai-sdk/openai-compatible`); the harness mitkox/esf uses (`opencode2`).
- **Claude Code**: routed to llama.cpp via `ANTHROPIC_BASE_URL` proxy; more
  moving parts.
- **pi coding agent** (`@earendil-works/pi-coding-agent`): the harness the
  saintnikopol factory talk itself used; supports:
  - headless one-shots: `pi --print "prompt"` (writes final text to stdout, exits)
  - JSONL event stream: `pi --mode json "prompt" > events.jsonl` (token
    accounting, structured events for the orchestrator)
  - RPC mode (`--mode rpc`) for long-lived controlled sessions
  - tool restriction: `pi --tools read,grep,find,ls --print ...` (read-only
    reviewers)
  - custom OpenAI-compatible providers via `models.json`:
    ```json
    { "providers": { "llamacpp": {
        "baseUrl": "http://127.0.0.1:8080/v1",
        "api": "openai-completions",
        "apiKey": "none",
        "models": [ { "id": "qwen3.5" } ] } } }
    ```
  - `--no-session` for ephemeral runs; `@path` file includes; piped stdin.

## Decision

Use **pi coding agent** as the factory's worker/reviewer harness:

- **Workers**: `pi --mode json` with full edit tools, driven per node brief.
- **Reviewers**: separate fresh-context `pi --print --tools read,grep,find,ls`
  runs (read-only, no shared conversation context).
- The orchestrator (`sfactory`) invokes pi, parses JSONL events, and enforces
  token budgets per run.

## Consequences

- Requires Node.js on the owner's machine (pi is an npm package).
- `models.json` is scaffolded per project (and/or in pi's agent directory)
  pointing at the llama.cpp endpoint — exact location verified at
  implementation time (`sfactory doctor` validates the provider resolves).
- If pi proves unsuitable for a role, `factory.toml` keeps `harness` a
  config key so an alternative harness can be added without redesign.

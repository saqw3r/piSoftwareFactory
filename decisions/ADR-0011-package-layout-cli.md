# ADR-0011: Package name, repo layout, and CLI command set

- **Status:** Proposed
- **Date:** 2026-09-27
- **Deciders:** Serhii Surnin (owner), ZCode (facilitator)
- **Depends on:** ADR-0003 (git one-liner), ADR-0009 (Python primary)

## Context

The package is the distributable unit of the one-liner and the pipeline
engine. Name and layout should be settled before any code.

## Decision (proposed)

1. **Package name: `sfactory`** (verified free on PyPI 2026-09-27, keeping
   the `uvx sfactory init` future open per ADR-0003).
2. **Repo layout** (src layout — import correctness enforced by packaging):

   ```
   F:\SoftwareFactory\
   ├── pyproject.toml            # uv/PEP 517 build; console script `sfactory`
   ├── README.md, LICENSE (MIT), .gitignore
   ├── decisions/                # this ADR folder (governance, ADR-0008)
   ├── src/sfactory/
   │   ├── cli.py                # typer app, command set below
   │   ├── config.py             # factory.toml pydantic model + loader
   │   ├── scaffold/             # Jinja2 templates for `sfactory init`
   │   ├── pipeline/             # intake, graph, dispatch, gates, review, reflect
   │   ├── integrations/         # llama, pi_agent, hindsight, paperclip, jev
   │   └── gate_profiles.py      # per-language gate definitions (ADR-0013)
   └── tests/                    # pytest suite
   ```

3. **CLI command set**:

   | Command | Purpose |
   |---|---|
   | `sfactory init [--auto] [--langs ...]` | scaffold a target project's factory |
   | `sfactory doctor` | verify endpoint/model/ctx, node+pi, hindsight, paperclip, toolchains |
   | `sfactory run "task"` | Intake → …→ Reflect for one task (single-node flow) |
   | `sfactory wave` | dispatch all ready graph nodes across lanes/worktrees |
   | `sfactory gates [--lang X]` | run deterministic gates on demand |
   | `sfactory review [node]` | fresh-context read-only review, verdict recorded |
   | `sfactory release` | assemble `stage` → human-approved merge to `main` |
   | `sfactory reflect` | hindsight retain + reflections.json + proposals |
   | `sfactory paperclip` | provision/inspect the Paperclip company |
   | `sfactory status` | graph, lanes, open PRs, token/budget spend |

## Alternatives considered

- Name `factory` (collision-prone on PyPI), `softfactory`, `aifactory` —
  less precise; `sfactory` is short, unique, and matches the domain.
- Flat layout (`sfactory/` at repo root) — simpler, but invites accidental
  imports from repo root and weakens packaging hygiene.

## Consequences

- MIT license chosen for compatibility with all referenced projects
  (pi, Hindsight, Paperclip, esf are MIT) — owner may override.
- Console script name `sfactory` becomes the stable entry point of the
  one-liner in ADR-0003.

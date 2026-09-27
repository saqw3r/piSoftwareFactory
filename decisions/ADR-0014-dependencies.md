# ADR-0014: Runtime dependencies and build tooling

- **Status:** Proposed
- **Date:** 2026-09-27
- **Deciders:** Serhii Surnin (owner), ZCode (facilitator)
- **Depends on:** ADR-0009 (Python ≥ 3.11, uv), ADR-0003 (uvx one-liner)

## Context

The one-liner resolves the package via `uvx --from git+...`, so the
dependency set must be small, pure-Python where possible, and
Windows-compatible. Heavy integrations should be optional extras, not hard
requirements (the factory must run fully offline with the heuristic intake).

## Decision (proposed)

1. **Core dependencies** (all pure-Python, Windows-safe, permissively
   licensed):

   | Package | Why |
   |---|---|
   | `typer` | CLI (commands in ADR-0011), rich help output |
   | `rich` | tables/status for `doctor`, `status`, `wave` |
   | `pydantic` ≥ 2 | `factory.toml` model, `IntakeDecision`, node/graph models |
   | `tomli-w` | writing `factory.toml` (reading is stdlib `tomllib`) |
   | `jinja2` | scaffold templates (AGENTS.md, configs, briefs) |
   | `httpx` | llama.cpp probe, Hindsight REST, Paperclip API |

2. **Optional extras**:
   - `sfactory[jev]` → `jev`, `typesafe-sdk` (ADR-0004)
   - `sfactory[hindsight]` → `hindsight-client` (typed REST client; the
     server itself is installed/started separately per ADR-0005)

3. **Build/backend**: PEP 517 with `hatchling` (uv-native, no setup.py,
   reliable src-layout and git+uvx builds); Python `requires-python =
   ">=3.11"`; `uv.lock` committed for reproducible dev installs.

4. **External tools** (not pip-managed; checked by `sfactory doctor`):
   git, Node.js ≥ 24.11 (pi + Paperclip), and per-language toolchains
   (dotnet, cargo, go, cmake, npm) as needed by target projects.

5. **Dev deps** (dependency-group, not shipped): `pytest`, `pytest-cov`,
   `ruff`.

## Alternatives considered

- `argparse` instead of `typer` (fewer deps) — rejected: help/UX quality and
  typed commands are worth one pure-Python dependency.
- `click` directly — typer wraps click with less boilerplate.
- `setuptools` — heavier, more failure modes in uvx/git builds.
- Pulling `dspy` for intake — rejected: Jev path needs only
  `jev`+`typesafe-sdk`; DSPy is a lab-level dependency (esf keeps it in
  `tools/brief_lab/`), not a pipeline one.

## Consequences

- Cold `uvx` start downloads ~6 pure-Python wheels — fast, no compiler.
- Everything core is MIT/BSD/Apache licensed — no license audit risk.
- `[jev]`/`[hindsight]` extras keep the default install fully offline.

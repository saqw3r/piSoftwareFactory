# ADR-0009: Primary language = Python; gates and scripts Python-first

- **Status:** Accepted
- **Date:** 2026-09-27
- **Deciders:** Serhii Surnin (owner, "Primary language of the setup should be python"), ZCode (facilitator)
- **Origin:** owner request; platform constraint (win32)

## Context

The factory must be a Python setup (owner requirement), while the projects it
builds span C#, Python, JS, Rust, C++, Go. The owner's machine is **Windows**
(win32, Git Bash). Reference implementations lean on bash (`build.sh` /
`test.sh` in esf, shell preflight scripts in the saintnikopol factory), which
is fragile on Windows.

## Decision

- **`sfactory` is a Python package** (Python ≥ 3.11), built and run with
  `uv` (uvx one-liner per ADR-0003).
- **Gates, preflight, interlock checks, and the lane manager are Python**
  (`python -m sfactory …` / `sfactory …` commands), not bash — Windows-safe
  by construction, single toolchain to maintain.
- Target projects keep their own native build systems (dotnet, cargo, go,
  cmake…); gate profiles merely *invoke* them (see ADR-0013, Proposed).
- Generated per-project helper scripts default to Python; bash/PowerShell
  variants are generated only if a target project already standardizes on
  them.

## Consequences

- One runtime dependency for the factory itself (`uv`/Python); workers still
  need Node.js for pi (ADR-0002) — `sfactory doctor` reports the full
  toolchain matrix.
- Polyglot support = gate profiles, not a rewrite of project tooling.

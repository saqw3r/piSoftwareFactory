# ADR-0001: Scope and concept — local-first AI software factory with a one-liner bootstrap

- **Status:** Accepted
- **Date:** 2026-09-27
- **Deciders:** Serhii Surnin (owner), ZCode (facilitator)
- **Origin:** initial request + discovery phase

## Context

The owner wants to turn agent prompting into a full software development
lifecycle that runs largely unattended ("auto mode"), bootstrapped into any
new project with a single command. Three references define the concept space:

- **saintnikopol / software-factory** (TechLead Conf talk): 8-stage pipeline
  (Intake → Design → Dispatch → Execution → Review → Gates → Release →
  Reflection); "agents run the floor and the human runs the gates"; git
  worktrees per lane; "1 element = 1 commit"; mechanical gates as scripts;
  fresh-context reviewers; a reflection loop that feeds the factory's own
  process.
- **Factory.ai / Software Factory**: SDLC coverage model — Triage → Code-gen →
  Validate → Release → Document → Monitor, with live metrics per stage.
- **mitkox/esf** (Engineering Software Factory, a Machinist fork): Task →
  orchestrator → sandbox → coding agent → **deterministic verification** →
  patch + evidence. Key principle: "Deterministic verification gates; agent
  success alone is not factory success." Repo-owned `build.sh`/`test.sh`;
  `factory.toml` configuration; "DSPy/Jev intake (advisory)".

Constraints from the owner: primary language of the setup is **Python**; the
factory must support **C#, Python, JS, Rust, C++, Go**; it must run against
the owner's **local llama.cpp** server; self-hosted and local-first.

## Decision

Build **`sfactory`** — a Python CLI package that, when run inside any new
project, scaffolds a complete software factory and runs it in auto mode:

1. **8-stage pipeline** (saintnikopol), mapped onto the SDLC coverage model
   (Factory.ai): Intake → Brief → Dispatch → Execute → Gates → Review →
   Release → Reflect.
2. **Deterministic verification gates** (esf): agent claims are never trusted;
   only machine-checkable build/test/lint results advance a node.
3. **Worktree lanes** (saintnikopol): each wave lane works in its own git
   worktree + branch; merge-only history into `main` via a `stage` branch.
4. **Polyglot gate profiles** for C#, Python, JS, Rust, C++, Go.
5. **Human gate only at Release** (merge to `main`); everything else is
   machine-gated.

## Consequences

- The factory is **self-hosted and local-first**: all model calls go to the
  owner's llama.cpp endpoint; no cloud dependency except explicitly optional
  integrations (Jev — see ADR-0004).
- One-liner distribution is a hard requirement (see ADR-0003).
- Python tooling must be Windows-safe (owner's platform is win32): gates and
  scripts are Python, not bash (see ADR-0009).

# ADR-0008: Governance — decisions require owner approval before implementation

- **Status:** Accepted
- **Date:** 2026-09-27
- **Deciders:** Serhii Surnin (owner) — direct instruction; ZCode (facilitator)
- **Origin:** owner instruction after plan review ("All the architectural and
  major product decision should be addressed to me first and only then
  applied if I approve")

## Context

The factory automates development work; the process that builds the factory
itself must not silently make consequential choices. The owner requires a
decision-gated workflow with an auditable trail.

## Decision

1. All architectural and major product decisions are recorded as **ADRs** in
   `decisions/NNNN-title.md` (this folder).
2. Workflow for every new major decision:
   - Write the ADR with `Status: Proposed` → commit.
   - **Submit to the owner** (AskUserQuestion or explicit review request)
     with the decision's essence and alternatives.
   - Apply **only after approval**; flip `Status: Proposed → Accepted` in the
     commit that implements it (or a dedicated `docs:` commit).
   - Rejected proposals get `Status: Rejected` with the owner's reason —
     never deleted (decision history is evidence, esf-style).
3. Decisions already agreed in conversation (ADR-0001..0010) were recorded
   and committed as `Accepted` as part of the approved plan.
4. Git history is part of governance: small commits, `docs:/feat:/chore:`
   prefixes, messages that explain **what and why**.
5. Owner-intercepted steps (never performed silently by the agent):
   creating/pushing the GitHub remote, publishing to any registry, and any
   action with irreversible or outward-facing effects.

## Consequences

- Implementation pauses at decision checkpoints; the agent may batch
  non-major, reversible implementation details without ceremony.
- `decisions/` is committed from day one and ships with the repo, so the
  one-liner scaffold can also seed an ADR folder in target projects
  (factory dogfoods its own governance).
- "Major" means: architecture, tool/harness choice, data formats, security
  posture, distribution, anything irreversible. Typos, copy edits, and
  test additions are not major.

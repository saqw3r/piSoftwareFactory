# ADR-0015: Auto service provisioning — init --auto installs and starts Hindsight and Paperclip

- **Status:** Accepted
- **Date:** 2026-09-28
- **Deciders:** Serhii Surnin (owner) — chose "Auto-start inside init --auto" over a standalone `services setup` command or the manual status quo
- **Depends on:** ADR-0005 (Hindsight), ADR-0006 (Paperclip), ADR-0012 (scaffold scope)

## Context

ADR-0012 scoped `sfactory init` to writing files and instructions ("services
not started without the owner's consent"). After using the one-liner, the
owner expected the whole floor to come up: memory and the management layer
included. The consent question is now answered — the owner explicitly wants
`init --auto` to provision both services.

## Decision (accepted)

1. New `sfactory services` command group: `setup`, `status`, `stop`.
2. `sfactory init --auto` invokes service setup automatically at the end
   (before the doctor run), so the one-liner brings up the entire factory:
   - **Hindsight**: installed into an isolated environment
     (`uv tool install --force hindsight-api`, falling back to an existing
     `hindsight-api` on PATH), started detached with the llamacpp provider
     env (`HINDSIGHT_API_LLM_*`, `HINDSIGHT_API_EMBEDDINGS_PROVIDER=local`)
     pointing at the project's configured backend; PID + log under
     `.factory/run/`.
   - **Paperclip**: `npx --yes paperclipai onboard --yes`, detached, log
     under `.factory/run/`; server expected on :3100.
3. Idempotent and **non-fatal**: an already-running service is detected and
   left alone; a failed download or a missing toolchain degrades to a
   warning with the log path — the scaffold and the pipeline never depend
   on either service (ADR-0005/0006 independence stands).
4. `init` without `--auto` does not touch services; it prints
   `sfactory services setup` as the explicit path.
5. Service start is environment configuration, not a governance decision:
   per-project approval happens once, here — the ADR records it.

## Consequences

- First `init --auto` on a machine downloads hindsight-api (embedded
  pg0 store) and the Paperclip npm package — minutes, not seconds; later
  runs are no-ops (idempotence).
- `sfactory services status` and `doctor` both reflect service state;
  doctor's hindsight/paperclip warnings turn green once this runs.
- The services live beyond individual projects (one Hindsight, one
  Paperclip server, many project banks/companies) — consistent with
  ADR-0005 (per-bank isolation) and ADR-0006 (multi-org).

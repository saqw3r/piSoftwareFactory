# ADR-0022: `provider` means the model source; the brain moves to `brain`

- **Status:** Accepted
- **Date:** 2026-11-06
- **Deciders:** Serhii Surnin (owner), Qoder (implementer)
- **Origin:** owner request — "тоді перейменовуй: backend --> provider, provider --> brain",
  after asking why llama.cpp was missing from `which worker brain?  1. pi 2. omp 3. zcode`
- **Amends:** ADR-0017 decision 2 and ADR-0019 decision 3 (command names only — the
  decisions themselves stand).

## Context

Two words named two things at once. `sfactory provider` switched the *worker
brain* (pi | omp | zcode) while the model-provider question inside
`sfactory backend --source harness-login` asked about *where the model comes
from* — llama.cpp, an endpoint, a subscription. Asked to choose llama.cpp, the
operator went to the command literally named `provider` and found pi, omp and
zcode there, which is a reasonable place to conclude llama.cpp had been left out
of the product. `sfactory backend` was no better: "backend" had meant both the
model source and, via `--harness`, the agent.

## Decision

1. **`sfactory provider` = which provider serves the model.** What the old
   `sfactory backend` did: source × provider × model, ending with one real
   prompt through the agent as proof. `--harness` stays, so a machine nobody has
   configured is still one command.
2. **`sfactory brain` = which agent does the work.** What the old `sfactory
   provider` did (pi | omp | zcode), with the same prove-before-saving rule.
3. **No aliases for the old names.** `sfactory backend` is gone rather than kept
   as a second spelling of the same command — two names for one job is the bug
   being fixed, and a deployment that types the old one gets an error, not a
   silently different meaning.
4. **The old muscle memory fails with the new command in its mouth.** A bare
   positional at the provider door is answered by name: `sfactory provider omp`
   → "'omp' is a worker brain, not a model provider — next command: `sfactory
   brain omp`", a preset → `sfactory provider --source llamacpp -y`, anything
   else → the wizard that lists what answers. Exit code 2, no prompt run. This is
   ADR-0015's bar: the failure carries the cause and the next command, not a log
   file.
5. **`provider:` is labelled wherever it prints.** `sfactory provider --show` and
   `sfactory status` now read `brain: pi · provider: llamacpp · <url> · <model>
   · key: …`, because an unlabelled middle word is exactly the ambiguity that
   started this.

## Consequences

- Every remedy string in the product now says `sfactory provider …`, `sfactory
  brain …` or `sfactory model <id>` — and where a dedicated command exists for
  the advice, it is used instead of the wizard flag (`--model` no longer
  duplicates `sfactory model`).
- Older ADRs (0016–0021) still say `sfactory backend` and, for the brain switch,
  `sfactory provider`. They are records of what was decided at the time and are
  not rewritten; read `sfactory backend` → `sfactory provider`, and `sfactory
  provider <harness>` → `sfactory brain <harness>`.
- Scripts and one-liners copied from an older README or an earlier `--show`
  output break loudly on the first word. On the Z: deployment that means the
  replay line must be re-copied from `sfactory provider --show`.
- `factory.toml` is untouched: `[backend]` stays the on-disk table name, because
  renaming it would invalidate every provisioned machine for a wording win. The
  internal module (`backend_setup.py`) and functions (`run_backend`,
  `run_provider`) keep their names — the surface rename is the user's contract,
  the module layout is not.
- Version 0.3.0: a public command name changed and the old one does not work.

# ADR-0017: Harness choice — pi or omp, three ways to feed the model

- **Status:** Accepted
- **Date:** 2026-10-31
- **Deciders:** Serhii Surnin (owner), Muse Spark (implementer)
- **Origin:** owner request — "А можна дозволити користувачу обирати між опціями — 1) pi (а тоді провести користувача до логіну і вибору моделі), 2) omp (аналогічно) 3) openAI API endpoint with key?"

## Context

ADR-0002 named pi as the worker harness and ADR-0016 generalized the backend to
URL + key, but the choice was still written into the code: the scaffold only knew
how to register a provider in *pi's* `models.json`, and switching meant editing
`factory.toml` by hand with no way to prove the result works. On a foreign
machine that is a support call. `omp` (the owner's second agent CLI) turns out to
read the same provider schema — JSON for pi at `~/.pi/agent/models.json`, YAML
for omp at `~/.omp/agent/models.yml` — so the two are one writer apart.

## Decision

1. `[harness] name` accepts `pi` | `omp`. `harnesses.py` is the single place
   that knows a harness's launcher, agent dir, registry format, model-id spelling
   and login command. Adding a third agent means adding an entry, not editing
   call sites.
2. `sfactory backend` asks for the choice and applies it — harness × source,
   where source is `llama-local` (owner's endpoint, mocked key),
   `openai-endpoint` (URL + key from the environment) or `harness-login`
   (subscription provider, via the agent's own `login`).
   Questions appear only for what no flag supplied, so
   `sfactory backend --harness omp --source llama-local --model X -y` is
   scriptable on a machine nobody has set up before.
3. **The command ends with a real prompt through the chosen agent** and streams
   the answer. A configuration is only saved when that succeeds; a failed check
   leaves `factory.toml` as it was and prints the agent's own error plus the next
   command. Consistent with ADR-0015's self-diagnosing bar: no log file to open.
4. The model list comes from the live endpoint (`GET {base_url}/models`) first,
   then from the agent's own catalog. The user picks from what actually answers
   instead of typing an id that may not exist.
5. The choice flows into the Paperclip seed's `harness` block. Paperclip's
   adapter set is fixed (no `omp_local`), so when the seed asks for omp the
   agents are hired on `pi_local` against the *same* endpoint and model, and
   `provision` says so instead of pretending. omp still runs the pipeline
   through `sfactory run` / `sfactory wave`.

## Consequences

- Two agents, one backend: switching harness is `sfactory backend --harness …`
  and takes ~1 min including the proof.
- Windows-specific facts are now encoded where they belong: harnesses are
  spawned by resolved path (`pi.CMD` cannot be created without the extension),
  and pi's key for a custom endpoint must arrive as `--api-key` at runtime —
  which is why `pi /login` was never required for local llama.cpp, while
  Paperclip's `pi_local` (no flag) still needs a stored credential.
- Per-role harness mixing (omp worker + pi reviewer) is not supported yet —
  `[harness]` stays one value per project.
- `harness-login` skips provider registration on purpose: a subscription
  provider must be proven with the agent's stored credential, so the check runs
  without `--api-key`.

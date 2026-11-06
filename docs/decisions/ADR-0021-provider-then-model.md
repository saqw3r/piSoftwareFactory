# ADR-0021: Provider first, then only that provider's models

- **Status:** Accepted
- **Date:** 2026-11-06
- **Deciders:** Serhii Surnin (owner), Qoder (implementer)
- **Origin:** owner request — "let's allow to set up provider, and when allow to set up
  model from the list of available for that provider. Let's add llama provider — and it
  should show use list of models which are available now"
- **Amends:** ADR-0017 decision 4 (model list) and its last consequence (harness-login
  skips registration).

## Context

`sfactory backend --source harness-login` asked one question — pick a model — and
answered it with `pi --list-models`: ~950 ids over 14 providers, in a fixed-width
table with no JSON mode. Two failures came from that list, both observed live:

- an operator picks `ovh-free/gpt-oss-120b` (a freeflow id) because it is in the
  list, and the factory points it at the llama.cpp endpoint — the provider the id
  belongs to was never asked, so nothing could keep the two in step;
- llama.cpp was effectively unchooseable: pi spells the local server `llama.cpp`,
  factory.toml stores the block that works as `llamacpp`, and a provider question
  did not exist, so the loaded GGUFs were never shown as a list.

pi's built-in `llama.cpp` provider takes no URL and waits silently when nothing
listens; the factory's `llamacpp` block fails fast. Offering both spellings as one
choice would hand the operator the hanging one half the time.

## Decision

1. **The catalog carries its provider.** `harnesses.harness_catalog(name)` returns
   `(provider, model)` pairs — parsed from pi's table (two-or-more-spaces split,
   header and warning rows skipped, pi's own order kept) and from omp's
   `models ls --json`. `harness_models` and `harness_providers` are derived from
   it, so one seam feeds the model list, the provider list and the counts.
2. **The wizard asks provider, then model.** `pick_provider` lists providers busiest
   first; each row says how it is served (a preset's label, or "N model(s) in X's
   catalog"). The model question then sees only that provider's ids.
3. **One spelling per machine.** `PROVIDER_ALIASES` folds pi's `llama.cpp` onto the
   factory's `llamacpp` before offering or matching, so a provider appears once and
   catalog lookup works whichever spelling the harness printed. Matching only the
   stored spelling is what made the wizard offer zero models for a provider it had
   just listed.
4. **A llama choice lists what answers now.** `models_for_provider` probes the
   preset's `/v1/models` first, logs `N model(s) loaded on <url> right now`, then
   appends ids the registry names but the server does not serve, saying they are not
   served. When the port does not answer it names the URL and says the ids below are
   unproven — the server is the thing to fix, not the model. `:8080` is back in
   `LOCAL_PORTS` so the live-port menu can find that server at all.
5. **A provider that is an endpoint gets registered.** When the chosen provider
   matches a preset, `harness-login` writes the provider block and keeps
   `agent_credential = false` (the mocked key is what a local llama.cpp accepts);
   a keyless subscription provider still gets no `--api-key` at all. zcode stays
   outside both: it has no registry, so no preset may claim it.

## Consequences

- Picking a model cannot silently pick a provider: the pair is recorded together,
  and `sfactory model` re-lists only the configured provider's ids.
- `sfactory backend --source harness-login --provider llama.cpp` works from pi's
  own spelling and saves the alias that answers.
- A provider pi reports but the factory does not know (openrouter, kilo, cline) is
  offered as an agent credential with no URL — correct, because those come from the
  agent's login, not from an endpoint block.
- The pi table parser is tied to a layout the CLI may change; a row that no longer
  splits into two cells is silently dropped rather than mis-parsed, and the wizard
  then says "nothing answered, type the id you know".

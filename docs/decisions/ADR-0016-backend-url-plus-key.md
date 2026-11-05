# ADR-0016: Generalized backend (URL + key, one per project)

- **Status:** Accepted
- **Date:** 2026-10-06
- **Deciders:** Serhii Surnin (owner), Muse Spark (implementer)
- **Origin:** owner request — "except local llm, can I replace it with or use additionally for some sub-agents OpenAI API keys? … generalize this usage, default stays llama.cpp's url"

## Context

ADR-0010 fixed the backend to the owner's llama.cpp (local-first) and ADR-0002
hardcoded pi's `--provider llamacpp` with a mocked `"apiKey": "none"`. pi itself
already supports any OpenAI-compatible provider via `models.json`, so the factory
was artificially limited to one local endpoint with no key handling.

## Decision

1. `factory.toml [backend]` is generalized as URL + key (all OpenAI-compatible):
   `provider` + `base_url` + `model` + `api` + `api_key_env`. The secret lives
   only in the environment — never in `factory.toml` or the scaffold.
2. `api_key_env` empty → mocked key `"none"` (llama.cpp default). Set →
   resolved from the environment and written into pi's provider block.
3. **One backend per project** (v1 scope): `run`/`wave`/`review` all use it.
   Scaffold defaults stay llama.cpp (`provider = "llamacpp"`), so existing
   projects and old `factory.toml` files (missing keys → pydantic defaults)
   keep working unchanged.
4. `sfactory init` (no `--force` needed) re-registers the configured provider
   into pi's agent dir (merged — other provider blocks survive) and `doctor`
   verifies reachability plus key presence.

## Consequences

- Swap per project via 5 TOML lines + `$OPENAI_API_KEY` + `sfactory init`
  (documented in README "Model backend").
- Per-role routing (local workers + OpenAI reviewer) is explicitly deferred —
  provider blocks already merge, so it lands without redesign when wanted.
- Multi-key rotation (parallel-lane 429s) is deferred until actually observed.

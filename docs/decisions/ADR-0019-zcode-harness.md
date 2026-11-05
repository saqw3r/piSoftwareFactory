# ADR-0019: zcode as a third harness — agent-credential by design

- **Status:** Accepted
- **Date:** 2026-11-03
- **Deciders:** Serhii Surnin (owner), ZCode Muse (implementer)
- **Origin:** owner request — "could we add zcode integration as one more option to the sfactory? I would like to check how easily it could be integrated and how good is it for real coding"

## Context

ADR-0017 made the harness a registry entry ("adding a third agent means adding
an entry, not editing call sites"). zcode — the ZCode Desktop app's bundled CLI
(`zcode.cjs`, v0.17.1 at the time of writing) — exposes the pieces the factory
needs: `-p` headless prompts, `--mode build|edit|plan|yolo|guarded`, `--json`,
`--cwd`, `--attach`, `login [zai|bigmodel]`. What it does not expose is any way
to steer the model: there is no `--model` flag, no provider registry file the
factory may write, and its model comes from its own saved settings or its own
`zcode login` (observed live: it dialed the owner's local llama endpoint from
saved settings without any sfactory involvement).

## Decision

1. `[harness] name` accepts `pi` | `omp` | `zcode`. zcode is an
   **agent-credential harness**: every `sfactory backend --source` collapses to
   the `harness-login` path — no provider registration, no key on the command
   line, and the proof is one real one-shot prompt (`-p "Reply with exactly:
   OK" --json --mode plan`).
2. `[backend]` is recorded but **nominal** for zcode. There is no per-project
   model selection; switching models happens in zcode's own settings or login.
   `sfactory status`/`backend --show` say so instead of pretending.
   Verified 2026-11-04: the headless CLI exposes no `--model` flag, no model
   env var (five `process.env` reads total, none model-related), and ignores a
   hand-written `defaultModelSelection` in `provider_config.json` — that field
   is written by the app through an integrity-checked repository. The default
   model must therefore be set once in the ZCode app's model picker; headless
   runs follow it from then on.
3. Workers run `-p <instruction> --json --mode yolo --attach <brief.md>`;
   reviewers run `-p <prompt> --mode plan` (read-only by construction — zcode's
   `--disallowed-tools` is not used because its tool names are not documented).
   The brief rides in `--attach`: pi's `@file` include is pi syntax.
4. The CLI has no PATH shim; `spawn_argv("zcode")` launches the Desktop bundle's
   `zcode.cjs` with a node prefix, preferring a future `zcode` shim on PATH if
   one appears. Everything that builds argv goes through this one prefix.
   The headless CLI also resolves its models through **two** config files the
   Desktop app injects as env vars, both missing from a plain terminal — the
   first outside-the-app run dies with "无法定位 CLI ZCode Built-in Provider":
   `ZCODE_PERSONAL_PROVIDER_CONFIG_FILE` (the user's own providers,
   `~/.zcode/v2/provider_config.json`) and `ZCODE_BUILTIN_PROVIDER_CONFIG_FILE`
   (the CDN-served provider catalog cached per app version under
   `~/.zcode/v2/runtime/provider/…`; newest mtime wins, old versions linger).
   `spawn_argv` injects both defaults when unset and never overrides the
   environment. Verified by reproducing the failure with every `ZCODE_*` var
   stripped and restoring resolution with only these two set.
5. zcode's `--json` output schema is **not pinned yet**. The parser makes a
   best-effort final-text extraction (single JSON object with a text-shaped
   field, else raw output) and leaves token/tool accounting at zero. Once a
   live run's output is captured, a dedicated parser lands next to
   `_parse_events` — dispatch meanwhile decides done/blocked from git facts and
   exit code, which never depended on the event schema.

## Consequences

- Three brains, one backend command: `sfactory backend --harness zcode -y`
  proves the setup in seconds when zcode's model answers, and fails with
  zcode's own error (remedy: check the ZCode app settings, or `zcode login`)
  when it does not.
- Paperclip is unchanged: its adapter set is fixed and has no `zcode` adapter,
  so company agents are still hired on `pi_local` against the seed's
  provider/model; zcode runs the pipeline through `sfactory run`/`wave`.
- The Start plan (z.ai) is sized for one interactive human. Factory waves are
  2–8 lanes × worker+reviewer — expect the plan's quota window to throttle
  mid-wave on heavy use. For sustained factory duty, pay-per-token (DashScope/
  Z.AI API) or local llama.cpp remain the dependable backends; zcode suits
  single-lane runs and reviews.
- The CLI ships inside the Desktop app and is minified — flags can shift on app
  updates. `spawn_argv` isolates the one launch point; a formal standalone CLI
  distribution would make this integration stable.
- Known gap carried on purpose: token usage and `tool_calls` read 0 for zcode
  until the `--json` schema is captured live (ADR-0019 follow-up).

# piSoftwareFactory

A local-first, self-hosted **AI software factory** you bootstrap into any new
project with a one-liner and run in full auto mode on your own machine.

```sh
uvx --from git+https://github.com/saqw3r/piSoftwareFactory sfactory init --auto
```

(From a local checkout instead: `uvx --from F:\SoftwareFactory sfactory init --auto`.)

- **Primary language**: Python — the factory tooling itself; it builds projects in **C#, Python, JS, Rust, C++, Go**
- **Model backend**: any OpenAI-compatible endpoint as URL + key — default is your local llama.cpp server (`http://127.0.0.1:8080/v1`, Qwen 3.5, 65 536 ctx, mocked key `none`) so nothing leaves your machine; swap to OpenAI per project via `factory.toml` (see Model backend below)
- **Worker harness**: [pi coding agent](https://github.com/badlogic/pi-mono) headless (`--mode json` workers, `--print --tools read,grep,find,ls` reviewers)
- **Multi-agent management**: [Paperclip](https://github.com/paperclipai/paperclip) companies (Architect → per-lane Workers → Reviewer)
- **Memory**: [Hindsight](https://github.com/vectorize-io/hindsight) (retain / recall / reflect, per-repo banks, MCP endpoint)
- **Intake decisions**: [Jev](https://pypi.org/project/jev/) typed, confidence-scored, abstaining (optional; deterministic heuristic fallback)

## Getting started

### 0. Prerequisites (once per machine)

| Need | Install |
|---|---|
| uv + Python 3.11+ | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| git | `winget install Git.Git` (or `choco install git`) |
| Node.js ≥ 24.11 + pi harness | `winget install OpenJS.NodeJS.LTS` then `npm install -g @earendil-works/pi-coding-agent` |
| llama.cpp server | your own `llama-server` (example below); `sfactory init` probes it and fills in model + ctx |

**Local llama.cpp setup (example, once per machine):**

```powershell
# 1. Get a binary: https://github.com/ggerganov/llama.cpp/releases
#    (or via winget, if a llama.cpp package is available there)
# 2. Get a GGUF model, e.g. Qwen3 8B Q4_K_M (~5 GB) from Hugging Face into .\models\
# 3. Serve it with the full 65k context the factory expects:
.\llama-server.exe -m .\models\qwen3-8b-q4_k_m.gguf --host 127.0.0.1 --port 8080 -c 65536
# 4. Verify (new terminal):
invoke-restmethod http://127.0.0.1:8080/v1/models | convertto-json -depth 5
# 5. Scaffold — init probes /v1/models and fills in factory.toml for you:
cd C:\work\my-project
sfactory init --auto
```

Notes: keep `-c 65536` (doctor warns if server ctx < configured ctx); GPU
offload flags (`-ngl 99`) depend on your build — CPU-only works, just slower.
Swap the `-m` file for any Qwen 3.x GGUF you prefer; `init` picks up the real
model id automatically.
| optional: Hindsight memory | auto-provisioned by `sfactory init --auto`; manual setup below |
| optional: Paperclip | auto-provisioned by `sfactory init --auto`; manual setup below |

### 1. Install the CLI (once per machine, optional)

```sh
uv tool install git+https://github.com/saqw3r/piSoftwareFactory
# local checkout instead:  uv tool install --force F:\SoftwareFactory
sfactory --version
```

Skip this entirely if you prefer — the `uvx --from …` one-liner above runs
without any install (uv caches it after first use).

### 2. Bootstrap a project (once per project)

```powershell
cd C:\work\my-project
sfactory init --auto
```

This detects your languages, probes llama.cpp (fills in model id + 65 536
ctx), registers the pi provider, scaffolds ~17 files, makes a baseline
commit, **installs and starts Hindsight + Paperclip** (ADR-0015 — skipped
without `--auto`), and **finishes with a doctor run** — so the one-liner
ends with evidence, not hope. Expected tail:

```
services:
  hindsight: started
  paperclip: started / starting — check .factory/run/paperclip.log
environment check:
  llama.cpp backend  ok  models=['qwen3.5-9b']; ctx=65536
  hindsight memory   ok  http://127.0.0.1:8888
```

Anything the doctor flags yellow is optional (Hindsight/Paperclip) or a
missing toolchain whose gates will simply skip with evidence.

### 3. Day-to-day work

**One task, full pipeline** (Intake → brief → lane worktree → pi worker →
gates → fresh-context review → reflect):

```powershell
sfactory run "Add rate limiting to the login endpoint"
```

**Batch of tasks, parallel lanes** — queue them as they come to mind, then
dispatch as one wave (one git worktree per lane):

```powershell
sfactory run "Add is_even helper + tests" --queue
sfactory run "Document the public API in README" --queue
sfactory run "Fix flaky checkout test" --queue
sfactory wave          # executes every ready node, then reviews each
```

**While a wave runs / after it:**

```powershell
sfactory status                 # graph, worktrees, last gate evidence
sfactory gates                  # deterministic gates on demand
sfactory gates --lang python    # one language only
sfactory review n003            # fresh-context review of one node
```

**Release** — the only human gate. The orchestrator never merges on its own:

```powershell
sfactory release                # merges done lanes into `stage`, shows the plan
# …you review the stage branch…
sfactory release --approve      # human-approved merge stage → main, dated tag
```

**Reflections** happen automatically after every run/wave; add context
manually any time — incidents become gates:

```powershell
sfactory reflect --summary "checkout wave shipped clean" --incident "uv lockfile drift blocked lane 2"
```

### A typical day, end to end

```powershell
cd my-project
sfactory status                          # where did we leave off? (memory/handover too)
sfactory run "Implement retry with backoff in http client" --queue
sfactory run "Add unit tests for retry" --queue
sfactory wave                            # workers go brrr on llama.cpp, gates decide
sfactory release                         # stage assembled; human gate message
git diff main..stage                     # you review
sfactory release --approve               # merge + tag release-20260928-…
```

### Model backend: local llama.cpp vs OpenAI (one backend per project)

The backend is a generalized OpenAI-compatible endpoint (`factory.toml [backend]`:
`provider` + `base_url` + `model` + `api_key_env`). Scaffolding defaults to
local llama.cpp (`provider = "llamacpp"`, mocked key) — nothing leaves your
machine. To swap one project to OpenAI (one key, secret stays in env):

```toml
# factory.toml
[backend]
provider = "openai"
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api = "openai-completions"
api_key_env = "OPENAI_API_KEY"
```

```powershell
$env:OPENAI_API_KEY="sk-..."
sfactory init      # re-registers the pi provider (safe without --force)
sfactory doctor    # should show openai backend ok + api key ok
```

Swap back by restoring `provider = "llamacpp"`,
`base_url = "http://127.0.0.1:8080/v1"`, `api_key_env = ""` + `sfactory init`.
Re-running `init` without `--force` never overwrites your scaffolded files —
it only re-registers the pi provider and re-probes the backend.

### Project languages: changing the primary language

The factory tooling itself is always Python; `languages` in `factory.toml`
is what *your project* is built in — it decides which deterministic gates run
and what Intake assumes. **The first entry is the primary language**: when
Intake can't tell from the task text, it falls back to `languages[0]`.

```powershell
# At scaffold time — force instead of auto-detect:
sfactory init --langs python,go

# Afterwards — edit factory.toml (primary first):
#   languages = ["go", "python"]
sfactory gates          # all listed languages, in order
sfactory gates --lang go   # one language only
```

Per-task override without touching the config:

```powershell
sfactory run "port the parser to Go" --lang go
```

Auto-detect looks for marker files (`*.csproj`/`*.sln`, `pyproject.toml`,
`package.json`, `Cargo.toml`, `CMakeLists.txt`, `go.mod`) — unknown values in
`--langs` are rejected, valid: `csharp, python, js, rust, cpp, go`. Note the
"Languages active in this repo" list in `AGENTS.md` is a scaffold-time
snapshot: re-running `init` won't touch it without `--force`, so update that
list by hand when you change `factory.toml`.

### Optional services (auto-provisioned with `--auto`)

Services are **never required**: any failure degrades to a warning with a
log path (`.factory/run/*.log`) and the pipeline keeps running (ADR-0015).
Manage them any time with:

```powershell
sfactory services setup   # install + start Hindsight and Paperclip (idempotent)
sfactory services status  # up / down + pids per service
sfactory services stop    # stop factory-started instances
```

#### Hindsight memory

- **Via the factory (recommended):** `sfactory init --auto` or
  `sfactory services setup` installs `hindsight-api` into an isolated uv
  tool env if missing, then starts it detached (PID + log in
  `.factory/run/`) with the LLM provider pointed at your `factory.toml`
  backend and local embeddings. Idempotent — an already-running server is
  left alone.
- **Manually:** `pip install hindsight-api` (or
  `uv tool install hindsight-api`), then run it with the backend from
  `factory.toml`:

```powershell
$env:HINDSIGHT_API_LLM_PROVIDER="llamacpp"
$env:HINDSIGHT_API_LLM_API_BASE="http://127.0.0.1:8080/v1"
$env:HINDSIGHT_API_LLM_MODEL="qwen3.5-9b"
$env:HINDSIGHT_API_LLM_API_KEY="none"
$env:HINDSIGHT_API_EMBEDDINGS_PROVIDER="local"
hindsight-api
# API: http://localhost:8888 — UI: http://localhost:9999
```

  The generated `hindsight.bootstrap.md` in your project repeats these steps
  with your actual model filled in, plus a Docker alternative. Without
  Hindsight the factory runs normally — recall just returns empty and memory
  files stay the source of truth.

#### Paperclip

- **Via the factory (recommended):** `sfactory init --auto` or
  `sfactory services setup` runs `npx paperclipai onboard --yes
  --no-install-service` non-interactively (first run downloads the package),
  then starts the server if onboard didn't (`paperclipai run` detached on
  Windows, background service on POSIX). An existing `~/.paperclip` instance
  is preserved untouched. `sfactory services status` tells you when `:3100`
  answers; `sfactory paperclip` then shows the company plan seeded from
  `paperclip.company.json`.
- **Manually:**

```powershell
npx paperclipai onboard --yes   # one-time config; starts the server
npx paperclipai run             # if the server isn't up afterwards
# server: http://127.0.0.1:3100
```

  Without Paperclip, `sfactory` itself is the orchestrator — the management
  layer is purely optional.

### Troubleshooting

- **`sfactory` not recognized** → open a new terminal after `uv tool install`
  (PATH is snapshotted per shell), or reinstall:
  `uv tool install --force git+https://github.com/saqw3r/piSoftwareFactory`.
- **doctor fails on the backend** → is the server up at the URL in `factory.toml` (`llama-server` for local, `https://api.openai.com/v1` + `$OPENAI_API_KEY` for OpenAI)?
- **gates BLOCKED** → that's the system working; the evidence JSON in
  `.factory/run/` names the failing step. Fix and re-run.

### Updating and uninstalling

**CLI** (installed as uv tool `pisoftwarefactory`):

```powershell
uv tool install --force git+https://github.com/saqw3r/piSoftwareFactory  # update to latest
sfactory --version                                                        # verify
uv tool uninstall pisoftwarefactory                                       # uninstall
```

**Scaffold inside a project** (there is no remove command — it's just files
plus git branches):

```powershell
sfactory services stop                     # stop factory-started Hindsight/Paperclip
git worktree remove --force .factory/worktrees/L1  # per lane (see `git worktree list`)
git branch -D lane/L1 stage                # lane branches + stage, if created
# then delete: AGENTS.md, factory.toml, launch.graph.json, .factory/,
#   memory/, hindsight.bootstrap.md, paperclip.company.json — and commit
```

Optional: remove the provider block the scaffold added to
`~/.pi/agent/models.json` (a backup sits at `models.json.bak`).


## The pipeline (8 stages)

**Intake → Brief → Dispatch → Execute → Gates → Review → Release → Reflect**

- Intake classifies every task into a node of `launch.graph.json` (kind, risk
  tier, lane, blockers) — Jev when configured, heuristics otherwise.
- Workers execute elements on **lane git worktrees**: *1 element = 1 commit*,
  20–30 minutes each, docs committed with code.
- **Gates are deterministic** and per-language; agent claims are never
  trusted. Skip is recorded as evidence, never a silent pass. Gate failures
  loop back into the worker with the truncated output attached.
- Review is fresh-context and read-only; verdicts are `clean | nits | blockers`.
- **Release is the only human gate**: waves accumulate on `stage`; `main`
  moves only via `sfactory release --approve`. The orchestrator never merges
  on its own — not in auto mode, not ever.
- Reflect writes `memory/reflections.json` + handover state and retains the
  wave in Hindsight; incidents become gates ("a prompt is forgotten on the
  fortieth turn; a script is not").

| Language | Detect | Gate steps |
|---|---|---|
| C# | `*.csproj`/`*.sln` | `dotnet build` → `dotnet test` |
| Python | `pyproject.toml` | `uv sync` → `ruff check` → `pytest` |
| JS/TS | `package.json` | `npm ci` → `npm run build` → `npm test` |
| Rust | `Cargo.toml` | `cargo fmt --check` → `clippy -D warnings` → `test` |
| C++ | `CMakeLists.txt` | `cmake --preset` → build → `ctest` |
| Go | `go.mod` | `go vet` → `go build` → `go test` |

## What `sfactory init` creates

`AGENTS.md` (the factory constitution) · `factory.toml` (backend, lanes,
gates, memory, auto flag) · `launch.graph.json` (the DAG) ·
`.factory/gates/` (preflight, interlock check) · `.factory/lanes/` (worktree
manager) · `.factory/briefs/` · `memory/` (reflections, proposals,
decisions, handover, open questions) · `hindsight.bootstrap.md` ·
`paperclip.company.json`. Everything is additive and idempotent (`--force`
to overwrite).

## Commands

| Command | Purpose |
|---|---|
| `sfactory init [--auto] [--langs …]` | scaffold the factory into a project |
| `sfactory doctor` | verify backend model/ctx + api key, node+pi, hindsight, paperclip, toolchains |
| `sfactory run "task"` | one task through the whole pipeline |
| `sfactory wave` | dispatch all ready nodes across lanes |
| `sfactory gates [--lang X]` | run deterministic gates on demand |
| `sfactory review [node]` | fresh-context review, verdict recorded |
| `sfactory release [--approve]` | assemble `stage`; merge to `main` only with `--approve` |
| `sfactory reflect [--summary …] [--incident …]` | retain learnings |
| `sfactory paperclip` | provision/inspect the management company |
| `sfactory status` | graph, worktrees, last gate evidence |

## One-time services (all optional, all local)

- **Hindsight memory**: `sfactory services setup` (or manual pip/Docker —
  see Optional services above and the generated `hindsight.bootstrap.md`).
  LLM provider = the same backend as the factory. Without it the factory
  runs and memory files stay complete — recall just returns empty.
- **Paperclip**: `sfactory services setup` (or `npx paperclipai onboard
  --yes` manually; Node 24.11+), then `sfactory paperclip`. Without it,
  `sfactory` is the orchestrator.

## Context discipline (65k window)

Briefs ≤ 2k tokens with explicit `@file` includes; Hindsight recall top-k
injected; fresh-context reviewers; per-run token accounting from pi's JSON
events; gate feedback truncated head+tail.

## Governance

Every architectural and major product decision is recorded as an ADR in
[`decisions/`](decisions/) (ADR-0001…0016) — proposed, approved by the
owner, then implemented. See `ADR-0008-governance.md`. Scaffolded projects
inherit the pattern (`memory/decisions.md`).

## Development

```sh
uv sync          # install with dev group
uv run pytest    # 27 offline tests (no server needed)
uv run sfactory doctor
```

MIT — © 2026 Serhii Surnin

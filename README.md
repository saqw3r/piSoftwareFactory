# piSoftwareFactory

A local-first, self-hosted **AI software factory** you bootstrap into any new
project with a one-liner and run in full auto mode on your own machine.

```sh
uvx --from git+https://github.com/<you>/piSoftwareFactory sfactory init --auto
```

(`--from F:/SoftwareFactory` works locally before the repo is published.)

- **Primary language**: Python — the factory tooling itself; it builds projects in **C#, Python, JS, Rust, C++, Go**
- **Model backend**: your running llama.cpp server (default `http://127.0.0.1:8080/v1`, Qwen 3.5, 65 536 ctx) — nothing leaves your machine
- **Worker harness**: [pi coding agent](https://github.com/badlogic/pi-mono) headless (`--mode json` workers, `--print --tools read,grep,find,ls` reviewers)
- **Multi-agent management**: [Paperclip](https://github.com/paperclipai/paperclip) companies (Architect → per-lane Workers → Reviewer)
- **Memory**: [Hindsight](https://github.com/vectorize-io/hindsight) (retain / recall / reflect, per-repo banks, MCP endpoint)
- **Intake decisions**: [Jev](https://pypi.org/project/jev/) typed, confidence-scored, abstaining (optional; deterministic heuristic fallback)

## Getting started

### 0. Prerequisites (once per machine)

| Need | Install |
|---|---|
| uv + Python 3.11+ | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| git | winget/choco/your usual way |
| Node.js ≥ 24.11 + pi harness | `npm install -g @earendil-works/pi-coding-agent` |
| llama.cpp server | your own `llama-server` (e.g. `-m qwen3.5-9b.gguf --port 8080`); `sfactory init` probes it and fills in model + ctx |
| optional: Hindsight memory | generated `hindsight.bootstrap.md` has exact commands |
| optional: Paperclip | `npx paperclipai onboard --yes` |

### 1. Install the CLI (once per machine, optional)

```sh
uv tool install git+https://github.com/<you>/piSoftwareFactory
# local checkout instead:  uv tool install --force F:\SoftwareFactory
sfactory --version
```

Skip this entirely if you prefer — the `uvx --from …` one-liner above runs
without any install (uv caches it after first use).

### 2. Bootstrap a project (once per project)

```powershell
cd Z:\WorkSources\zen\my-project
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

### Optional services (auto-provisioned with `--auto`)

- **Hindsight memory** — `sfactory init --auto` **installs and starts it for
  you** (isolated uv env, llamacpp provider, detached process; idempotent).
  Manage it with `sfactory services setup | status | stop`; manual
  instructions remain in the generated `hindsight.bootstrap.md`.
- **Paperclip** — `init --auto` also runs `npx paperclipai onboard --yes`
  non-interactively (first run downloads the package; `sfactory services
  status` tells you when :3100 is up). Then `sfactory paperclip` shows the
  company plan. Without it, `sfactory` itself is the orchestrator.
- Services are **never required**: any failure degrades to a warning with a
  log path (`.factory/run/*.log`) and the pipeline keeps running
  (ADR-0015).

### Troubleshooting

- **`sfactory` not recognized** → open a new terminal after `uv tool install`
  (PATH is snapshotted per shell), or reinstall: `uv tool install --force F:\SoftwareFactory`.
- **doctor fails on the backend** → is `llama-server` up at the URL in `factory.toml`?
- **gates BLOCKED** → that's the system working; the evidence JSON in
  `.factory/run/` names the failing step. Fix and re-run.


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
| `sfactory doctor` | verify llama.cpp model/ctx, node+pi, hindsight, paperclip, toolchains |
| `sfactory run "task"` | one task through the whole pipeline |
| `sfactory wave` | dispatch all ready nodes across lanes |
| `sfactory gates [--lang X]` | run deterministic gates on demand |
| `sfactory review [node]` | fresh-context review, verdict recorded |
| `sfactory release [--approve]` | assemble `stage`; merge to `main` only with `--approve` |
| `sfactory reflect [--summary …] [--incident …]` | retain learnings |
| `sfactory paperclip` | provision/inspect the management company |
| `sfactory status` | graph, worktrees, last gate evidence |

## One-time services (all optional, all local)

- **Hindsight memory**: see the generated `hindsight.bootstrap.md` (pip or
  Docker; LLM provider = the same llama.cpp). Without it the factory runs
  and memory files stay complete — recall just returns empty.
- **Paperclip**: `npx paperclipai onboard --yes` (Node 24.11+), then
  `sfactory paperclip`. Without it, `sfactory` is the orchestrator.

## Context discipline (65k window)

Briefs ≤ 2k tokens with explicit `@file` includes; Hindsight recall top-k
injected; fresh-context reviewers; per-run token accounting from pi's JSON
events; gate feedback truncated head+tail.

## Governance

Every architectural and major product decision is recorded as an ADR in
[`decisions/`](decisions/) (ADR-0001…0014) — proposed, approved by the
owner, then implemented. See `ADR-0008-governance.md`. Scaffolded projects
inherit the pattern (`memory/decisions.md`).

## Development

```sh
uv sync          # install with dev group
uv run pytest    # 25 offline tests (no server needed)
uv run sfactory doctor
```

MIT — © 2026 Serhii Surnin

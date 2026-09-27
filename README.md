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

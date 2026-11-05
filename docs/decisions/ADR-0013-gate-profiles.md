# ADR-0013: Polyglot gate profiles (C#, Python, JS, Rust, C++, Go)

- **Status:** Accepted
- **Date:** 2026-09-27 (approved by owner same day, as written)
- **Deciders:** Serhii Surnin (owner), ZCode (facilitator)
- **Depends on:** ADR-0001 (polyglot requirement), ADR-0009 (Python-first gates)

## Context

"Deterministic verification gates; agent success alone is not factory
success" (esf). The factory must build and test six ecosystems. Gates must
be Windows-safe, incremental to run, and gracefully skip when a toolchain or
subsystem is absent.

## Decision (accepted)

Each language has a **gate profile**: ordered steps, each with
`detect` (does it apply), `run` (command), and `fix_hint` (feedback given to
the worker on failure). `sfactory init` activates profiles by marker-file
detection; `sfactory gates` runs active profiles (or `--lang`-selected ones).

| Language | Detect (marker) | Steps |
|---|---|---|
| C# | `*.csproj` / `*.sln` | `dotnet build -c Release` → `dotnet test` |
| Python | `pyproject.toml` or `requirements*.txt` | `uv sync` (fallback `pip install -e .`) → `ruff check .` (if configured) → `pytest` |
| JS/TS | `package.json` | `npm ci` (fallback `npm install`) → `npm run build` (if defined) → `npm test` (if defined); pnpm/bun respected if lockfile present |
| Rust | `Cargo.toml` | `cargo fmt --check` → `cargo clippy -- -D warnings` → `cargo test` |
| C++ | `CMakeLists.txt` / `CMakePresets.json` | `cmake --preset <default>` → `cmake --build --preset <default>` → `ctest --preset <default>` |
| Go | `go.mod` | `go vet ./...` → `go build ./...` → `go test ./...` |

Rules encoded in the gate runner:

1. **Skip-with-evidence**: a step whose toolchain is missing or whose
   trigger (e.g. `npm run build` undefined) is absent is recorded as
   `skipped:<reason>` — never silently passed (saintnikopol incident:
   "skipped checks looking like passes").
2. **Failures are evidence**: on failure, gate output (head+tail truncated
   per ADR-0010) is attached to the node and looped back to the worker.
3. **Timeouts**: every step has a default timeout (10 min, configurable in
   `factory.toml`).
4. **Fresh state**: steps run in the lane's worktree; builds never share
   artifacts across lanes.
5. Extensible: projects can add steps in `factory.toml`
   (`[gates.<lang>.extra]`).

## Alternatives considered

- Repo-owned `build.sh`/`test.sh` only (esf style) — kept as an *additional*
  hook (`factory.toml` may point to project scripts), but not the default,
  since bash is not Windows-safe (ADR-0009).
- Containerized gates — rejected for now (local-first, zero-Docker
  default); revisit as a future ADR if hermeticity becomes a problem.

## Consequences

- C++ gates assume CMake presets; non-CMake C++ projects need a
  `factory.toml` override (documented in the scaffold README section).
- `sfactory doctor` reports the toolchain matrix (dotnet/uv/node/cargo/
  cmake/go versions or "missing") so gate skips are predictable.

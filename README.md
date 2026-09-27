# sfactory

A local-first, self-hosted **AI software factory** you can bootstrap into any new
project with a one-liner, and run in full auto mode on your own machine.

- **Bootstrap**: `uvx --from git+https://github.com/<you>/sfactory sfactory init --auto`
- **Primary language**: Python (the factory tooling itself; it builds projects in C#, Python, JS, Rust, C++, Go)
- **Model backend**: your running llama.cpp server (e.g. Qwen 3.5, 65 536 ctx) — nothing leaves your machine
- **Multi-agent management**: [Paperclip](https://github.com/paperclipai/paperclip) companies (Architect → Workers → Reviewer)
- **Memory**: [Hindsight](https://github.com/vectorize-io/hindsight) (retain / recall / reflect, per-repo banks)
- **Intake decisions**: [Jev](https://pypi.org/project/jev/) typed, confidence-scored decisions (optional; heuristic fallback)
- **Worker harness**: [pi coding agent](https://github.com/badlogic/pi-mono) headless (`pi --print`, `pi --mode json`)

> Status: **bootstrap phase** — architecture decisions are being recorded in
> [`decisions/`](decisions/) as ADRs and implemented one approved decision at a time.

## Governance

All architectural and major product decisions live in `decisions/NNNN-*.md`.
Decisions are proposed first, applied only after owner approval, and flipped to
`Accepted` in a follow-up commit. See `decisions/ADR-0008-governance.md`.

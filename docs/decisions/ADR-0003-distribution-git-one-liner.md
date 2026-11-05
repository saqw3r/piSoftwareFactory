# ADR-0003: Distribution = git-based one-liner

- **Status:** Accepted
- **Date:** 2026-09-27
- **Deciders:** Serhii Surnin (owner) — explicit choice among PyPI / git-based / local-only; ZCode (facilitator)
- **Origin:** user answer to distribution question

## Context

The core requirement: **one-liner for any new project** that sets up the
software factory in auto mode. Options considered:

- **PyPI**: `uvx sfactory init` — cleanest UX, but requires publishing to
  PyPI (account, release process). Note: the name `sfactory` was verified
  free on PyPI (2026-09-27), so this path stays open.
- **Git-based**: `uvx --from git+https://github.com/<you>/sfactory sfactory
  init --auto` — no registry account needed; the repo is the distribution.
- **Local-only**: `uvx --from F:/SoftwareFactory sfactory init` — works only
  on this machine; rejected as the primary path (owner wants "any new
  project", implying other machines/repositories too).

## Decision

Primary distribution is **git-based**:

```
uvx --from git+https://github.com/<you>/piSoftwareFactory sfactory init --auto
```

(The package name `piSoftwareFactory` was chosen by the owner in ADR-0011;
the console command is `sfactory`.) The `<you>` placeholder becomes the
owner's GitHub account when the repo is published. Creating the GitHub
repository is an **owner-intercepted step**: the owner runs
`gh repo create piSoftwareFactory --public --source=. --push` (or uses the
browser UI); ZCode never creates remotes or pushes without being asked.

## Consequences

- The package must be `uv`-buildable from a git checkout (PEP 517, src
  layout, pinned build backend).
- Versioning via git tags (`v0.1.0`, …); `uvx --from git+...@v0.1.0` pins.
- If the owner later wants a registry-based install, the normalized PyPI
  name `pisoftwarefactory` was verified free (2026-09-27), so publishing is
  an incremental step, not a redesign.
- Publishing to GitHub makes the code public unless a private repo is
  chosen at creation time — owner decides at the interception point.

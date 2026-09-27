"""``sfactory init`` — scaffold a factory into a project (ADR-0012).

Everything written here is additive: existing files are never touched
without ``--force``. The three scaffolded scripts (preflight, interlock,
lane manager) are stdlib-only so they run even where ``sfactory`` is not
importable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from jinja2 import Template

from .config import (
    ALL_LANGUAGES,
    FactoryConfig,
    Language,
    bank_id_for,
    save_config,
)
from .gate_profiles import detect_languages

# --------------------------------------------------------------------------
# Scaffolded file templates
# --------------------------------------------------------------------------

AGENTS_MD = """\
# {{ project_name }} — Software Factory Constitution

This repository runs on the piSoftwareFactory pipeline (ADR-0007 in the
sfactory repo). Agents run the floor; the human runs the gates.

## Pipeline

Intake → Brief → Dispatch → Execute → **Gates** → Review → Release → Reflect.

- Intake classifies every task (Jev when configured, heuristic otherwise)
  into a node of `launch.graph.json` with kind, risk tier, lane and blockers.
- Execute happens on a **lane worktree** (`.factory/worktrees/<lane>`),
  one lane per worker, never on `{{ main_branch }}` directly.
- **Gates are deterministic** (`sfactory gates`): compiling is not building,
  and building is not working. A gate result of `skipped:<reason>` is
  evidence, not a pass.
- Review is a fresh-context, read-only pass. Verdicts are exactly
  `clean | nits | blockers`. Blockers loop back to Execute.
- Release: waves accumulate on `{{ stage_branch }}`; `{{ main_branch }}`
  moves only via a **human-approved merge**. The orchestrator never merges
  on its own, in auto mode or otherwise.

## Worker contract

1. **1 element = 1 commit.** An element is 20–30 minutes of work. Commit
   message: what changed and why. Docs are committed with the code they
   describe.
2. **Done ≠ verified ≠ works.** Your claim of success is not verification.
   The gates decide. If a gate fails, read the attached output, fix, commit.
3. Work only inside the files your brief names plus what they genuinely
   require. Do not refactor beyond the element's scope.
4. Run `GIT_EDITOR=true git …` for any git command that could open an
   editor, and `git show --stat HEAD` after every commit (an unstaged-edit
   incident once shipped because this was skipped).
5. Interruption granularity is one commit: keep the tree consistent at
   every commit so a kill never loses partial structure.

## Memory

- Before starting an element, consult the memories injected in your brief
  (Hindsight recall) and `memory/handover/CURRENT.md`.
- Past incidents became gates — never weaken a gate to make your work pass.
- Process improvements go to `memory/proposals.md`; decisions to
  `memory/decisions.md`; open questions for the human to
  `memory/open_questions.md`.

## Context discipline

Briefs are ≤ 2k tokens by design. Read exactly the files they name with
`@path` includes; do not "explore the repo" beyond the element. If context
is missing to do the element correctly, stop and report rather than guess.

## Languages active in this repo

{% for lang in languages %}- {{ lang }}
{% endfor %}
"""

PI_MODELS_JSON = """\
{
  "providers": {
    "llamacpp": {
      "baseUrl": "{{ base_url }}",
      "api": "openai-completions",
      "apiKey": "none",
      "models": [
        { "id": "{{ model }}" }
      ]
    }
  }
}
"""

PREFLIGHT_PY = '''#!/usr/bin/env python3
"""Session preflight (saintnikopol pattern): refuse to start on a broken state.

Stdlib-only; run directly:  python .factory/gates/preflight.py [--skip-net]
Exit codes: 0 = preflight passed, 1 = a check failed (evidence printed).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FAILURES: list[str] = []


def check(name: str, ok: bool, evidence: str = "") -> None:
    status = "ok" if ok else "FAIL"
    print(f"[{status:>4}] {name}" + (f" — {evidence}" if evidence and not ok else ""))
    if not ok:
        FAILURES.append(name)


def git(*args: str) -> str:
    return subprocess.run(("git", *args), capture_output=True, text=True, check=True).stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-net", action="store_true", help="skip backend reachability check")
    args = parser.parse_args()

    check("git work tree", (ROOT / ".git").exists() or "gitdir:" in (ROOT / ".git").read_text() if (ROOT / ".git").exists() else False)
    try:
        git("-C", str(ROOT), "rev-parse", "--git-dir")
        check("git works", True)
    except Exception as exc:  # noqa: BLE001
        check("git works", False, str(exc))
        return 1

    unmerged = git("-C", str(ROOT), "ls-files", "-u").strip()
    check("no unresolved merge conflicts", not unmerged, unmerged.splitlines()[0] if unmerged else "")

    check("factory.toml present", (ROOT / "factory.toml").is_file())

    if not args.skip_net and (ROOT / "factory.toml").is_file():
        raw = tomllib.loads((ROOT / "factory.toml").read_text(encoding="utf-8"))
        base = raw.get("backend", {}).get("base_url", "http://127.0.0.1:8080/v1").rstrip("/")
        try:
            with urllib.request.urlopen(f"{base}/models", timeout=5) as resp:
                models = json.load(resp)
            ids = [m.get("id") for m in models.get("data", [])]
            check("llm backend reachable", True, f"models={ids}")
        except Exception as exc:  # noqa: BLE001
            check("llm backend reachable", False, f"{base} ({exc})")

    if FAILURES:
        print(f"preflight: {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("preflight: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

INTERLOCK_PY = '''#!/usr/bin/env python3
"""Interlock check (saintnikopol pattern): lanes must not touch the same files.

Compares the changed-file set of the current lane against every other lane
worktree/branch. Exit 1 on intersection — dispatch must not proceed.

Usage: python .factory/gates/interlock_check.py [base_ref]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FACTORY_DIR = ROOT / ".factory"


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(
        ("git", *args), cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def changed_files(base: str) -> set[str]:
    out = git("diff", "--name-only", f"{base}...HEAD")
    out += git("diff", "--name-only", "--cached")
    out += git("diff", "--name-only")
    return {line.strip() for line in out.splitlines() if line.strip()}


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "main"
    mine = changed_files(base)
    if not mine:
        print("interlock: current lane has no changes yet")
        return 0

    conflicts: dict[str, set[str]] = {}
    worktrees_root = FACTORY_DIR / "worktrees"
    if worktrees_root.is_dir():
        for lane in sorted(p for p in worktrees_root.iterdir() if (p / ".git").exists()):
            try:
                theirs = {
                    line.strip()
                    for line in git(
                        "diff", "--name-only", f"{base}...HEAD", cwd=lane
                    ).splitlines()
                    if line.strip()
                }
            except subprocess.CalledProcessError:
                continue  # lane has no commits yet
            overlap = mine & theirs
            if overlap:
                conflicts[lane.name] = overlap

    if conflicts:
        print("interlock: FILE-SET COLLISION — dispatch is blocked")
        for lane, overlap in conflicts.items():
            print(f"  lane {lane!r} also touches: {sorted(overlap)}")
        return 1
    print(f"interlock: clean ({len(mine)} files exclusive to this lane)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

LANE_PY = '''#!/usr/bin/env python3
"""Git-worktree lane manager (ADR-0007): one worktree + branch per lane.

Usage:
  python .factory/lanes/lane.py create <name>   # worktree .factory/worktrees/<name>, branch lane/<name>
  python .factory/lanes/lane.py list
  python .factory/lanes/lane.py remove <name>
  python .factory/lanes/lane.py clean           # prune stale worktree metadata
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WT_ROOT = ROOT / ".factory" / "worktrees"


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(
        ("git", *args), cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]

    if cmd == "create":
        if len(sys.argv) != 3:
            print("usage: lane.py create <name>")
            return 2
        name = sys.argv[2]
        path = WT_ROOT / name
        if path.exists():
            print(f"lane {name!r} already exists at {path}")
            return 1
        WT_ROOT.mkdir(parents=True, exist_ok=True)
        base = sys.argv[3] if len(sys.argv) > 3 else None
        args = ["worktree", "add", str(path), "-b", f"lane/{name}"]
        if base:
            args.append(base)
        print(git(*args).strip())
        print(f"lane {name!r} ready at {path}")
        return 0

    if cmd == "list":
        print(git("worktree", "list").strip())
        return 0

    if cmd == "remove":
        if len(sys.argv) != 3:
            print("usage: lane.py remove <name>")
            return 2
        name = sys.argv[2]
        print(git("worktree", "remove", str(WT_ROOT / name), "--force").strip())
        try:
            git("branch", "-D", f"lane/{name}")
        except subprocess.CalledProcessError:
            pass
        return 0

    if cmd == "clean":
        print(git("worktree", "prune").strip())
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
'''

HINDSIGHT_BOOTSTRAP_MD = """\
# Hindsight memory bootstrap (ADR-0005)

Per-project memory bank: `{{ bank_id }}`

## Run the server (pick one)

pip (Windows-supported):

    pip install hindsight-api
    set HINDSIGHT_API_LLM_PROVIDER=llamacpp
    set HINDSIGHT_API_LLM_API_BASE={{ base_url }}
    set HINDSIGHT_API_LLM_MODEL={{ model }}
    set HINDSIGHT_API_LLM_API_KEY=none
    set HINDSIGHT_API_EMBEDDINGS_PROVIDER=local
    hindsight-api

Docker:

    docker run -it --pull always --name hindsight --restart unless-stopped ^
      -p 8888:8888 -p 9999:9999 ^
      -e HINDSIGHT_API_LLM_PROVIDER=llamacpp ^
      -e HINDSIGHT_API_LLM_API_BASE={{ base_url }} ^
      -e HINDSIGHT_API_LLM_MODEL={{ model }} ^
      -e HINDSIGHT_API_LLM_API_KEY=none ^
      -e HINDSIGHT_API_EMBEDDINGS_PROVIDER=local ^
      -v hindsight-data:/home/hindsight/.pg0 ^
      ghcr.io/vectorize-io/hindsight:latest

(Exact env var names per your Hindsight version: see
https://hindsight.vectorize.io — `sfactory doctor` validates the result.)

## Endpoints

- API: http://localhost:8888 — UI: http://localhost:9999
- MCP (bank-scoped, for ZCode/agents): `http://localhost:8888/mcp/{{ bank_id }}/`

## Wire coding agents

    npx @vectorize-io/hindsight-coding-agents install all

## Operations used by the pipeline

- `retain` — after every wave/incident (Reflect stage)
- `recall` — top-{{ recall_top_k }} memories injected into each worker brief
- `reflect` — deep analysis when the orchestrator needs cross-wave insight
"""

PAPERCLIP_COMPANY_JSON = """\
{
  "//": "Paperclip company seed (ADR-0006). Applied by: sfactory paperclip",
  "company": {
    "name": "{{ project_name }} Factory",
    "goal": "Ship {{ project_name }} through the 8-stage factory pipeline; human gate at release only.",
    "roles": [
      { "title": "Architect", "reports_to": "board", "duties": "design briefs, wave planning, risk tiers; never decides for the user" },
      { "title": "Worker-L1", "reports_to": "Architect", "duties": "execute lane-1 elements; 1 element = 1 commit; never bypass gates" },
      { "title": "Worker-L2", "reports_to": "Architect", "duties": "execute lane-2 elements; 1 element = 1 commit; never bypass gates" },
      { "title": "Reviewer", "reports_to": "board", "duties": "fresh-context read-only review; verdict clean|nits|blockers" }
    ],
    "gates": [
      { "name": "verify", "type": "machine", "command": "sfactory gates" },
      { "name": "review", "type": "machine", "command": "sfactory review" },
      { "name": "release", "type": "human", "note": "merge stage -> main requires board approval" }
    ],
    "harness": {
      "name": "pi",
      "command": "pi --mode json @brief.md",
      "model": "{{ model }}",
      "endpoint": "{{ base_url }}"
    },
    "budgets": { "currency": "local-tokens", "note": "per-agent caps enforced by Paperclip; overspend pauses the agent" }
  }
}
"""

# --------------------------------------------------------------------------


def _write(path: Path, content: str, force: bool, log) -> bool:
    """Write *content* to *path* unless it exists and --force is unset."""
    if path.exists() and not force:
        log(f"  [yellow]skip[/] {path.relative_to(path.anchor)} exists (use --force)")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    log(f"  [green]wrote[/] {path}")
    return True


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(root), *args), capture_output=True, text=True, check=True
    ).stdout


def _probe_backend(config: FactoryConfig, log) -> None:
    """Fill the real model id from the live llama.cpp server (ADR-0010)."""
    try:
        from .integrations.llama import probe_backend

        info = probe_backend(config.backend.base_url)
        if info.models:
            config.backend.model = info.models[0]
        if info.context_window:
            config.backend.context_window = info.context_window
        log(f"  [green]probed[/] backend {config.backend.base_url} → model={config.backend.model} ctx={config.backend.context_window}")
    except Exception as exc:  # noqa: BLE001
        log(f"  [yellow]backend probe failed[/] ({exc}); keeping defaults")


def run_init(target: Path, auto: bool, langs: str, force: bool, log) -> None:
    """Scaffold the factory into *target* (ADR-0012)."""
    root = target.resolve()

    if not (root / ".git").exists():
        subprocess.run(("git", "init", "-b", "main"), cwd=root, check=True, capture_output=True)
        log(f"[green]git init[/] {root} (new projects need git; lane worktrees depend on it)")

    if langs.strip():
        requested = [part.strip().lower() for part in langs.split(",") if part.strip()]
        unknown = [lang for lang in requested if lang not in ALL_LANGUAGES]
        if unknown:
            raise SystemExit(f"unknown language(s): {unknown}; valid: {list(ALL_LANGUAGES)}")
        languages: list[Language] = requested  # type: ignore[assignment]
        log(f"languages (forced): {languages}")
    else:
        languages = detect_languages(root)
        log(f"languages (detected): {languages or 'none — gates will skip until files appear'}")

    config = FactoryConfig(
        auto=auto,
        languages=languages,
        memory={"bank_id": root.name.lower().replace(" ", "-")},
    )
    _probe_backend(config, log)

    ctx = {
        "project_name": root.name,
        "languages": languages,
        "auto": auto,
        "base_url": config.backend.base_url,
        "model": config.backend.model,
        "main_branch": config.release.main_branch,
        "stage_branch": config.release.stage_branch,
        "bank_id": bank_id_for(root, config),
        "recall_top_k": config.memory.recall_top_k,
    }

    log(f"scaffolding factory into [bold]{root}[/]")
    _write(root / "AGENTS.md", Template(AGENTS_MD).render(**ctx), force, log)
    save_config(config, root)
    log(f"  [green]wrote[/] {root / 'factory.toml'}")
    _write(root / "launch.graph.json", json.dumps({"version": 1, "nodes": []}, indent=2) + "\n", force, log)
    _write(root / ".pi" / "models.json", Template(PI_MODELS_JSON).render(**ctx), force, log)
    _write(root / ".factory" / "gates" / "preflight.py", PREFLIGHT_PY, force, log)
    _write(root / ".factory" / "gates" / "interlock_check.py", INTERLOCK_PY, force, log)
    _write(root / ".factory" / "lanes" / "lane.py", LANE_PY, force, log)
    _write(root / ".factory" / "briefs" / ".gitkeep", "", force, log)
    _write(root / ".factory" / "run" / ".gitignore", "*\n!.gitignore\n", force, log)
    _write(root / "memory" / "reflections.json", json.dumps({"entries": []}, indent=2) + "\n", force, log)
    _write(
        root / "memory" / "proposals.md",
        "# Process proposals\n\nAccepted proposals become gates (an incident becomes a script).\n\n| date | proposal | status |\n|---|---|---|\n",
        force,
        log,
    )
    _write(
        root / "memory" / "decisions.md",
        f"# Decisions — {root.name}\n\nAppend-only decision log. Format: date · decision · reason.\n\n",
        force,
        log,
    )
    _write(
        root / "memory" / "handover" / "CURRENT.md",
        "# Handover — current state\n\nWhat is in flight, what is blocked, what the next session must know.\n\n- (empty)\n",
        force,
        log,
    )
    _write(
        root / "memory" / "open_questions.md",
        "# Open questions for the human\n\nThe orchestrator never decides for the user — park questions here.\n\n- (empty)\n",
        force,
        log,
    )
    _write(root / "hindsight.bootstrap.md", Template(HINDSIGHT_BOOTSTRAP_MD).render(**ctx), force, log)
    _write(root / "paperclip.company.json", Template(PAPERCLIP_COMPANY_JSON).render(**ctx), force, log)

    gitignore = root / ".gitignore"
    entry = ".factory/run/\n"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if entry not in existing:
        gitignore.write_text(existing + ("\n" if existing and not existing.endswith("\n") else "") + "# sfactory runtime evidence\n" + entry, encoding="utf-8")
        log(f"  [green]wrote[/] {gitignore} (+ .factory/run/)")

    log("\n[bold]Factory scaffolded.[/]")
    log(f"  auto mode: {'[green]ON[/] — unattended waves; the only human gate is the release merge' if auto else '[yellow]off[/] (pass --auto for unattended waves)'}")
    log("  next steps:")
    log("    sfactory doctor            # verify llama.cpp, pi, hindsight, paperclip, toolchains")
    log("    sfactory run \"<task>\"      # one task through the whole pipeline")
    log("    sfactory paperclip         # provision the management company (needs: npx paperclipai onboard --yes)")
    log("    hindsight.bootstrap.md     # start local memory (one-time)")

"""``sfactory doctor`` — verify every dependency the factory needs.

Reports ok/warn/fail per check with exact fix hints; exit code 1 only when
a critical dependency (llama.cpp backend or the pi harness) is missing.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rich.table import Table

from .config import FactoryConfig, load_config
from .integrations import hindsight, paperclip
from .integrations.llama import probe_backend


@dataclass
class Check:
    name: str
    state: str  # ok | warn | fail
    detail: str
    hint: str = ""


def _run_version(argv: tuple[str, ...]) -> str | None:
    binary = shutil.which(argv[0])
    if binary is None:
        return None
    try:
        proc = subprocess.run((binary, *argv[1:]), capture_output=True, text=True, timeout=15)
        out = (proc.stdout or proc.stderr).strip().splitlines()
        return out[0] if out else "installed"
    except Exception:  # noqa: BLE001
        return "installed"


def run_doctor(root: Path, log) -> int:  # pragma: no cover - interactive output
    root = root.resolve()
    checks: list[Check] = []

    config: FactoryConfig | None = None
    if (root / "factory.toml").is_file():
        config = load_config(root)
        checks.append(Check("factory.toml", "ok", f"{root / 'factory.toml'}"))
    else:
        checks.append(Check("factory.toml", "warn", "not scaffolded yet", "run: sfactory init --auto"))

    py = _run_version(("python", "--version")) or "missing"
    checks.append(Check("python", "ok" if py != "missing" else "fail", py))

    git = _run_version(("git", "--version")) or "missing"
    checks.append(Check("git", "ok" if git != "missing" else "fail", git))

    node = _run_version(("node", "--version")) or "missing"
    if node != "missing" and node.lstrip("v"):
        major = int(node.lstrip("v").split(".")[0].split("-")[0])
        checks.append(Check("node", "ok" if major >= 24 else "warn", node, "pi/paperclip want Node ≥ 24.11"))
    else:
        checks.append(Check("node", "fail", "missing", "install Node.js 24.11+ (pi + Paperclip)"))

    pi = _run_version(("pi", "--version")) or "missing"
    checks.append(
        Check("pi harness", "ok" if pi != "missing" else "fail", pi,
              "npm install -g @earendil-works/pi-coding-agent")
    )

    # Backend (critical)
    if config:
        try:
            info = probe_backend(config.backend.base_url)
            if info.healthy:
                detail = f"models={info.models}"
                if info.context_window and info.context_window < config.backend.context_window:
                    checks.append(Check("llama.cpp backend", "warn", f"{detail}; server ctx {info.context_window} < configured {config.backend.context_window}", "align factory.toml backend.context_window"))
                elif info.context_window:
                    checks.append(Check("llama.cpp backend", "ok", f"{detail}; ctx={info.context_window}"))
                else:
                    checks.append(Check("llama.cpp backend", "ok", detail + " (ctx not reported)"))
                if config.backend.model not in info.models:
                    checks.append(Check("model id", "warn", f"configured {config.backend.model!r} not in {info.models}", "update factory.toml backend.model"))
            else:
                checks.append(Check("llama.cpp backend", "fail", "no models reported", "start llama-server"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("llama.cpp backend", "fail", str(exc), f"is llama-server up at {config.backend.base_url}?"))
    else:
        checks.append(Check("llama.cpp backend", "warn", "skipped (no factory.toml)"))

    # Memory
    if config:
        up = hindsight.is_up(config.memory.hindsight_url)
        checks.append(
            Check("hindsight memory", "ok" if up else "warn", config.memory.hindsight_url if up else "not running",
                  "see hindsight.bootstrap.md — the pipeline degrades gracefully without it")
        )

    # Paperclip
    pc_up = paperclip.server_up()
    checks.append(
        Check("paperclip server", "ok" if pc_up else "warn", paperclip.DEFAULT_SERVER if pc_up else "not running",
              "npx paperclipai onboard --yes (management layer is optional)")
    )

    # Language toolchains
    for tool in ("uv", "dotnet", "npm", "cargo", "cmake", "go"):
        found = shutil.which(tool)
        checks.append(Check(f"toolchain: {tool}", "ok" if found else "warn", found or "not installed",
                            "" if found else "gates for that language will skip with evidence"))

    table = Table(title="sfactory doctor", title_justify="left")
    table.add_column("check")
    table.add_column("state")
    table.add_column("detail", overflow="fold")
    table.add_column("hint", overflow="fold")
    critical_fail = False
    for c in checks:
        style = {"ok": "green", "warn": "yellow", "fail": "red"}[c.state]
        if c.state == "fail" and c.name in ("llama.cpp backend", "pi harness"):
            critical_fail = True
        table.add_row(c.name, f"[{style}]{c.state}[/]", c.detail, c.hint)
    log(table)

    fails = sum(1 for c in checks if c.state == "fail")
    warns = sum(1 for c in checks if c.state == "warn")
    log(f"{fails} fail(s), {warns} warn(s)" + (" — critical dependency missing" if critical_fail else ""))
    return 1 if critical_fail else 0

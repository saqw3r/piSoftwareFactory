"""Release (ADR-0007): waves accumulate on ``stage``; ``main`` moves only via
a human-approved merge. The orchestrator never merges on its own — without
``approve=True`` this stage only prepares and reports.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from ..config import FactoryConfig
from .graph import Graph


class ReleaseError(RuntimeError):
    pass


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(("git", "-C", str(root), *args), capture_output=True, text=True, check=check)


def _require_clean(root: Path) -> None:
    status = _git(root, "status", "--porcelain").stdout.strip()
    if status:
        raise ReleaseError(f"working tree not clean:\n{status}")


def collect(root: Path, config: FactoryConfig, graph: Graph, log=print) -> str:
    """Machine step: merge every done lane branch into ``stage`` (created if missing)."""
    _require_clean(root)
    stage_exists = _git(root, "rev-parse", "--verify", config.release.stage_branch, check=False).returncode == 0
    if stage_exists:
        _git(root, "checkout", config.release.stage_branch)
    else:
        _git(root, "checkout", "-b", config.release.stage_branch)
    merged: list[str] = []
    for node in graph.nodes:
        if node.status != "done" or not node.lane:
            continue
        branch = f"lane/{node.lane}"
        proc = _git(root, "merge", "--no-ff", "-m", f"wave merge: {node.id} {node.title}", branch, check=False)
        if proc.returncode == 0:
            merged.append(branch)
            log(f"  merged {branch} into {config.release.stage_branch}")
        else:
            log(f"  [yellow]merge conflict[/] for {branch}: {proc.stdout} {proc.stderr}")
    _git(root, "checkout", config.release.main_branch, check=False)
    return ", ".join(merged) or "(nothing new)"


def merge_to_main(root: Path, config: FactoryConfig, log=print) -> str:
    """The human gate. Only ever called with explicit owner approval."""
    _require_clean(root)
    _git(root, "checkout", config.release.main_branch)
    _git(root, "merge", "--no-ff", config.release.stage_branch, "-m", "factory release: human-approved merge")
    tag = f"release-{time.strftime('%Y%m%d-%H%M%S')}"
    _git(root, "tag", tag)
    log(f"  merged {config.release.stage_branch} → {config.release.main_branch}, tagged [bold]{tag}[/]")
    return tag

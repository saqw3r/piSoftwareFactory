"""Reflect (ADR-0005/ADR-0007): mandatory after every wave/incident.

Writes the durable record to ``memory/`` (source of truth) and retains a
summary in Hindsight (semantic index). Reflections are where proposals
come from; accepted proposals become gates.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from ..config import FactoryConfig, bank_id_for
from ..integrations import hindsight
from .graph import Graph


def _recent_commits(root: Path, n: int = 10) -> list[str]:
    proc = subprocess.run(
        ("git", "-C", str(root), "log", "--oneline", f"-{n}"),
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in (proc.stdout or "").splitlines() if line.strip()]


def reflect(root: Path, config: FactoryConfig, graph: Graph, summary: str, incident: str = "") -> Path:
    """Append a reflection entry, update handover, retain in Hindsight."""
    root = root.resolve()
    done = [n.id for n in graph.nodes if n.status == "done"]
    blocked = [n.id for n in graph.nodes if n.status == "blocked"]

    entry = {
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "summary": summary,
        "incident": incident,
        "done": done,
        "blocked": blocked,
        "commits": _recent_commits(root),
    }

    refl_path = root / "memory" / "reflections.json"
    data = json.loads(refl_path.read_text(encoding="utf-8")) if refl_path.is_file() else {"entries": []}
    data.setdefault("entries", []).append(entry)
    refl_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    handover = root / "memory" / "handover" / "CURRENT.md"
    handover.parent.mkdir(parents=True, exist_ok=True)
    handover.write_text(
        "# Handover — current state\n\n"
        f"Updated: {entry['date']}\n\n"
        f"- done: {', '.join(done) or '—'}\n"
        f"- blocked: {', '.join(blocked) or '—'}\n"
        f"- summary: {summary}\n"
        + (f"- incident: {incident}\n" if incident else ""),
        encoding="utf-8",
    )

    hindsight.retain(
        config.memory.hindsight_url,
        bank_id_for(root, config),
        summary + (f" Incident: {incident}" if incident else ""),
        context="factory reflection",
    )
    return refl_path

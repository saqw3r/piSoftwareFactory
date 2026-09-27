"""``launch.graph.json`` — the intake DAG (ADR-0007).

State, not events, between layers: the graph file is the pipeline's source
of truth and makes every run resumable. Interruption granularity is one
commit; node status transitions are recorded here by the orchestrator.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["feature", "bug", "chore", "docs"]
Risk = Literal["low", "standard", "high"]
Status = Literal["intake", "ready", "dispatched", "gates", "review", "done", "released", "blocked"]


class Node(BaseModel):
    id: str
    title: str
    task: str
    kind: str = "feature"
    risk: str = "standard"
    language: str = ""
    lane: str = ""
    size: str = "medium"
    status: str = "intake"
    verdict: str = ""  # clean | nits | blockers (review stage)
    gate_status: str = ""  # pass | blocked | "" (not yet run)
    files: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    provider: str = ""  # jev | heuristic (intake evidence)
    confidence: float | None = None
    created_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    updated_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    notes: str = ""

    def touch(self) -> None:
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")


class Graph(BaseModel):
    version: int = 1
    nodes: list[Node] = Field(default_factory=list)

    def add(self, node: Node) -> Node:
        node.id = node.id or f"n{len(self.nodes) + 1:03d}"
        self.nodes.append(node)
        return node

    def get(self, node_id: str) -> Node:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(f"node {node_id!r} not in graph")

    def ready(self) -> list[Node]:
        """Nodes whose blockers are all done/released and which await dispatch."""
        done = {n.id for n in self.nodes if n.status in ("done", "released")}
        return [
            n
            for n in self.nodes
            if n.status in ("intake", "ready")
            and all(b in done for b in n.blockers)
        ]


def load_graph(root: Path) -> Graph:
    path = root / "launch.graph.json"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found — run `sfactory init` first.")
    return Graph.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_graph(root: Path, graph: Graph) -> Path:
    path = root / "launch.graph.json"
    path.write_text(graph.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def commit_graph(root: Path) -> bool:
    """Commit graph-state changes (state, not events; interruption
    granularity is one commit). Keeps `sfactory release`'s clean-tree gate
    passable without manual housekeeping. Returns True when a commit was made."""
    import os
    import subprocess

    root = root.resolve()
    subprocess.run(("git", "-C", str(root), "add", "launch.graph.json"), check=False)
    proc = subprocess.run(
        ("git", "-C", str(root), "commit", "-m", "chore(graph): record node state"),
        capture_output=True, text=True, check=False,
        env={**os.environ, "GIT_EDITOR": "true"},
    )
    return proc.returncode == 0

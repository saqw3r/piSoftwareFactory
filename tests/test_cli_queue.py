"""CLI-level flow: `run --queue` lands a ready node without dispatching."""

from __future__ import annotations

from typer.testing import CliRunner

from pisoftwarefactory.cli import app
from pisoftwarefactory.pipeline.graph import load_graph
from pisoftwarefactory.scaffold import run_init


def test_run_queue_adds_ready_node(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")
    run_init(target=tmp_path, auto=True, langs="python", force=False, log=print)

    result = CliRunner().invoke(app, ["run", "Add a feature", "--queue", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output

    graph = load_graph(tmp_path)
    assert len(graph.nodes) == 1
    assert graph.nodes[0].status == "ready"
    assert graph.ready()[0].title.startswith("Add a feature")

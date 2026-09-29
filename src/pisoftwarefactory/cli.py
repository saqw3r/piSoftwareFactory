"""The ``sfactory`` command line (ADR-0011 command set)."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from . import __version__

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Local-first AI software factory (piSoftwareFactory).",
)
console = Console()
err = Console(stderr=True, style="bold red")


def _version(value: bool) -> None:
    if value:
        console.print(f"sfactory {__version__} (piSoftwareFactory)")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", callback=_version, is_eager=True, help="Show version and exit."
    ),
) -> None:
    """Local-first AI software factory (piSoftwareFactory)."""


@app.command()
def init(
    target: Path = typer.Argument(Path("."), help="Project directory to scaffold."),
    auto: bool = typer.Option(False, "--auto", help="Enable unattended auto mode (ADR-0007)."),
    langs: str = typer.Option(
        "", "--langs", help="Comma-separated languages (default: auto-detect). e.g. python,rust,go"
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing scaffolded files."),
) -> None:
    """Scaffold the factory into a project (ADR-0012)."""
    from .scaffold import run_init

    run_init(target=target, auto=auto, langs=langs, force=force, log=console.print)

    # The one-liner ends with evidence: verify the environment right away.
    try:
        from .doctor import run_doctor

        console.print("\n[bold]environment check:[/]")
        run_doctor(target.resolve(), log=console.print)
    except Exception as exc:  # noqa: BLE001 — never fail the scaffold on doctor
        console.print(f"[yellow]doctor skipped[/] ({exc}); run `sfactory doctor` later")


@app.command()
def doctor() -> None:
    """Verify the local factory environment (llama.cpp, pi, hindsight, paperclip, toolchains)."""
    from .doctor import run_doctor

    raise typer.Exit(code=run_doctor(Path("."), log=console.print))


@app.command()
def gates(
    lang: str = typer.Option("", "--lang", help="Run a single language's gates."),
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """Run deterministic verification gates (ADR-0013)."""
    from rich.table import Table

    from .config import load_config
    from .gates import FAILED, run_gates

    root = root.resolve()
    config = load_config(root)
    results = run_gates(root, config, only_lang=lang or None)

    any_failed = False
    for profile in results:
        table = Table(title=f"gates: {profile.language}", title_justify="left")
        table.add_column("step")
        table.add_column("status")
        table.add_column("detail", overflow="fold")
        for step in profile.steps:
            style = "green" if step.status == "passed" else ("yellow" if step.status.startswith("skipped:") else "red")
            if step.status == FAILED:
                any_failed = True
            table.add_row(step.name, f"[{style}]{step.status}[/]", step.detail)
        console.print(table)
    verdict = "[red]BLOCKED[/] — fix and re-run" if any_failed else "[green]PASS[/]"
    console.print(f"gate verdict: {verdict}")
    raise typer.Exit(code=1 if any_failed else 0)


@app.command()
def run(
    task: str = typer.Argument(..., help="Task description for Intake."),
    lang: str = typer.Option("", "--lang", help="Force the language/lane (default: Intake decides)."),
    queue: bool = typer.Option(
        False, "--queue", help="Intake only: add to the graph without dispatching (execute later with `sfactory wave`)."
    ),
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """Run one task through the full pipeline: Intake → …→ Reflect (ADR-0007)."""
    from .config import load_config
    from .pipeline.dispatch import dispatch_node
    from .pipeline.graph import Graph, Node, commit_graph, load_graph, save_graph
    from .pipeline.intake import run_intake
    from .pipeline.reflect import reflect
    from .pipeline.review import review_node

    root = root.resolve()
    config = load_config(root)
    graph = load_graph(root)

    decision = run_intake(task, config)
    if lang:
        decision.language = lang
    console.print(
        f"intake ({decision.provider}): kind={decision.kind} risk={decision.risk} "
        f"size={decision.size} language={decision.language or 'n/a'}"
    )

    node = Node(
        id=f"n{len(graph.nodes) + 1:03d}",
        title=task.strip().splitlines()[0][:80],
        task=task,
        kind=decision.kind,
        risk=decision.risk,
        language=decision.language,
        lane=f"L{len(graph.nodes) % config.lanes.count + 1}",
        size=decision.size,
        status="ready",
        provider=decision.provider,
        confidence=decision.confidence,
    )
    graph.add(node)
    save_graph(root, graph)

    if queue:
        commit_graph(root)
        console.print(f"queued {node.id} — execute later with [bold]sfactory wave[/] (parallel across lanes)")
        return

    result = dispatch_node(root, node, config, log=console.print)
    save_graph(root, graph)
    commit_graph(root)
    if node.status == "blocked":
        reflect(root, config, graph, f"{node.id} blocked by gates", incident="gate failure loop exhausted")
        save_graph(root, graph)
        commit_graph(root)
        err.print(f"node {node.id} BLOCKED by gates — see evidence in .factory/run/")
        raise typer.Exit(code=1)

    wt = root / ".factory" / "worktrees" / node.lane
    console.print("review: fresh-context reviewer reading the lane diff…")
    review_node(root, node, config, cwd=wt)
    if node.verdict == "blockers":
        node.status = "blocked"
        node.gate_status = "pass"
        save_graph(root, graph)
        reflect(root, config, graph, f"{node.id} blocked in review", incident="review verdict: blockers")
        err.print(f"node {node.id} got verdict `blockers` — looped back for a human look")
        raise typer.Exit(code=1)

    save_graph(root, graph)
    commit_graph(root)
    reflect(root, config, graph, f"{node.id} completed: {node.title} (verdict {node.verdict or 'n/a'})")
    console.print(f"[green]done[/] {node.id} `{node.title}` verdict={node.verdict or 'n/a'} — merge via `sfactory release`")


@app.command()
def wave(
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """Dispatch all ready graph nodes across lanes/worktrees (ADR-0007)."""
    from .config import load_config
    from .pipeline.dispatch import dispatch_wave
    from .pipeline.graph import commit_graph, load_graph, save_graph
    from .pipeline.reflect import reflect
    from .pipeline.review import review_node

    root = root.resolve()
    config = load_config(root)
    graph = load_graph(root)
    ready = graph.ready()
    if not ready:
        console.print("no ready nodes in launch.graph.json — add tasks with `sfactory run`")
        return

    results = dispatch_wave(root, graph, config, log=console.print)
    for result in results:
        node = result.node
        if node.status == "done":
            wt = root / ".factory" / "worktrees" / node.lane
            review_node(root, node, config, cwd=wt)
            if node.verdict == "blockers":
                node.status = "blocked"
    save_graph(root, graph)
    commit_graph(root)

    done = [r.node.id for r in results if r.node.status == "done"]
    blocked = [r.node.id for r in results if r.node.status == "blocked"]
    reflect(root, config, graph, f"wave dispatched: done={done or '—'} blocked={blocked or '—'}")
    console.print(f"wave complete: [green]{len(done)} done[/], [red]{len(blocked)} blocked[/]")


@app.command()
def review(
    node: str = typer.Argument("", help="Graph node id (default: most recent done node)."),
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """Fresh-context read-only review with a clean|nits|blockers verdict (ADR-0007)."""
    from .config import load_config
    from .pipeline.graph import load_graph, save_graph
    from .pipeline.review import review_node

    root = root.resolve()
    config = load_config(root)
    graph = load_graph(root)
    if node:
        target = graph.get(node)
    else:
        candidates = [n for n in graph.nodes if n.status == "done"]
        if not candidates:
            err.print("no done nodes to review")
            raise typer.Exit(code=1)
        target = candidates[-1]

    wt = root / ".factory" / "worktrees" / (target.lane or "L1")
    review_node(root, target, config, cwd=wt)
    save_graph(root, graph)
    style = {"clean": "green", "nits": "yellow"}.get(target.verdict, "red")
    console.print(f"review {target.id}: [{style}]{target.verdict or 'no verdict parsed'}[/]")
    if target.notes:
        console.print(target.notes)


@app.command()
def release(
    approve: bool = typer.Option(False, "--approve", help="Human approval for the merge to main."),
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """Assemble stage branch; merge to main ONLY with human approval (ADR-0007)."""
    from .config import load_config
    from .pipeline.graph import commit_graph, load_graph, save_graph
    from .pipeline.release import collect, merge_to_main

    root = root.resolve()
    config = load_config(root)
    graph = load_graph(root)

    merged = collect(root, config, graph, log=console.print)
    console.print(f"stage assembly: {merged}")

    if not approve:
        console.print(
            "[bold]Human gate:[/] review the stage branch, then run [bold]sfactory release --approve[/] "
            "to merge into main. The orchestrator never merges on its own."
        )
        return

    tag = merge_to_main(root, config, log=console.print)
    for node in graph.nodes:
        if node.status == "done":
            node.status = "released"
    save_graph(root, graph)
    commit_graph(root)
    console.print(f"[green]released[/] as {tag}")


@app.command()
def reflect(
    summary: str = typer.Option("", "--summary", help="Wave/incident summary (default: derived from the graph)."),
    incident: str = typer.Option("", "--incident", help="Incident description; incidents become gates."),
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """Retain wave/incident learnings in Hindsight + memory/ files (ADR-0005)."""
    from .config import load_config
    from .pipeline.graph import load_graph
    from .pipeline.reflect import reflect

    root = root.resolve()
    config = load_config(root)
    graph = load_graph(root)
    text = summary or "manual reflection entry"
    path = reflect(root, config, graph, text, incident=incident)
    console.print(f"[green]reflected[/] → {path} (+ handover, Hindsight retain attempted)")


@app.command()
def paperclip(
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """Provision/inspect the Paperclip company for this project (ADR-0006)."""
    from .integrations import paperclip as pc

    root = root.resolve()
    up = pc.server_up()
    console.print(f"paperclip server: {'[green]running[/]' if up else '[yellow]not running[/]'} ({pc.DEFAULT_SERVER})")
    console.print(pc.provision_plan(root))


@app.command()
def status(
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """Show graph, lanes, and last gate evidence."""
    import json
    import subprocess

    from rich.table import Table

    from .pipeline.graph import load_graph

    root = root.resolve()
    graph = load_graph(root)

    table = Table(title="graph", title_justify="left")
    for col in ("id", "title", "kind", "risk", "lane", "status", "gates", "verdict"):
        table.add_column(col)
    for n in graph.nodes:
        table.add_row(n.id, n.title[:48], n.kind, n.risk, n.lane, n.status, n.gate_status or "—", n.verdict or "—")
    console.print(table)

    wt = subprocess.run(
        ("git", "-C", str(root), "worktree", "list"), capture_output=True, text=True
    )
    console.print(f"worktrees:\n{wt.stdout.strip() or '(none)'}")

    run_dir = root / ".factory" / "run"
    if run_dir.is_dir():
        evidence = sorted(run_dir.glob("gates-*.json"))
        if evidence:
            last = json.loads(evidence[-1].read_text(encoding="utf-8"))
            for r in last.get("results", []):
                console.print(f"last gates [{r['language']}]: {'[green]pass[/]' if r['passed'] else '[red]BLOCKED[/]'}")


def main() -> None:  # pragma: no cover
    """Console-script entry point (kept for `python -m` symmetry)."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()

"""The ``sfactory`` command line (ADR-0011 command set)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import typer
from rich.console import Console

from . import __version__

# -h everywhere, not just --help: muscle memory shouldn't pay a retry.
_HELP_CTX = {"help_option_names": ["-h", "--help"]}

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    context_settings=_HELP_CTX,
    help="Local-first AI software factory (piSoftwareFactory).",
)
services_app = typer.Typer(
    no_args_is_help=True, context_settings=_HELP_CTX, help="Provision optional local services (ADR-0015)."
)
app.add_typer(services_app, name="services")
console = Console()
err = Console(stderr=True, style="bold red")


def _version(value: bool) -> None:
    if value:
        console.print(f"sfactory {__version__} (piSoftwareFactory)")
        raise typer.Exit()


def _echo_invocation() -> None:
    """Echo the invoked command highlighted; internal step logs stream after it.

    Skipped for --help/--version so those stay machine-clean.
    """
    args = sys.argv[1:]
    if not args or any(a in ("-h", "--help", "-v", "--version") for a in args):
        return
    console.print(f"[bold bright_cyan]$ sfactory {' '.join(args)}[/]\n")


class _Tee:
    """Split stream: terminal + log file.

    Attribute passthrough (isatty/fileno/encoding) keeps rich colors and
    terminal detection intact — only write/flush are intercepted.
    """

    def __init__(self, stream, logfile) -> None:
        self._stream = stream
        self._logfile = logfile

    def write(self, data):
        self._stream.write(data)
        self._logfile.write(data)
        self._logfile.flush()
        return len(data)

    def flush(self):
        self._stream.flush()
        self._logfile.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _start_file_log(root: Path) -> Path | None:
    """Tee all console output to ``.factory/run/sfactory-<cmd>-<stamp>.log``.

    Called first in every command (after this, consoles are rebound so rich
    writes through the tee). Prints the log path up front; returns it.
    Best-effort: returns None when the run dir can't be created.
    """
    global console, err
    path: Path | None = None
    try:
        run_dir = root.resolve() / ".factory" / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        parts = [a for a in sys.argv[1:] if not a.startswith("-")][:2] or ["sfactory"]
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = run_dir / f"sfactory-{'-'.join(parts)}-{stamp}.log"
        fh = path.open("a", encoding="utf-8", errors="replace")
    except OSError:
        fh = None
    if fh is not None and path is not None:
        sys.stdout = _Tee(sys.stdout, fh)  # type: ignore[assignment]
        sys.stderr = _Tee(sys.stderr, fh)  # type: ignore[assignment]
        console = Console()
        err = Console(stderr=True)
        console.print(f"[dim]full log: {path}[/]")
    _echo_invocation()
    return path


def _review_or_die(root: Path, node, config, wt: Path) -> None:  # node: pipeline.graph.Node
    """Review the lane, or stop with git's own words.

    A reviewer handed an unreadable diff reviews nothing and still emits a
    verdict — which is how a node with no work behind it printed `done`. The
    refusal belongs in the operator's face, not in a file.
    """
    from .gitcmd import GitRefused
    from .pipeline.review import review_node

    try:
        review_node(root, node, config, cwd=wt)
    except GitRefused as exc:
        err.print(f"[red]review could not read the lane diff[/] — {node.id} stays unreviewed")
        err.print(str(exc))
        raise typer.Exit(code=1) from exc


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "-v", "--version", callback=_version, is_eager=True, help="Show version and exit."
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
    _start_file_log(target)
    from .scaffold import run_init

    run_init(target=target, auto=auto, langs=langs, force=force, log=console.print)
    root = target.resolve()

    if auto:
        # ADR-0015: --auto brings up the whole floor (idempotent, non-fatal).
        from .config import load_config
        from .services import setup_all

        console.print("\n[bold]services:[/]")
        try:
            config = load_config(root)
            results = setup_all(root, config, log=console.print)
            for name, state in results.items():
                style = "green" if state in ("started", "already-running", "already-running (pidfile)") else "yellow"
                console.print(f"  {name}: [{style}]{state}[/]")
        except Exception as exc:  # noqa: BLE001 — services never break the scaffold
            console.print(f"[yellow]service setup skipped[/] ({exc})")

    # The one-liner ends with evidence: verify the environment right away.
    try:
        from .doctor import run_doctor

        console.print("\n[bold]environment check:[/]")
        run_doctor(root, log=console.print)
    except Exception as exc:  # noqa: BLE001 — never fail the scaffold on doctor
        console.print(f"[yellow]doctor skipped[/] ({exc}); run `sfactory doctor` later")


@services_app.command()
def setup(
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """Install and start Hindsight + Paperclip (idempotent, ADR-0015)."""
    _start_file_log(root)
    from .config import load_config
    from .services import setup_all

    root = root.resolve()
    config = load_config(root)
    results = setup_all(root, config, log=console.print)
    for name, state in results.items():
        console.print(f"{name}: {state}")


@services_app.command()
def status(
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """Show whether Hindsight / Paperclip are up."""
    _start_file_log(root)
    from .config import load_config
    from .services import status as svc_status

    state = svc_status(root.resolve(), load_config(root.resolve()))
    for key, value in state.items():
        console.print(f"{key}: {value}")


@services_app.command()
def stop(
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """Stop factory-started Hindsight / Paperclip instances."""
    _start_file_log(root)
    from .services import stop_all

    for name, state in stop_all(root.resolve(), log=console.print).items():
        console.print(f"{name}: {state}")


@app.command()
def doctor() -> None:
    """Verify the local factory environment (llama.cpp, pi, hindsight, paperclip, toolchains)."""
    _start_file_log(Path("."))
    from .doctor import run_doctor

    raise typer.Exit(code=run_doctor(Path("."), log=console.print))


@app.command()
def update() -> None:
    """Reinstall sfactory from the source it was installed from (uv tool)."""
    import tempfile

    _start_file_log(Path(tempfile.gettempdir()))
    from .selfupdate import run_update

    raise typer.Exit(code=run_update(log=console.print))


@app.command()
def backend(
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
    harness: str = typer.Option("", "--harness", help="Coding agent: pi | omp."),
    source: str = typer.Option("", "--source", help="llama-local | openai-endpoint | harness-login."),
    base_url: str = typer.Option("", "--base-url", help="Endpoint serving /v1/models."),
    model: str = typer.Option("", "--model", help="Model id (otherwise picked from the live list)."),
    api_key_env: str = typer.Option("", "--api-key-env", help="Env var holding the endpoint key."),
    provider: str = typer.Option("", "--provider", help="Provider id the agent knows this endpoint by."),
    login: bool = typer.Option(False, "--login", help="Hand the terminal to the agent's own login flow."),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="Run one real prompt through the agent."),
    yes: bool = typer.Option(False, "--yes", "-y", help="No questions — take the defaults and the values passed."),
) -> None:
    """Choose the worker brain — pi/omp × local llama.cpp / OpenAI endpoint / subscription login."""
    _start_file_log(root)
    from .backend_setup import run_backend

    code = run_backend(
        root, harness=harness, source=source, base_url=base_url, model=model,
        api_key_env=api_key_env, provider=provider, login=login, verify=verify, yes=yes,
        log=console.print,
    )
    raise typer.Exit(code=code)


@app.command()
def gates(
    lang: str = typer.Option("", "--lang", help="Run a single language's gates."),
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """Run deterministic verification gates (ADR-0013)."""
    _start_file_log(root)
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
    _start_file_log(root)
    from .config import load_config
    from .pipeline.dispatch import dispatch_node
    from .pipeline.graph import Graph, Node, commit_graph, load_graph, save_graph
    from .pipeline.intake import run_intake
    from .pipeline.reflect import reflect

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
        err.print(f"node {node.id} BLOCKED — {result.evidence or 'no evidence recorded'}")
        raise typer.Exit(code=1)

    console.print("review: fresh-context reviewer reading the lane diff…")
    wt = root / ".factory" / "worktrees" / node.lane
    _review_or_die(root, node, config, wt)
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
    console.print(f"[green]done[/] {node.id} `{node.title}` verdict={node.verdict or 'n/a'}")
    # "done" without saying where the work sits is the question asked next.
    console.print(f"  work: branch [bold]lane/{node.lane}[/] in {wt}")
    console.print(f"  merge: `sfactory release` assembles stage; `sfactory release --approve` moves {config.release.main_branch}")


@app.command()
def wave(
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """Dispatch all ready graph nodes across lanes/worktrees (ADR-0007)."""
    _start_file_log(root)
    from .config import load_config
    from .gitcmd import GitRefused
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
        if node.status != "done":
            continue
        wt = root / ".factory" / "worktrees" / node.lane
        try:
            review_node(root, node, config, cwd=wt)
        except GitRefused as exc:
            # One unreadable lane doesn't cancel the rest of the wave's reviews.
            console.print(f"[red]{node.id} unreviewed[/] — the lane diff could not be read\n{exc}")
            continue
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
    _start_file_log(root)
    from .config import load_config
    from .pipeline.graph import load_graph, save_graph

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
    _review_or_die(root, target, config, wt)
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
    _start_file_log(root)
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
    _start_file_log(root)
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
    provision: bool = typer.Option(
        False, "--provision",
        help="Apply the seed: create company, hire agents, import open graph nodes as tickets (idempotent).",
    ),
) -> None:
    """Provision/inspect the Paperclip company for this project (ADR-0006)."""
    _start_file_log(root)
    from .integrations import paperclip as pc

    root = root.resolve()
    up = pc.server_up()
    console.print(f"paperclip server: {'[green]running[/]' if up else '[yellow]not running[/]'} ({pc.DEFAULT_SERVER})")
    if provision:
        raise typer.Exit(code=pc.provision(root, log=console.print))
    console.print(pc.provision_plan(root))


@app.command()
def status(
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """Show graph, lanes, and last gate evidence."""
    _start_file_log(root)
    import json

    from rich.table import Table

    from . import gitcmd
    from .pipeline.graph import load_graph

    root = root.resolve()
    graph = load_graph(root)

    table = Table(title="graph", title_justify="left")
    for col in ("id", "title", "kind", "risk", "lane", "status", "gates", "verdict"):
        table.add_column(col)
    for n in graph.nodes:
        table.add_row(n.id, n.title[:48], n.kind, n.risk, n.lane, n.status, n.gate_status or "—", n.verdict or "—")
    console.print(table)

    wt = gitcmd.run(root, "worktree", "list", check=False)
    console.print(f"worktrees:\n{wt.stdout.strip() or wt.stderr.strip() or '(none)'}")

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

"""The ``sfactory`` command line (ADR-0011 command set).

Commands are filled in commit-by-commit; each stub states which ADR
governs it so the CLI surface is stable from day one.
"""

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


def _not_implemented(name: str, adr: str) -> None:
    console.print(
        f"[yellow]sfactory {name}[/] is not implemented yet — governed by [bold]{adr}[/], "
        "lands in an upcoming commit (see git log)."
    )
    raise typer.Exit(code=2)


@app.command()
def init(
    target: Path = typer.Argument(Path("."), help="Project directory to scaffold."),
    auto: bool = typer.Option(False, "--auto", help="Enable unattended auto mode (ADR-0007)."),
    langs: str = typer.Option(
        "",
        "--langs",
        help="Comma-separated languages (default: auto-detect). e.g. python,rust,go",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing scaffolded files."),
) -> None:
    """Scaffold the factory into a project (ADR-0012)."""
    from .scaffold import run_init

    run_init(target=target, auto=auto, langs=langs, force=force, log=console.print)


@app.command()
def doctor() -> None:
    """Verify the local factory environment (llama.cpp, pi, hindsight, paperclip, toolchains)."""
    _not_implemented("doctor", "ADR-0011")


@app.command()
def run(
    task: str = typer.Argument(..., help="Task description for Intake."),
    lang: str = typer.Option("", "--lang", help="Force the language/lane (default: Intake decides)."),
) -> None:
    """Run one task through the full pipeline (ADR-0007)."""
    _not_implemented("run", "ADR-0007")


@app.command()
def wave() -> None:
    """Dispatch all ready graph nodes across lanes/worktrees (ADR-0007)."""
    _not_implemented("wave", "ADR-0007")


@app.command()
def gates(
    lang: str = typer.Option("", "--lang", help="Run a single language's gates."),
    root: Path = typer.Option(Path("."), "--root", help="Project root."),
) -> None:
    """Run deterministic verification gates (ADR-0013)."""
    _not_implemented("gates", "ADR-0013")


@app.command()
def review(node: str = typer.Argument("", help="Graph node id (default: current lane work).")) -> None:
    """Fresh-context read-only review with a clean|nits|blockers verdict (ADR-0007)."""
    _not_implemented("review", "ADR-0007")


@app.command()
def release(
    approve: bool = typer.Option(False, "--approve", help="Human approval for the merge to main."),
) -> None:
    """Assemble stage branch; merge to main only with human approval (ADR-0007)."""
    _not_implemented("release", "ADR-0007")


@app.command()
def reflect() -> None:
    """Retain wave/incident learnings in Hindsight + memory/ files (ADR-0005)."""
    _not_implemented("reflect", "ADR-0005")


@app.command()
def paperclip() -> None:
    """Provision/inspect the Paperclip company for this project (ADR-0006)."""
    _not_implemented("paperclip", "ADR-0006")


@app.command()
def status() -> None:
    """Show graph, lanes, gates and spend summary."""
    _not_implemented("status", "ADR-0011")


def main() -> None:  # pragma: no cover
    """Console-script entry point (kept for `python -m` symmetry)."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()

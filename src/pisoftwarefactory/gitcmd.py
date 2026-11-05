"""One git entry point for the whole factory.

Every command we run touches a repository the user pointed us at or a lane
worktree we created, but git's ownership protection refuses both on network
drives and copied directories ("detected dubious ownership"). That refusal used
to look like data — an empty diff, an unchanged lane — and the pipeline then
reported a result that never existed. So each call trusts exactly the directory
it operates on (``-c`` for one invocation; nothing is written to global config),
and a refusal still failing that way is raised, not swallowed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .harnesses import no_window


class GitRefused(RuntimeError):
    """git would not run. Distinct from "ran, and the answer is empty"."""


def run(root: Path, *args: str, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """``git -C <root> <args…>``, scoped-trusted.

    With ``check=True`` a non-zero exit raises GitRefused carrying git's own
    words and the command that clears them; with ``check=False`` the caller
    inspects ``returncode`` itself.
    """
    here = Path(root).as_posix()
    proc = subprocess.run(
        ("git", "-c", f"safe.directory={here}", "-C", here, *args),
        capture_output=True,
        text=True,
        check=False,
        env=env,
        creationflags=no_window(),
    )
    if check and proc.returncode != 0:
        raise GitRefused(_reason(root, args, proc))
    return proc


def _reason(root: Path, args: tuple[str, ...], proc: subprocess.CompletedProcess) -> str:
    detail = ((proc.stderr or "") + (proc.stdout or "")).strip()
    hint = ""
    if "dubious ownership" in detail:
        # Still worth naming: this call was already scoped, so a refusal here
        # means git normalized the path differently (mapped drive, symlink).
        hint = (f"\nfix: git config --global --add safe.directory "
                f"'{Path(root).as_posix()}' — use the forward-slash path git prints above")
    return f"git {' '.join(args)} failed (rc={proc.returncode}): {detail[:500]}{hint}"

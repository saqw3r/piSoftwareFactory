"""``sfactory update`` — reinstall the tool from the source it was installed from.

The install source is recorded by uv in the package's own ``direct_url.json``
metadata, so the command needs no arguments and works on any machine the
same way. A local-path install is refreshed with ``uv tool install --force``;
anything else (registry/git) goes through ``uv tool upgrade``.

Windows caveat: the running ``sfactory.exe`` image is locked by its own
process, so uv cannot replace it while we execute. When the inline refresh
hits that lock, the reinstall is handed to a detached helper that waits for
this process to exit and finishes the job — the user never re-runs anything.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from importlib import metadata
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

PACKAGE = "pisoftwarefactory"

# Signatures of "the exe is in use" on Windows — the self-update deadlock.
_LOCK_MARKS = ("access is denied", "used by another process", "being used")


def install_source() -> dict:
    """Resolve this install's origin from its own dist metadata.

    Returns {"url": str, "editable": bool, "version": str}. Raises
    RuntimeError when the metadata is missing (e.g. a bare source checkout).
    """
    try:
        dist = metadata.distribution(PACKAGE)
    except metadata.PackageNotFoundError as exc:
        raise RuntimeError(f"{PACKAGE} is not installed as a distribution") from exc
    raw = dist.read_text("direct_url.json")
    if not raw:
        raise RuntimeError("no direct_url.json — installed without a recorded source")
    data = json.loads(raw)
    url = data.get("url", "")
    if not url:
        raise RuntimeError("direct_url.json has no url")
    return {
        "url": url,
        "editable": bool(data.get("dir_info", {}).get("editable")),
        "version": dist.version,
    }


def _local_path(url: str) -> str | None:
    """file:// URL -> native path; None for non-file sources."""
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None
    path = url2pathname(parsed.path)
    if parsed.netloc and parsed.netloc not in ("", "localhost"):
        path = f"//{parsed.netloc}{path}"
    return path


def _ps_quote(value: str) -> str:
    return value.replace("'", "''")


def _schedule_after_exit(uv: str, source: str, log_file: Path) -> None:
    """Run the reinstall detached, starting once this process is gone.

    Start-Process with native redirection (not `uv ... 2>&1 | Out-File`):
    uv logs progress to stderr, and piping stderr through PowerShell turns
    every informational line into a scary NativeCommandError record.
    """
    ps = (
        f"Wait-Process -Id {os.getpid()} -Timeout 60 -ErrorAction SilentlyContinue; "
        f"Start-Process -FilePath '{_ps_quote(uv)}' "
        f"-ArgumentList 'tool','install','--force','{_ps_quote(source)}' "
        f"-NoNewWindow -Wait "
        f"-RedirectStandardOutput '{_ps_quote(str(log_file))}.out' "
        f"-RedirectStandardError '{_ps_quote(str(log_file))}'"
    )
    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
    )


def run_update(log=print) -> int:
    """Update this sfactory in place. Returns the process exit code."""
    uv = shutil.which("uv")
    if uv is None:
        log("uv not found on PATH — cannot self-update; install uv (https://docs.astral.sh/uv/)")
        return 1
    try:
        origin = install_source()
    except RuntimeError as exc:
        log(f"cannot determine install source: {exc}")
        return 1

    if origin["editable"]:
        path = _local_path(origin["url"]) or origin["url"]
        log(f"editable install of {path} — the code you run is the repo itself; nothing to update")
        return 0

    local = _local_path(origin["url"])
    source = local or origin["url"]
    if local is not None:
        argv = [uv, "tool", "install", "--force", source]
    else:
        argv = [uv, "tool", "upgrade", PACKAGE]
    log(f"updating from {origin['url']} (current version {origin['version']})…")

    if os.name == "nt":
        # We are executing from the very exe uv must replace — an inline
        # attempt can only fail, and possibly half-replace the env first.
        # Hand the whole job to the post-exit helper instead.
        log_file = Path(os.environ.get("TEMP", ".")) / "sfactory-update.log"
        _schedule_after_exit(uv, source, log_file)
        log(
            "scheduled: the reinstall applies a moment after this command exits "
            f"(sfactory.exe cannot replace itself while running).\n"
            f"verify with `sfactory --version`; raw output: {log_file}"
        )
        return 0

    proc = subprocess.run(argv, capture_output=True, text=True, timeout=600, check=False)
    output = (proc.stdout or "") + (proc.stderr or "")
    for line in output.splitlines():
        if line.strip():
            log(f"  │ {line[:300]}")

    if proc.returncode == 0:
        log("updated — the new version is live on your next `sfactory` run")
        return 0

    if any(mark in output.lower() for mark in _LOCK_MARKS):
        log_file = Path(os.environ.get("TEMP", ".")) / "sfactory-update.log"
        _schedule_after_exit(uv, source, log_file)
        log(
            "the running sfactory locked itself — reinstall scheduled for right "
            f"after this command exits; details: {log_file}"
        )
        return 0

    log(f"update failed (exit {proc.returncode}) — see output above")
    return 1

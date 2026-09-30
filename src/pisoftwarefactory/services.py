"""Service provisioning (ADR-0015): install + start Hindsight and Paperclip.

Both services are optional to the pipeline (ADR-0005/0006): every failure
here degrades to a warning with a log path. Idempotent — running services
are detected and left alone. On Windows processes spawn detached
(`DETACHED_PROCESS`), on POSIX via `start_new_session`; PID and log live
under `.factory/run/`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .config import FactoryConfig
from .integrations import hindsight, paperclip

# Start-up allowances: first start builds an embedded store / downloads
# packages, so these are generous; all waits are non-fatal.
HINDSIGHT_START_TIMEOUT = 240
PAPERCLIP_START_TIMEOUT = 300


def _run_dir(root: Path) -> Path:
    d = root / ".factory" / "run"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(
            ("tasklist", "/FI", f"PID eq {pid}"), capture_output=True, text=True
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _pid_state(pidfile: Path) -> tuple[bool, int | None]:
    if not pidfile.is_file():
        return False, None
    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip())
    except ValueError:
        return False, None
    return _pid_alive(pid), pid


def _spawn_detached(argv: list[str], env: dict[str, str], logfile: Path, pidfile: Path) -> int:
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with logfile.open("ab") as fh:
        proc = subprocess.Popen(
            argv,
            env=env,
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **kwargs,
        )
    pidfile.write_text(str(proc.pid) + "\n", encoding="utf-8")
    return proc.pid


def _wait_up(check, timeout: int) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(4)
    return False


def hindsight_env(config: FactoryConfig) -> dict[str, str]:
    """llamacpp-backed server env (ADR-0005/ADR-0010): same backend as workers."""
    env = {**os.environ}
    env.update(
        {
            "HINDSIGHT_API_LLM_PROVIDER": "llamacpp",
            "HINDSIGHT_API_LLM_API_BASE": config.backend.base_url,
            "HINDSIGHT_API_LLM_MODEL": config.backend.model,
            "HINDSIGHT_API_LLM_API_KEY": "none",
            "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "local",
        }
    )
    return env


def _find_hindsight_exe(log) -> str | None:
    exe = shutil.which("hindsight-api")
    if exe:
        return exe
    uv = shutil.which("uv")
    if uv is None:
        return None
    log("  installing hindsight-api (uv tool install --force hindsight-api; isolated env)…")
    proc = subprocess.run(
        (uv, "tool", "install", "--force", "hindsight-api"),
        capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        log(f"  hindsight-api install failed: {(proc.stderr or proc.stdout)[-300:]}")
        return None
    bin_dir = Path.home() / ".local" / "bin"
    for candidate in sorted(bin_dir.glob("hindsight-api*")):
        return str(candidate)
    return shutil.which("hindsight-api")


def setup_hindsight(root: Path, config: FactoryConfig, log=print) -> str:
    run_dir = _run_dir(root)
    if hindsight.is_up(config.memory.hindsight_url):
        return "already-running"
    (run_dir / "hindsight.pid").unlink(missing_ok=True)
    exe = _find_hindsight_exe(log)
    if exe is None:
        return "unavailable — install uv or pip install hindsight-api (see hindsight.bootstrap.md)"

    log("  starting hindsight-api (detached; first start builds its embedded store)…")
    _spawn_detached(
        [exe],
        hindsight_env(config),
        run_dir / "hindsight.log",
        run_dir / "hindsight.pid",
    )
    if _wait_up(lambda: hindsight.is_up(config.memory.hindsight_url), HINDSIGHT_START_TIMEOUT):
        return "started"
    return "starting — not responding yet; check .factory/run/hindsight.log"


def _paperclip_cli(npx: str, *args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    """Run a paperclipai subcommand, capturing output for diagnostics."""
    return subprocess.run(
        (npx, "--yes", "paperclipai", *args),
        capture_output=True, text=True, timeout=timeout,
    )


def setup_paperclip(root: Path, log=print) -> str:
    run_dir = _run_dir(root)
    if paperclip.server_up():
        return "already-running"
    # A pidfile only proves "already-running" if the server actually answers;
    # PIDs get reused on Windows, so a stale file is cleared, not trusted.
    (run_dir / "paperclip.pid").unlink(missing_ok=True)
    npx = shutil.which("npx")
    if npx is None:
        return "unavailable — install Node.js 24.11+ (npx required)"

    instance = Path.home() / ".paperclip" / "instances" / "default"
    if instance.exists():
        log(f"  existing Paperclip instance detected at {instance} — preserved untouched")

    # Non-interactive first-run config (idempotent): `onboard --yes` accepts
    # quickstart defaults AND starts the server immediately — so after it, the
    # port decides; a follow-up `run` happens only if it did not come up.
    onboard = _paperclip_cli(npx, "onboard", "--yes", "--no-install-service", timeout=900)
    if onboard.returncode != 0 and not (instance / "config.json").is_file():
        (run_dir / "paperclip.log").write_text(
            (onboard.stdout + onboard.stderr)[-4000:], encoding="utf-8"
        )
        return "failed at onboard — see .factory/run/paperclip.log"

    if paperclip.server_up():
        return "started"

    # `service` management is unsupported on win32 ("use paperclipai run"):
    # POSIX gets the registered background service, Windows a detached run.
    log("  server not up after onboard — starting detached `paperclipai run`…")
    if os.name != "nt":
        installed = _paperclip_cli(npx, "service", "install")
        if installed.returncode == 0:
            _paperclip_cli(npx, "service", "start", timeout=300)
        else:
            _spawn_detached([npx, "--yes", "paperclipai", "run"], {**os.environ},
                            run_dir / "paperclip.log", run_dir / "paperclip.pid")
    else:
        _spawn_detached([npx, "--yes", "paperclipai", "run"], {**os.environ},
                        run_dir / "paperclip.log", run_dir / "paperclip.pid")

    if _wait_up(lambda: paperclip.server_up(), PAPERCLIP_START_TIMEOUT):
        return "started"

    logtail = ""
    logfile = run_dir / "paperclip.log"
    if logfile.is_file():
        logtail = logfile.read_text(encoding="utf-8", errors="replace")[-2000:]
    if "failed to start" in logtail or "already exists" in logtail:
        return (
            "failed — the existing Paperclip database rejected this CLI version's migrations; "
            "it was preserved untouched. Remediation is yours: `npx paperclipai update`, start "
            "your instance the way you normally do, or (only if that data is disposable) reset "
            "the instance yourself. Diagnostics: .factory/run/paperclip.log"
        )
    return "starting — not responding yet; check .factory/run/paperclip.log"


def setup_all(root: Path, config: FactoryConfig, log=print) -> dict[str, str]:
    return {
        "hindsight": setup_hindsight(root, config, log),
        "paperclip": setup_paperclip(root, log),
    }


def status(root: Path, config: FactoryConfig) -> dict[str, str]:
    run_dir = root / ".factory" / "run"
    h_alive, h_pid = _pid_state(run_dir / "hindsight.pid")
    p_alive, p_pid = _pid_state(run_dir / "paperclip.pid")
    return {
        "hindsight": "up" if hindsight.is_up(config.memory.hindsight_url) else ("pidfile-alive" if h_alive else "down"),
        "hindsight_pid": str(h_pid) if h_pid else "-",
        "paperclip": "up" if paperclip.server_up() else ("pidfile-alive" if p_alive else "down"),
        "paperclip_pid": str(p_pid) if p_pid else "-",
    }


def _stop_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(("taskkill", "/PID", str(pid), "/T", "/F"), capture_output=True)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def stop_all(root: Path, log=print) -> dict[str, str]:
    run_dir = root / ".factory" / "run"
    result: dict[str, str] = {}
    for name in ("hindsight", "paperclip"):
        alive, pid = _pid_state(run_dir / f"{name}.pid")
        if alive and pid:
            _stop_pid(pid)
            result[name] = f"stopped (pid {pid})"
        else:
            result[name] = "not running (no pidfile)"
    return result

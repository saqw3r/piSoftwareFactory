"""Service provisioning (ADR-0015): install + start Hindsight and Paperclip.

Both services are optional to the pipeline (ADR-0005/0006): every failure
here degrades to a warning with a log path. Idempotent — running services
are detected and left alone. On Windows processes spawn detached
(`DETACHED_PROCESS`), on POSIX via `start_new_session`; PID and log live
under `.factory/run/`.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
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
        # NEW_PROCESS_GROUP + NO_WINDOW, deliberately WITHOUT Detached:
        # DETACHED_PROCESS breaks stdout/stderr inheritance across the
        # cmd.exe shim boundary (npx/paperclip are .CMD) — the child exits 0
        # while the log stays empty and the real error is lost. Without a
        # console of its own the child still survives terminal close, output
        # flows to the logfile, and no window flashes.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
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


# Steady-state chatter these servers emit every ~30s; useless while waiting
# for a start and the reason run logs grow to tens of MB unrotated.
_NOISE_MARKS = ("[WORKER_STATS]",)


def _is_noise(line: str) -> bool:
    return any(mark in line for mark in _NOISE_MARKS)


def _stream_new(logfile: Path | None, pos: int, log) -> int:
    """Print service-log lines appended since *pos*; return the new offset.

    Lets `setup` show real start-up progress live instead of a blind
    heartbeat, so nobody has to open the file to tell 'building its store'
    from 'stuck'. The file is opened by the child for append; reading it
    concurrently is fine on both platforms.
    """
    if logfile is None:
        return pos
    try:
        with logfile.open("rb") as fh:
            fh.seek(pos)
            chunk = fh.read()
            new_pos = fh.tell()
    except OSError:
        return pos
    for line in chunk.decode("utf-8", errors="replace").splitlines():
        if line.strip() and not _is_noise(line):
            log(f"  │ {line[:300]}")
    return new_pos


def _wait_up(check, timeout: int, log=print, label: str = "service", died=None,
             logfile: Path | None = None) -> bool:
    """Poll *check* until true or *timeout*, streaming new *logfile* lines as progress.

    *died* is an optional zero-arg predicate: when the server process exits
    before its port answers, waiting the full timeout is pointless — report
    early so the caller can surface the log tail instead.
    """
    start = time.monotonic()
    last_output = pos = 0
    last_report = start
    while time.monotonic() - start < timeout:
        if check():
            return True
        if died is not None and died():
            log(f"  {label} process exited before its port answered")
            return False
        time.sleep(2)
        new_pos = _stream_new(logfile, pos, log)
        if new_pos != pos:
            last_output = time.monotonic()
        pos = new_pos
        # Heartbeat only when the service itself is silent — otherwise its
        # own streamed lines already prove the wait is progressing.
        if time.monotonic() - last_report >= 30 and time.monotonic() - last_output >= 10:
            last_report = time.monotonic()
            log(f"  still waiting for {label}… ({time.monotonic() - start:.0f}s elapsed, no new output)")
    return False


def _log_tail(logfile: Path, n: int = 15) -> str:
    """Last *n* meaningful lines of a run log for inline error reports.

    Reads only the file's tail (run logs can be tens of MB) and drops the
    steady-state chatter so the lines that remain are the diagnosis.
    """
    try:
        size = logfile.stat().st_size
        with logfile.open("rb") as fh:
            fh.seek(max(0, size - 65536))
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return "(no log file)"
    lines = [l for l in text.splitlines() if l.strip() and not _is_noise(l)]
    if size > 65536 and lines:
        lines[0] = "… " + lines[0]
    return "\n".join(lines[-n:]) if lines else "(no output yet)"


def _stale_postgres_hint(output: str) -> str:
    """Remediation when embedded PostgreSQL refuses a taken-over data dir.

    Leftover postgres processes (earlier starts, crashed runs) hold shared
    memory on the data dir; the data itself is fine — only the squatters
    must go. Reuse is impossible here by construction: a live Paperclip on
    :3100 would already have been kept as `already-running`, but an orphaned
    postmaster without its server can't be adopted — Paperclip exclusively
    owns its data dir and won't attach to a foreign one. Killing the
    processes is data-safe (WAL recovery on next start). Returns "" when
    the signature is absent.
    """
    if "shared memory block is still in use" not in output:
        return ""
    if os.name == "nt":
        kill = "taskkill /F /IM postgres.exe"
    else:
        kill = "pkill postgres"
    return (
        f"\nStale embedded-PostgreSQL processes hold the data dir (leftovers from an "
        f"earlier start — data is intact). Kill them and retry: `{kill}`, then "
        f"`sfactory services setup`. If the shared-memory block survives that, reboot."
    )


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


def _reset_log(logfile: Path) -> None:
    """Start each spawn with a fresh log so streamed lines/tail are this start's.

    A still-open handle (orphan holding the file) is not fatal: the append
    simply continues in the old file.
    """
    try:
        logfile.write_bytes(b"")
    except OSError:
        pass


def setup_hindsight(root: Path, config: FactoryConfig, log=print) -> str:
    run_dir = _run_dir(root)
    if hindsight.is_up(config.memory.hindsight_url):
        return "already-running"
    (run_dir / "hindsight.pid").unlink(missing_ok=True)
    exe = _find_hindsight_exe(log)
    if exe is None:
        return "unavailable — install uv or pip install hindsight-api (see hindsight.bootstrap.md)"

    log("  starting hindsight-api (detached; first start builds its embedded store)…")
    logfile = run_dir / "hindsight.log"
    _reset_log(logfile)
    h_pid = _spawn_detached(
        [exe],
        hindsight_env(config),
        logfile,
        run_dir / "hindsight.pid",
    )
    if _wait_up(lambda: hindsight.is_up(config.memory.hindsight_url), HINDSIGHT_START_TIMEOUT,
                log, "hindsight", died=lambda: not _pid_alive(h_pid), logfile=logfile):
        return "started"
    tail = _log_tail(logfile)
    if not _pid_alive(h_pid):
        return ("hindsight-api exited before its port answered — output:\n" + tail)
    return (f"hindsight-api not answering after {HINDSIGHT_START_TIMEOUT}s — last output:\n"
            + tail + _stale_postgres_hint(tail))


def _paperclip_cli(npx: str, *args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    """Run a paperclipai subcommand, capturing output for diagnostics.

    stdin is detached: without this, an unexpected interactive prompt
    (e.g. onboard facing an existing instance) inherits the user's terminal
    while stdout is captured — a silent hang with no visible question.
    With DEVNULL it fails fast and the caller falls through to `run`.
    """
    return subprocess.run(
        (npx, "--yes", "paperclipai", *args),
        capture_output=True, text=True, timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def _run_onboard(npx: str, run_dir: Path, log=print, timeout: int = 900) -> tuple[str | None, int | None, str]:
    """Stream `paperclipai onboard --yes` live; accept a running server as success.

    onboard keeps its server in-process in the foreground: waiting for the
    process to *exit* can hang forever next to a healthy server (observed:
    `Server startup recovery complete`, process never returns). So while
    streaming each line to console + logfile, we also poll :3100 — whichever
    wins first decides.

    Returns (status, returncode, output): *status* set means done (server
    answering — left running, pid tracked for `services stop`; or timed-out
    handling by the caller via TimeoutExpired). *status* None + returncode
    means onboard exited without the server — caller falls through to `run`.
    """
    log("  running `paperclipai onboard --yes` (streams live; up to 15 min on first download)…")
    argv = (npx, "--yes", "paperclipai", "onboard", "--yes", "--no-install-service")
    logfile = run_dir / "paperclip.log"
    popen_kwargs: dict = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        # Same no-window detached spawn as servers: output flows via the pipe,
        # terminal close doesn't take a serving onboard down with it.
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", bufsize=1,
        **popen_kwargs,
    )
    line_queue: queue.Queue[str | None] = queue.Queue()

    def _reader() -> None:
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                line_queue.put(line)
        finally:
            line_queue.put(None)  # EOF sentinel

    threading.Thread(target=_reader, daemon=True).start()
    deadline = time.monotonic() + timeout
    chunks: list[str] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            proc.kill()
            raise subprocess.TimeoutExpired(argv, timeout)
        try:
            item = line_queue.get(timeout=min(remaining, 4))
        except queue.Empty:
            item = None
        if item is None:
            # Quiet tick (or EOF): server up wins, exited process ends it.
            if paperclip.server_up():
                break
            if proc.poll() is not None:
                break
            continue
        chunks.append(item)
        if item.strip():
            log(f"  {item.rstrip()}")
        with logfile.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write(item)
        if paperclip.server_up():
            break
    output = "".join(chunks)
    if paperclip.server_up():
        # Onboard's server is serving: leave it running and track it so
        # `services status/stop` work (never kill a serving embedded-pg).
        (run_dir / "paperclip.pid").write_text(str(proc.pid) + "\n", encoding="utf-8")
        return (f"started — onboard's server is answering on :3100 (tracked pid {proc.pid})", None, output)
    rc = proc.poll()
    if rc is None:  # stdout closed but process lingering — last resort
        proc.kill()
        rc = proc.wait()
    else:
        proc.wait()
    return (None, rc, output)


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
    try:
        onboard_status, onboard_rc, onboard_output = _run_onboard(npx, run_dir, log)
    except subprocess.TimeoutExpired:
        return "timed out at onboard after 15 min — see .factory/run/paperclip.log; retry or start your instance manually"
    if onboard_status is not None:
        return onboard_status
    if onboard_rc != 0 and not (instance / "config.json").is_file():
        (run_dir / "paperclip.log").write_text(onboard_output[-4000:], encoding="utf-8")
        tail = "\n".join(onboard_output.splitlines()[-15:]) or "(empty output)"
        return "failed at onboard — last output:\n" + tail

    if paperclip.server_up():
        return "started"

    # `service` management is unsupported on win32 ("use paperclipai run"):
    # POSIX gets the registered background service, Windows a detached run.
    log("  server not up after onboard — starting detached `paperclipai run`…")
    run_pid: int | None = None
    if os.name != "nt":
        installed = _paperclip_cli(npx, "service", "install")
        if installed.returncode == 0:
            _paperclip_cli(npx, "service", "start", timeout=300)
        else:
            run_pid = _spawn_detached([npx, "--yes", "paperclipai", "run"], {**os.environ},
                                       run_dir / "paperclip.log", run_dir / "paperclip.pid")
    else:
        run_pid = _spawn_detached([npx, "--yes", "paperclipai", "run"], {**os.environ},
                                    run_dir / "paperclip.log", run_dir / "paperclip.pid")

    died = (lambda: not _pid_alive(run_pid)) if run_pid is not None else None
    logfile = run_dir / "paperclip.log"
    if _wait_up(lambda: paperclip.server_up(), PAPERCLIP_START_TIMEOUT, log, "paperclip",
                died=died, logfile=logfile):
        return "started"
    tail = _log_tail(logfile)
    if run_pid is not None and not _pid_alive(run_pid):
        return ("paperclip `run` exited before :3100 answered — output:\n"
                + tail + _stale_postgres_hint(tail))

    logtail = ""
    if logfile.is_file():
        logtail = logfile.read_text(encoding="utf-8", errors="replace")[-2000:]
    if "shared memory block is still in use" in logtail:
        return "failed — stale postgres processes hold the data dir." + _stale_postgres_hint(logtail)
    if "failed to start" in logtail or "already exists" in logtail:
        return (
            "failed — the existing Paperclip database rejected this CLI version's migrations; "
            "it was preserved untouched. Remediation is yours: `npx paperclipai update`, start "
            "your instance the way you normally do, or (only if that data is disposable) reset "
            "the instance yourself. Last output:\n" + tail
        )
    return (f"paperclip not answering on :3100 after {PAPERCLIP_START_TIMEOUT}s — last output:\n"
            + tail + _stale_postgres_hint(tail))


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

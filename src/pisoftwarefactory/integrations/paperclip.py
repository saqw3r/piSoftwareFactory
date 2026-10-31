"""Paperclip integration (ADR-0006).

Provisioning drives the ``paperclipai`` CLI with ``--json`` output (full
parity with the control-plane API; auth resolves from the local onboard
profile). Every step is idempotent: existing companies/agents/tickets are
detected and left alone, so ``sfactory paperclip --provision`` can be
re-run freely and prints one state line per step.

Server availability is delegated to services.setup_paperclip — this module
assumes :3100 answers. The factory engine never depends on Paperclip: all
failures here degrade to a report, never a crash.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import httpx

DEFAULT_SERVER = "http://127.0.0.1:3100"


class PaperclipError(RuntimeError):
    """A paperclipai CLI invocation failed — message carries the server's own words."""


def server_up(server: str = DEFAULT_SERVER, timeout: float = 3.0) -> bool:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            return client.get(server).status_code < 500
    except httpx.HTTPError:
        return False


def load_company_seed(root: Path) -> dict:
    path = root / "paperclip.company.json"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found — run `sfactory init` first.")
    return json.loads(path.read_text(encoding="utf-8"))


def _find_paperclip_js() -> str | None:
    """Locate the cached paperclipai CLI entry (dist/index.js) under the npm _npx cache."""
    import glob

    caches = [
        Path(os.environ.get("LOCALAPPDATA", Path.home())) / "npm-cache" / "_npx",
        Path.home() / ".npm" / "_npx",
    ]
    for cache in caches:
        hits = sorted(glob.glob(str(cache / "*" / "node_modules" / "paperclipai" / "dist" / "index.js")))
        if hits:
            return hits[-1]
    return None


def _cli_argv(*args: str) -> list[str] | None:
    """Build the invocation argv, preferring node -> dist/index.js.

    The npx .CMD shim re-parses the command line through cmd.exe, which
    mangles JSON payloads (quotes confuse its parser, then `|`/`&` inside
    payloads execute as pipes). Calling node.exe directly keeps every
    payload byte intact. Returns None when neither path is available.
    """
    node = shutil.which("node")
    js = _find_paperclip_js()
    if node and js:
        return [node, js, *args]
    npx = shutil.which("npx")
    if npx:
        return [npx, "--yes", "paperclipai", *args]
    return None


def _cli(*args: str, timeout: int = 120) -> object:
    """Run the paperclipai CLI with ``--json`` and parse the reply.

    Errors keep the server's validation message verbatim so the caller can
    print the cause inline — nobody is sent to a log file.
    """
    argv = _cli_argv(*args, "--json")
    if argv is None:
        raise PaperclipError("neither node+npx nor npx found — install Node.js 24.11+")
    proc = subprocess.run(
        argv,
        capture_output=True, text=True, timeout=timeout,
        stdin=subprocess.DEVNULL, check=False,
    )
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        detail = (proc.stderr or out or "").strip()
        raise PaperclipError(f"paperclipai {' '.join(args[:2])} failed: {detail[-500:]}")
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        # Some subcommands print plain text even with --json; hand it back raw.
        return out


def _issue_prefix(name: str) -> str:
    words = [w for w in name.replace("_", " ").replace("-", " ").split() if w.isalnum()]
    base = "".join(w[0].upper() for w in words) or name[:4].upper()
    return base[:6] if len(base) >= 2 else (base + "X")[:2]


def _company_list() -> list[dict]:
    data = _cli("company", "list")
    return data if isinstance(data, list) else []


def ensure_company(seed: dict) -> tuple[str, str, str | None]:
    """(state, detail, company_id) — creates the seeded company unless it exists."""
    company = seed.get("company", {})
    name = company.get("name", "")
    if not name:
        return "fail", "paperclip.company.json has no company.name", None
    for c in _company_list():
        if c.get("name") == name:
            return "ok", f"company '{name}' already exists", c.get("id")
    created = _cli("company", "create", "--payload-json", json.dumps(
        {"name": name, "issuePrefix": _issue_prefix(name)}
    ))
    cid = created.get("id") if isinstance(created, dict) else None
    return "changed", f"created company '{name}' ({cid or 'id unknown'})", cid


def _pi_model(harness: dict) -> str:
    """pi wants <provider>/<model>; factory.toml spells the local one 'llamacpp'
    (the display label 'llama.cpp' in pi --list-models is NOT the key)."""
    provider = harness.get("provider") or "llamacpp"
    return f"{provider}/{harness.get('model', 'default')}"


def _pi_env(provider_key: str) -> dict[str, str]:
    """Local llama.cpp/OpenAI-compatible endpoints ignore the key but pi refuses
    to run without one — hand it a dummy via the documented env var."""
    return {provider_key.upper().replace("-", "_") + "_API_KEY": "none"}


def ensure_agents(cid: str, seed: dict, root: Path) -> list[tuple[str, str]]:
    """One pi_local agent per seeded role; existing titles are left alone."""
    existing = _cli("agent", "list", "-C", cid)
    by_name = {a.get("name"): a for a in existing} if isinstance(existing, list) else {}
    company = seed.get("company", {})
    harness = company.get("harness", {})
    provider = harness.get("provider") or "llamacpp"
    results: list[tuple[str, str]] = []
    for role in company.get("roles", []):
        title = role.get("title", "")
        if not title:
            continue
        payload = {
            "name": title,
            "adapterType": "pi_local",
            "adapterConfig": {
                "model": _pi_model(harness),
                "cwd": str(root),
                "env": _pi_env(provider),
            },
            "capabilities": role.get("duties", ""),
        }
        try:
            agent = by_name.get(title)
            if agent is not None:
                cfg = agent.get("adapterConfig") or {}
                if agent.get("adapterType") == "pi_local" and cfg.get("model") == _pi_model(harness):
                    results.append(("ok", f"agent '{title}' already hired"))
                    continue
                _cli("agent", "update", agent["id"], "--payload-json", json.dumps(payload))
                results.append(("changed", f"rewired agent '{title}' to pi_local ({_pi_model(harness)})"))
                continue
            _cli("agent", "create", "-C", cid, "--payload-json", json.dumps(payload))
            results.append(("changed", f"hired agent '{title}' (pi_local, {_pi_model(harness)})"))
        except PaperclipError as exc:
            results.append(("fail", f"agent '{title}': {exc}"))
    return results


def ensure_tickets(cid: str, nodes: list) -> list[tuple[str, str]]:
    """One issue per open graph node; idempotent via the [node-id] title marker."""
    existing = _cli("issue", "list", "-C", cid)
    titles = {i.get("title", "") for i in existing} if isinstance(existing, list) else set()
    results: list[tuple[str, str]] = []
    for n in nodes:
        marker = f"[{n.id}]"
        if any(marker in t for t in titles):
            continue  # silent ok — the ticket list would drown otherwise
        try:
            _cli("issue", "create", "-C", cid,
                 "--title", f"{marker} {n.title}"[:200],
                 "--description", (n.task or "")[:2000],
                 "--priority", "high" if n.risk == "high" else "medium")
            results.append(("changed", f"ticket for {n.id} — {n.title[:40]}"))
        except PaperclipError as exc:
            results.append(("fail", f"ticket {n.id}: {exc}"))
    if not results:
        results.append(("ok", "every open graph node already has a ticket"))
    return results


def provision(root: Path, log=print) -> int:
    """seed → company → agents → tickets, idempotently. Returns exit code."""
    if not server_up():
        log("paperclip server not running — bring it up first: `sfactory services setup`")
        return 1
    try:
        seed = load_company_seed(root)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        log(f"cannot load company seed: {exc}")
        return 1

    harness_name = seed.get("company", {}).get("harness", {}).get("name", "pi")
    if harness_name != "pi":
        log(f"  [skip] Paperclip has no {harness_name} adapter (its adapters are fixed: pi_local, "
            "claude_local, codex_local, opencode_local, gemini_local, grok_local, kimi_local, "
            "hermes_local, paperclip_runner, process, http) — agents are hired on pi_local against "
            f"the same endpoint and model. `{harness_name}` still runs this project's pipeline via "
            "`sfactory run` / `sfactory wave`.")

    rc = 0
    try:
        state, detail, cid = ensure_company(seed)
        log(f"  [{state}] {detail}")
        rc = rc or (state == "fail")
        if not cid:
            log("  [fail] no company id to continue with")
            return 1
        steps = ensure_agents(cid, seed, root)
        try:
            from ..pipeline.graph import load_graph

            open_nodes = [n for n in load_graph(root).nodes
                          if n.status in ("intake", "ready", "in_progress", "blocked")]
            steps += ensure_tickets(cid, open_nodes)
        except FileNotFoundError:
            steps.append(("skip", "no graph.yaml — nothing to import (run `sfactory intake` first)"))
        for state, detail in steps:
            log(f"  [{state}] {detail}")
            rc = rc or (state == "fail")
    except PaperclipError as exc:
        log(f"  [fail] {exc}")
        return 1
    return rc


def provision_plan(root: Path, server: str = DEFAULT_SERVER) -> str:
    """Human-readable summary of the seed and what --provision will do."""
    seed = load_company_seed(root)
    company = seed.get("company", {})
    roles = company.get("roles", [])
    lines = [
        f"company: {company.get('name', '?')}",
        f"goal: {company.get('goal', '?')}",
        "roles:",
    ]
    lines += [f"  - {r.get('title')} → {r.get('reports_to')}: {r.get('duties')}" for r in roles]
    lines += [
        "",
        "with --provision this command does, idempotently:",
        f"  1. create the company above on {server} (skipped when it exists)",
        "  2. hire one agent per role (skipped per role when already hired)",
        "  3. import open graph nodes as tickets (matched by [node-id] in title)",
        "  release gate stays human (ADR-0007): approve in the Paperclip UI/board",
        "  the server itself comes up via: sfactory services setup",
    ]
    return "\n".join(lines)

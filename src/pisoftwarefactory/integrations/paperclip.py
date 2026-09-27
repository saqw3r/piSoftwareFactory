"""Paperclip integration (ADR-0006).

v0.1 scope: verify the local server, validate the company seed, and print
the exact provisioning path. Full API automation lands after the adapter
spec is verified against docs.paperclip.ing (noted in ADR-0006); the
factory engine does not depend on Paperclip being present.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

DEFAULT_SERVER = "http://127.0.0.1:3100"


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


def provision_plan(root: Path, server: str = DEFAULT_SERVER) -> str:
    """Human-readable provisioning steps (v0.1: printed, not automated)."""
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
        "provisioning steps:",
        "  1. npx paperclipai onboard --yes        # once; Node 24.11+, server on " + server,
        "  2. open the Paperclip UI and create the company above",
        "  3. hire agents per role; wire the worker harness command from paperclip.company.json",
        "     (harness: pi via the Bash/HTTP adapter — 'if it can receive a heartbeat, it's hired')",
        "  4. set the release gate to require board approval (human gate, ADR-0007)",
        "  5. import graph nodes as tickets: sfactory wave --ticket-from paperclip (roadmap)",
    ]
    return "\n".join(lines)

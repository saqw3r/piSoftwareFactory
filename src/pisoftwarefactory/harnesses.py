"""Coding-agent harnesses (ADR-0002/ADR-0017): pi and omp behind one registry.

Both agents resolve a model through a `providers` block in their own agent
directory, with the same field names — pi keeps it as JSON
(``~/.pi/agent/models.json``), omp as YAML (``~/.omp/agent/models.yml``). So
the factory writes one provider block in whichever format the harness reads,
and the project's ``[backend]`` in factory.toml stays the single source of
truth for URL + model + key.

omp's agent dir is asked from omp itself (``omp config path``) rather than
guessed: profiles and XDG layouts move it, and a wrong path means the provider
silently never appears in the harness.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Literal

import yaml

from .config import Backend

HarnessName = Literal["pi", "omp"]

HARNESSES: tuple[str, ...] = ("pi", "omp")

# pi's models.json vs omp's models.yml — same schema, different serializer.
_REGISTRY_FILE = {"pi": ("models.json", "json"), "omp": ("models.yml", "yaml")}

_VERIFY_PROMPT = "Reply with exactly: OK"


class HarnessError(RuntimeError):
    """The harness can't serve this backend — message carries the remedy."""


def installed(name: str) -> bool:
    return shutil.which(name) is not None


def binary(name: str) -> str:
    """Absolute path of the harness launcher.

    Windows cannot CreateProcess an extensionless `pi` — npm installs it as
    `pi.CMD`, which only runs when the extension is part of the path handed to
    the API. Spawning the resolved path keeps the .cmd shim (and its own
    node/python launchers) working without going through a shell.
    """
    return shutil.which(name) or name


def agent_dir(name: str) -> Path:
    """Where the harness keeps auth + model registry (env override first)."""
    override = os.environ.get("PI_CODING_AGENT_DIR" if name == "pi" else "OMP_CODING_AGENT_DIR")
    if override:
        return Path(override)
    if name == "omp":
        out = subprocess.run(
            ("omp", "config", "path"), capture_output=True, text=True, check=False
        ).stdout.strip()
        if out and Path(out).is_dir():
            return Path(out)
        return Path.home() / ".omp" / "agent"
    return Path.home() / ".pi" / "agent"


def registry_path(name: str) -> Path:
    filename, _fmt = _REGISTRY_FILE[name]
    return agent_dir(name) / filename


def provider_block(backend: Backend) -> dict:
    """The harness-side view of ``[backend]``: URL + key + the id the server serves.

    The registry stores the model name exactly as the endpoint answers to it —
    it is what the harness puts on the wire. The ``provider/`` prefix some
    harnesses want on the command line is selection, not identity; see
    :func:`selectable_model_id`. (Proven live: an omp registry id of
    ``llamacpp/qwen3.5-9b`` reaches llama-router verbatim and it answers 400
    "model not found".)
    """
    return {
        "baseUrl": backend.base_url,
        "api": backend.api,
        "apiKey": backend.resolve_api_key(),
        "models": [{"id": backend.model}],
    }


def _harness_key_style(name: str) -> str:
    """How this harness spells a model id: omp selects ``provider/id``, pi the bare id."""
    return "provider/id" if name == "omp" else "bare"


def selectable_model_id(name: str, backend: Backend) -> str:
    """The model as this harness wants it *on the command line*.

    omp selects `provider/id`; pi is given `--provider` and the bare id. The
    registry itself always stores the served id — see :func:`provider_block`.
    """
    if _harness_key_style(name) == "provider/id" and "/" not in backend.model:
        return f"{backend.provider}/{backend.model}"
    return backend.model


def registry_load(name: str) -> dict | None:
    """Existing registry, or None when absent/unreadable (never fatal here)."""
    path = registry_path(name)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text) if _REGISTRY_FILE[name][1] == "json" else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError):
        raise HarnessError(
            f"{path} is not valid {_REGISTRY_FILE[name][1]} — fix or remove it, then re-run "
            "`sfactory backend` (the factory will not overwrite a file it cannot parse)."
        ) from None
    return data if isinstance(data, dict) else {}


def _backup(path: Path) -> None:
    bak = path.with_name(path.name + ".bak")
    if not bak.exists():
        shutil.copy2(path, bak)


def register_provider(name: str, backend: Backend, log=print) -> str:
    """Merge the backend's provider block into the harness registry (idempotent).

    Returns `registered` / `already-registered` / `created`. A file we cannot
    parse raises with the fix; nothing is written blind over it.
    """
    path = registry_path(name)
    block = provider_block(backend)

    data = registry_load(name)
    providers = (data or {}).get("providers") if data is not None else None
    if isinstance(providers, dict) and providers.get(backend.provider) == block:
        log(f"  [green]{name} provider ok[/] {backend.provider} → {backend.base_url} ({path})")
        return "already-registered"

    if backend.api_key_env and backend.resolve_api_key() == "none":
        log(f"  [yellow]${backend.api_key_env} is not set[/] — wrote placeholder key for provider "
            f"{backend.provider!r}; export it before dispatching")
    if data is None:
        data = {"providers": {backend.provider: block}}
        created = True
    else:
        if not isinstance(data.get("providers"), dict):
            data = {**data, "providers": {}}
        data["providers"][backend.provider] = block
        created = False
        if path.is_file():
            _backup(path)

    path.parent.mkdir(parents=True, exist_ok=True)
    if _REGISTRY_FILE[name][1] == "json":
        payload = json.dumps(data, indent=2) + "\n"
    else:
        payload = yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    path.write_text(payload, encoding="utf-8", newline="\n")
    log(f"  [green]{'created' if created else 'registered'}[/] {name} provider "
        f"{backend.provider} → {backend.base_url} in {path}")
    return "created" if created else "registered"


def list_models(backend: Backend, harness: str | None = None) -> list[str]:
    """Model ids selectable for this backend: endpoint catalog, else harness catalog."""
    ids = endpoint_models(backend)
    if ids:
        return ids
    if harness and installed(harness):
        return harness_models(harness)
    return []


def endpoint_models(backend: Backend) -> list[str]:
    """GET {base_url}/models — works for llama.cpp and OpenAI-compatible gates."""
    import httpx

    url = backend.base_url.rstrip("/") + "/models"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {backend.resolve_api_key()}"})
            resp.raise_for_status()
            body = resp.json()
    except Exception:  # noqa: BLE001 — probing a foreign endpoint is best-effort
        return []
    data = body.get("data", body) if isinstance(body, dict) else body
    if not isinstance(data, list):
        return []
    return sorted({str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")})


def harness_models(name: str) -> list[str]:
    """The harness's own catalog (subscription providers included)."""
    argv = (binary(name), "models", "ls", "--json") if name == "omp" else (binary(name), "--list-models")
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return []
    out = proc.stdout.strip()
    if name == "omp":
        try:
            payload = json.loads(out)
        except ValueError:
            return []
        return sorted({str(m.get("selector") or m.get("id")) for m in payload.get("models", []) if isinstance(m, dict)})
    return sorted({line.strip() for line in out.splitlines() if line.strip() and " " not in line.strip()})


def verify_argv(name: str, backend: Backend, runtime_key: bool = True) -> list[str]:
    """One-shot prompt run — the harness must answer, proving auth + model work.

    pi only honors a key handed to it on the command line for a custom endpoint
    (observed: models.json's ``apiKey`` and the provider env var are both
    ignored there), so ``runtime_key`` keeps that flag for URL+key backends and
    drops it for subscription providers, where the stored credential is the one
    that must be proven.
    """
    if name == "omp":
        return [binary(name), "--model", selectable_model_id(name, backend), "--print", _VERIFY_PROMPT]
    argv = [binary(name), "--provider", backend.provider, "--model", backend.model]
    if runtime_key:
        argv += ["--api-key", backend.resolve_api_key()]
    return argv + ["--print", _VERIFY_PROMPT]


def login_argv(name: str) -> list[str]:
    """The interactive command that hands the terminal to the harness login."""
    return [binary(name), "login"] if name == "omp" else [binary(name)]


def login_hint(name: str) -> str:
    if name == "omp":
        return "`omp login` — pick the provider (or run `sfactory backend --login` to hand you over)"
    return "run `pi`, type `/login`, pick the provider, and use key `none` for a local llama.cpp endpoint"


def check_auth(name: str, backend: Backend) -> tuple[bool, str]:
    """Ask the harness whether it can serve this provider — no secret is read here.

    pi has `pi auth check`; omp has no equivalent dry-run, so for omp the answer
    comes from the one-shot verify run instead.
    """
    if name == "pi":
        proc = subprocess.run(
            (binary("pi"), "auth", "check", "--provider", backend.provider, "--json"),
            capture_output=True, text=True, check=False,
        )
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            return (False, (proc.stderr or proc.stdout).strip()[-300:] or "pi auth check returned no output")
        ready = bool(payload.get("ready") or payload.get("hasKey") or payload.get("apiKey"))
        return (ready, json.dumps(payload)[:300])
    return (False, f"{name}: no dry-run auth check — verify with a one-shot prompt run")

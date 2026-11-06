"""Coding-agent harnesses (ADR-0002/ADR-0017/ADR-0019): pi, omp and zcode.

pi and omp both resolve a model through a `providers` block in their own agent
directory, with the same field names — pi keeps it as JSON
(``~/.pi/agent/models.json``), omp as YAML (``~/.omp/agent/models.yml``). So
the factory writes one provider block in whichever format the harness reads,
and the project's ``[backend]`` in factory.toml stays the single source of
truth for URL + model + key.

zcode (ADR-0019) is different by design: it serves whatever model its own
settings or ``zcode login`` define — no provider block, no key on the command
line, ``[backend]`` recorded but nominal. The factory proves it with one real
one-shot prompt (``-p``).

omp's agent dir is asked from omp itself (``omp config path``) rather than
guessed: profiles and XDG layouts move it, and a wrong path means the provider
silently never appears in the harness.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Literal

import yaml

from .config import Backend

HarnessName = Literal["pi", "omp", "zcode"]

HARNESSES: tuple[str, ...] = ("pi", "omp", "zcode")

# pi's models.json vs omp's models.yml — same schema, different serializer.
# zcode is deliberately absent: it serves whatever model its own settings (or
# `zcode login`) define, and sfactory never writes into its configuration.
_REGISTRY_FILE = {"pi": ("models.json", "json"), "omp": ("models.yml", "yaml")}

_VERIFY_PROMPT = "Reply with exactly: OK"


def _zcode_cjs() -> Path:
    """Where the ZCode Desktop bundle keeps its single-file CLI."""
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return base / "Programs" / "ZCode" / "resources" / "glm" / "zcode.cjs"


def _zcode_builtin_config() -> Path | None:
    """Newest cached copy of the CDN-served built-in provider config.

    The Desktop app keeps one per app version under
    ``~/.zcode/v2/runtime/provider/<platform>/<version>/endpoint-<id>/``;
    old versions linger after updates, so newest mtime wins.
    """
    root = Path.home() / ".zcode" / "v2" / "runtime" / "provider"
    if not root.is_dir():
        return None
    candidates = list(root.glob("*/*/endpoint-*/zcode-builtin.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _ensure_zcode_provider_env() -> None:
    """zcode's headless CLI resolves its models through two config files that the
    Desktop app injects as env vars — and a plain terminal (the factory's case)
    has neither, so the CLI dies with "无法定位 CLI ZCode Built-in Provider":

    - ``ZCODE_PERSONAL_PROVIDER_CONFIG_FILE`` — the user's own providers
      (observed: ``~/.zcode/v2/provider_config.json``);
    - ``ZCODE_BUILTIN_PROVIDER_CONFIG_FILE`` — the CDN-served provider catalog
      cached per app version under ``~/.zcode/v2/runtime/provider/…``.

    Both default to the app's on-disk locations, never overriding the
    environment.
    """
    if not os.environ.get("ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"):
        personal = Path.home() / ".zcode" / "v2" / "provider_config.json"
        if personal.is_file():
            os.environ["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"] = str(personal)
    if not os.environ.get("ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"):
        builtin = _zcode_builtin_config()
        if builtin:
            os.environ["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"] = str(builtin)


def spawn_argv(name: str) -> list[str]:
    """The argv prefix that actually launches the harness.

    pi/omp are PATH shims (npm .cmd files); zcode may only exist as the Desktop
    bundle's ``zcode.cjs``, which needs a node prefix. Everything that builds
    an argv (verify, login, models, auth, worker/reviewer commands) starts from
    here, so a future official shim on PATH is picked up without changes.
    """
    if name == "zcode":
        _ensure_zcode_provider_env()
        shim = shutil.which("zcode")
        if shim:
            return [shim]
        script = _zcode_cjs()
        if script.is_file():
            return [shutil.which("node") or "node", str(script)]
        return ["zcode"]
    return [binary(name)]


class HarnessError(RuntimeError):
    """The harness can't serve this backend — message carries the remedy."""


def no_window() -> int:
    """subprocess creation flags that stop console-window flashing on Windows.

    The factory's children (node CLIs, their own helpers) spawn console
    processes of their own; a child with no console to inherit gets a visible
    window that flashes and dies — one per spawn. ``CREATE_NO_WINDOW`` gives
    the whole tree one invisible console. Interactive spawns (the `login`
    handover) must not use it: they need the real terminal.
    """
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def installed(name: str) -> bool:
    if name == "zcode":
        return shutil.which(name) is not None or _zcode_cjs().is_file()
    return shutil.which(name) is not None


def binary(name: str) -> str:
    """Absolute path of the harness launcher.

    Windows cannot CreateProcess an extensionless `pi` — npm installs it as
    `pi.CMD`, which only runs when the extension is part of the path handed to
    the API. Spawning the resolved path keeps the .cmd shim (and its own
    node/python launchers) working without going through a shell.
    """
    return shutil.which(name) or name


def has_registry(name: str) -> bool:
    """Whether sfactory may merge a provider block for this harness at all.

    zcode serves whatever model its own settings (or ``zcode login``) define —
    there is no provider registry to merge into, so backend setup and `--show`
    skip it instead of pretending.
    """
    return name in _REGISTRY_FILE


def agent_dir(name: str) -> Path:
    """Where the harness keeps auth + model registry (env override first)."""
    override = {"pi": "PI_CODING_AGENT_DIR", "omp": "OMP_CODING_AGENT_DIR",
                "zcode": "ZCODE_CODING_AGENT_DIR"}.get(name)
    if override and os.environ.get(override):
        return Path(os.environ[override])
    if name == "omp":
        out = subprocess.run(
            ("omp", "config", "path"), capture_output=True, text=True, check=False,
            creationflags=no_window(),
        ).stdout.strip()
        if out and Path(out).is_dir():
            return Path(out)
        return Path.home() / ".omp" / "agent"
    if name == "zcode":
        return Path.home() / ".zcode"  # diagnostics only — sfactory never writes here
    return Path.home() / ".pi" / "agent"


def registry_path(name: str) -> Path:
    if name not in _REGISTRY_FILE:
        raise HarnessError(
            f"{name} keeps no provider registry — its model comes from its own "
            "settings/login, so there is nothing to register or check"
        )
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
    # pi 1.5.0 validates the registry and rejects an empty apiKey outright —
    # a placeholder keeps the file loadable; the factual key still flows via
    # --api-key at runtime (or the agent's own credential, for keyless ids).
    api_key = backend.resolve_api_key() or "none"
    return {
        "baseUrl": backend.base_url,
        "api": backend.api,
        "apiKey": api_key,
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


def mask_argv(argv: list[str]) -> list[str]:
    """argv safe to print or log: the value after ``--api-key`` is redacted.

    The key rides inside the worker/reviewer command line (pi takes it at
    runtime for custom endpoints). Nothing logs full argv today; this exists so
    the first "helpful debugging output" does not start leaking it into
    ``.factory/run`` logs.
    """
    out = list(argv)
    for i, part in enumerate(out):
        if part == "--api-key" and i + 1 < len(out):
            out[i + 1] = "***"
    return out


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
            "`sfactory provider` (the factory will not overwrite a file it cannot parse)."
        ) from None
    return data if isinstance(data, dict) else {}


def _backup(path: Path) -> None:
    bak = path.with_name(path.name + ".bak")
    if not bak.exists():
        shutil.copy2(path, bak)


def register_provider(name: str, backend: Backend, log=print) -> str:
    """Merge the backend's provider block into the harness registry (idempotent).

    Returns `registered` / `already-registered` / `created`. A file we cannot
    parse raises with the fix; nothing is written blind over it. Repairs empty
    apiKeys first — one stale keyless entry makes pi 1.5.0 reject the whole
    file, which would read back as "Unknown provider" for everything.
    """
    repair_empty_keys(name, log)
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


def repair_empty_keys(name: str, log=print) -> int:
    """Fill empty apiKeys in the harness registry with the 'none' placeholder.

    pi 1.5.0 validates models.json and rejects the whole file over a single
    empty apiKey — one stale keyless entry (e.g. a freeflow experiment) takes
    every custom provider down with it. A placeholder is safe: the factual key
    still flows via --api-key at runtime, and keyless extensions ignore the
    registry value. Idempotent; backs up before the first change.
    """
    path = registry_path(name)
    data = registry_load(name)
    providers = (data or {}).get("providers")
    if not isinstance(providers, dict):
        return 0
    changed = 0
    for pid, block in providers.items():
        if isinstance(block, dict) and block.get("apiKey", "none") == "":
            block["apiKey"] = "none"
            changed += 1
    if changed:
        _backup(path)
        if _REGISTRY_FILE[name][1] == "json":
            payload = json.dumps(data, indent=2) + "\n"
        else:
            import yaml

            payload = yaml.safe_dump(data, sort_keys=False)
        path.write_text(payload, encoding="utf-8", newline="\n")
        log(f"  [green]repaired[/] {name} registry: {changed} empty apiKey(s) -> 'none' "
            f"(pi 1.5.0 rejects the file over them) in {path}")
    return changed


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


def _catalog_pi(out: str) -> list[tuple[str, str]]:
    """pi prints a fixed-width table and offers no JSON mode — parse what it says.

    Columns are separated by two or more spaces and neither cell contains one,
    so the first two cells of every row are provider and model. Rows are taken in
    pi's own order and deduplicated: an operator reads this list, so it should
    match what `pi --list-models` showed them.
    """
    rows: list[tuple[str, str]] = []
    for line in out.splitlines():
        cells = re.split(r"\s{2,}", line.strip())
        if len(cells) < 2 or cells[0] == "provider":
            continue
        entry = (cells[0], cells[1])
        if entry not in rows:
            rows.append(entry)
    return rows


def harness_catalog(name: str) -> list[tuple[str, str]]:
    """``(provider, model)`` pairs the agent itself lists, in the order it prints.

    The provider has to travel with the model: `pi --list-models` answers with
    ~950 rows across 14 providers, and offering all of them as one flat list
    means the operator picks an id whose provider they then have to configure
    separately — which is how a freeflow id ends up pointed at a dead endpoint.
    """
    if name == "zcode":
        return []  # no catalog command; zcode serves what its settings define
    argv = (binary(name), "models", "ls", "--json") if name == "omp" else (binary(name), "--list-models")
    proc = subprocess.run(argv, capture_output=True, text=True, check=False, creationflags=no_window())
    if proc.returncode != 0:
        return []
    out = proc.stdout.strip()
    if name != "omp":
        return _catalog_pi(out)
    try:
        payload = json.loads(out)
    except ValueError:
        return []
    models = [m for m in payload.get("models", []) if isinstance(m, dict)]
    return sorted({(str(m.get("provider") or ""), str(m.get("selector") or m.get("id"))) for m in models})


def harness_models(name: str) -> list[str]:
    """Every model id the harness knows, regardless of provider."""
    return [model for _provider, model in harness_catalog(name)]


def harness_providers(name: str) -> list[tuple[str, int]]:
    """Providers with their model counts, busiest first — the answer to "what can I run?".

    An empty list means the harness does not name providers at all; callers then
    skip the provider question rather than inventing a group for the models.
    """
    counts: dict[str, int] = {}
    for provider, _model in harness_catalog(name):
        if provider:
            counts[provider] = counts.get(provider, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))


def verify_argv(name: str, backend: Backend, runtime_key: bool = True) -> list[str]:
    """One-shot prompt run — the harness must answer, proving auth + model work.

    pi only honors a key handed to it on the command line for a custom endpoint
    (observed: models.json's ``apiKey`` and the provider env var are both
    ignored there), so ``runtime_key`` keeps that flag for URL+key backends and
    drops it for subscription providers, where the stored credential is the one
    that must be proven.
    """
    if name == "zcode":
        # plan mode is read-only by construction; --json so the answer is machine-ish.
        return [*spawn_argv(name), "-p", _VERIFY_PROMPT, "--json", "--mode", "plan"]
    if name == "omp":
        return [binary(name), "--model", selectable_model_id(name, backend), "--print", _VERIFY_PROMPT]
    argv = [binary(name), "--provider", backend.provider, "--model", backend.model]
    key = backend.resolve_api_key()
    if runtime_key and key:
        argv += ["--api-key", key]
    return argv + ["--print", _VERIFY_PROMPT]


def login_argv(name: str) -> list[str]:
    """The interactive command that hands the terminal to the harness login."""
    if name == "zcode":
        return [*spawn_argv(name), "login"]
    return [binary(name), "login"] if name == "omp" else [binary(name)]


def login_hint(name: str) -> str:
    if name == "zcode":
        return "`zcode login` — pick z.ai (Start plan) or BigModel; the CLI then serves its own model"
    if name == "omp":
        return "`omp login` — pick the provider (or run `sfactory provider --login` to hand you over)"
    return "run `pi`, type `/login`, pick the provider, and use key `none` for a local llama.cpp endpoint"


def remedy(output: str, backend: Backend, harness: str = "pi") -> str:
    """Map the harness's own error words to the next command — no log file needed.

    Shared by `sfactory provider` (which proves a choice) and dispatch (which
    discovers the failure mid-wave): the fix for a dead agent must reach the
    operator wherever the agent was spawned from.
    """
    lowered = output.lower()
    if "no api key" in lowered or ("api key" in lowered and "not found" in lowered):
        return (f"the agent has no credential for provider {backend.provider!r}: {login_hint(harness)}"
                " — or pass the key at runtime, which `sfactory provider` and dispatch both do now")
    if "connect" in lowered or "econnrefused" in lowered:
        if harness == "zcode":
            return (f"{harness} cannot reach the model its own settings point at — set the "
                    "model/provider in the ZCode app settings, or `zcode login` to serve a "
                    "subscription model")
        return (f"nothing is serving {backend.base_url} — start the endpoint (llama-router / llama-server) "
                "or point the project elsewhere with `sfactory provider --source openai-endpoint`")
    if "model" in lowered and ("not found" in lowered or "not offered" in lowered):
        return (f"{backend.model!r} is not offered by this endpoint — `sfactory provider` lists what it "
                "does serve (note: a HF repo id is not a served model name)")
    if "timed out" in lowered:
        return (f"{backend.model!r} did not answer inside the budget — a local model spends most of it "
                "reading the agent's system prompt, so raise [harness] worker_timeout_min or pick a "
                "smaller/faster model with `sfactory model <id>`")
    return ""


def check_auth(name: str, backend: Backend) -> tuple[bool, str]:
    """Ask the harness whether it can serve this provider — no secret is read here.

    pi has `pi auth check`; omp has no equivalent dry-run, so for omp the answer
    comes from the one-shot verify run instead.
    """
    if name == "zcode":
        # "provider_not_found" reuses backend_setup's unmanaged path: no login
        # handover is forced — the real prompt is the only honest proof here.
        return (False, f"provider_not_found: {name} serves its own configured model — "
                       "proving with a real prompt instead")
    if name == "pi":
        proc = subprocess.run(
            (binary("pi"), "auth", "check", "--provider", backend.provider, "--json"),
            capture_output=True, text=True, check=False, creationflags=no_window(),
        )
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            return (False, (proc.stderr or proc.stdout).strip()[-300:] or "pi auth check returned no output")
        ready = bool(payload.get("ready") or payload.get("hasKey") or payload.get("apiKey"))
        return (ready, json.dumps(payload)[:300])
    return (False, f"{name}: no dry-run auth check — verify with a one-shot prompt run")

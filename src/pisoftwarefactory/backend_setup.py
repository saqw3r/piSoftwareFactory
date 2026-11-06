"""``sfactory backend`` — choose the brain, then prove it answers (ADR-0017).

One command decides two things: which coding agent does the work (``pi`` or
``omp``) and where its model comes from — the owner's local llama.cpp, an
OpenAI-compatible endpoint behind a key, or a subscription provider the agent
logs into itself. Every branch ends with a real one-shot prompt through the
chosen agent, so "configured" means "answered", not "the file says so".

Nothing here asks the user to edit a file, and no secret is stored in
``factory.toml``: the key lives in the environment (``api_key_env``) and is
handed to the agent registry at write time. Interactive questions only appear
when a value was not passed as a flag, so the same command runs unattended on a
foreign machine.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import typer

from . import harnesses
from .config import Backend, FactoryConfig, load_config, save_config
from .integrations.pi_agent import stream_agent

DEFAULT_LOCAL_URL = "http://127.0.0.1:8080/v1"
DEFAULT_OPENAI_URL = "https://api.openai.com/v1"

SOURCES = ("llama-local", "openai-endpoint", "harness-login")

_SOURCE_LABELS = {
    "llama-local": "1) local llama.cpp (pi/omp on your own GPU box, mocked key)",
    "openai-endpoint": "2) OpenAI-compatible endpoint + key from the environment",
    "harness-login": "3) subscription provider — the agent's own login (OAuth/API key)",
}

# Presets: well-known OpenAI-compatible endpoints, so the URL + key-env pair
# stops living only in README examples. A preset name is accepted wherever a
# source is (`--source qoder`, or typed at the menu) and pre-answers the
# questions the openai-endpoint flow would ask; explicit flags still win.
PRESETS: dict[str, dict[str, str]] = {
    "llamacpp": {
        "base_url": "http://127.0.0.1:8080/v1",
        "api_key_env": "",
        "provider": "llamacpp",
        "label": "local llama.cpp / llama-router (:8080, mocked key)",
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key_env": "",
        "provider": "ollama",
        "label": "local Ollama (:11434, mocked key)",
    },
    "qoder": {
        "base_url": "http://127.0.0.1:8790/v1",
        "api_key_env": "QODER_API_KEY",
        "provider": "qoder",
        "label": "local qoder gateway (your Qoder subscription behind an OpenAI-compatible proxy)",
    },
    "dashscope": {
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "provider": "dashscope",
        "label": "Alibaba DashScope/Model Studio (Qwen behind a key, no Qoder needed)",
    },
}

# pi's catalog name for the local server, spelled the way pi writes it.
PROVIDER_ALIASES = {"llama.cpp": "llamacpp", "llama": "llamacpp"}

# Known homes of local OpenAI-compatible servers, probed before the source
# question so "what can I use?" is answered by pressing a number instead of
# knowing a URL. :8080 is llama.cpp/llama-router — the model list a running
# server serves right now is the only list worth offering.
LOCAL_PORTS = (8080, 8790, 1234, 11434, 8000)  # llama.cpp, qoder gateway, LM Studio, Ollama, vLLM

_VERIFY_TIMEOUT_SEC = 180


def probe_local_endpoints(ports: tuple[int, ...] = LOCAL_PORTS) -> list[tuple[str, list[str]]]:
    """GET /models on each candidate loopback port; returns the ones that answer.

    Best-effort and never raises — a dead port is the normal case. Probing runs
    in parallel and is capped by the per-port timeout, so an all-dead machine
    costs ~1.5 s, not one timeout per port.
    """
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    def _probe(port: int) -> tuple[str, list[str]] | None:
        url = f"http://127.0.0.1:{port}/v1"
        try:
            with httpx.Client(timeout=1.5) as client:
                resp = client.get(url + "/models", headers={"Authorization": "Bearer none"})
                resp.raise_for_status()
                body = resp.json()
        except Exception:  # noqa: BLE001 — a dead port is the normal case
            return None
        data = body.get("data", body) if isinstance(body, dict) else body
        if not isinstance(data, list):
            return None
        ids = sorted({str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id")})
        return (url, ids) if ids else None

    with ThreadPoolExecutor(max_workers=len(ports)) as pool:
        return [hit for hit in pool.map(_probe, ports) if hit]


def offer_live_endpoint(live: list[tuple[str, list[str]]], ask: Callable[..., str], log) -> str:
    """Menu of live local endpoints; returns the picked base_url, or '' on Enter."""
    log(f"found {len(live)} live OpenAI-compatible endpoint(s) on this machine:")
    for i, (url, models) in enumerate(live, 1):
        shown = ", ".join(models[:4]) + ("…" if len(models) > 4 else "")
        log(f"  {i}. {url} — {shown}")
    answer = ask("use one? (number, Enter to skip)", default="").strip()
    if answer.isdigit() and 1 <= int(answer) <= len(live):
        return live[int(answer) - 1][0]
    return ""


def _run_stream(
    argv: list[str],
    log,
    timeout: int = _VERIFY_TIMEOUT_SEC,
    model: str = "the agent",
    agent_credential: bool = False,
    provider: str = "",
    quiet_note: str = "",
) -> tuple[int, str]:
    """Run *argv*, echoing each line as it lands; returns (rc, captured output).

    Streaming is the point: a local model loading for a minute looks identical
    to a hang when nothing is printed, and the owner should see which it is.
    The quiet-progress line names *model*, not argv[0] — the executable never
    varies, the model does, and "which one is thinking" is the question.
    """
    rc, out, errtext = stream_agent(argv, None, timeout, model, log=log, quiet_s=30,
                                    echo_text=True, agent_credential=agent_credential,
                                    provider=provider, quiet_note=quiet_note)
    return (rc, out + errtext)


def pick_source(ask: Callable[..., str], log) -> str:
    log("where does the model come from?")
    for key in SOURCES:
        log(f"  {_SOURCE_LABELS[key]}")
    if PRESETS:
        log("  presets: " + "; ".join(f"{name} — {p['label']}" for name, p in PRESETS.items()))
    answer = ask("choice", default="1").strip().lower()
    by_number = {"1": "llama-local", "2": "openai-endpoint", "3": "harness-login"}
    if answer in by_number:
        return by_number[answer]
    if answer in SOURCES or answer in PRESETS:
        return answer
    raise SystemExit(f"unknown source {answer!r} — pick 1, 2 or 3, a preset ({', '.join(PRESETS)}) "
                     "or pass --source")


def pick_model(candidates: list[str], current: str, ask: Callable[..., str], log) -> str:
    """Offer what the backend actually serves; free text stays allowed."""
    if not candidates:
        log("  no model list from the endpoint — type the id you know it answers to")
        return ask("model", default=current)
    shown = candidates[:15]
    default_index = shown.index(current) + 1 if current in shown else 1
    for i, model in enumerate(shown, 1):
        log(f"  {i}. {model}")
    if len(candidates) > len(shown):
        # The list is a menu, not an inventory: a hidden id is still typeable,
        # but only the operator who knows it exists. Say what is not shown.
        log(f"  … {len(candidates) - len(shown)} more — type the id, or pass "
            "`sfactory model <id>` to search the whole list")
    answer = ask("model", default=str(default_index)).strip()
    if answer.isdigit() and 1 <= int(answer) <= len(shown):
        return shown[int(answer) - 1]
    return answer


def _preset_for(provider: str) -> dict[str, str]:
    """The preset that owns this provider name, or {} when nothing does."""
    return next((p for p in PRESETS.values() if p["provider"] == provider), {})


def provider_choices(harness: str) -> list[tuple[str, int]]:
    """Providers to offer: the harness's own, plus every preset, aliased onto one name.

    pi's table calls the local server ``llama.cpp`` and the factory's working
    block ``llamacpp``; they are the same machine, so they must appear once —
    under the spelling that answers. pi's built-in ``llama.cpp`` provider takes
    no ``--api-key`` and waits silently when nothing listens, where the factory's
    fails fast.
    """
    merged: dict[str, int] = {}
    for provider, count in harnesses.harness_providers(harness):
        key = PROVIDER_ALIASES.get(provider, provider)
        merged[key] = merged.get(key, 0) + count
    for preset in PRESETS.values():
        merged.setdefault(preset["provider"], 0)
    return sorted(merged.items(), key=lambda kv: (-kv[1], kv[0].lower()))


def pick_provider(choices: list[tuple[str, int]], current: str, ask: Callable[..., str], log) -> str:
    """Provider first, then its models — one 950-row list answers neither question."""
    default_index = next((i for i, (p, _n) in enumerate(choices, 1) if p == current), 1)
    log("which provider serves the model?")
    for i, (provider, count) in enumerate(choices, 1):
        label = _preset_for(provider).get("label") or f"{count} model(s) in {provider}'s catalog"
        log(f"  {i}. {provider} — {label}")
    answer = ask("provider", default=str(default_index)).strip()
    if answer.isdigit() and 1 <= int(answer) <= len(choices):
        return choices[int(answer) - 1][0]
    if not answer:
        return current
    return PROVIDER_ALIASES.get(answer, answer)


def catalog_ids_for(harness: str, provider: str) -> list[str]:
    """Catalog rows belonging to this provider, under any spelling it prints.

    pi calls the local server ``llama.cpp``; factory.toml stores the alias that
    works (``llamacpp``). Matching only one of the two makes the wizard list zero
    models for a provider it just offered.
    """
    return [model for row_provider, model in harnesses.harness_catalog(harness)
            if row_provider == provider or PROVIDER_ALIASES.get(row_provider, row_provider) == provider]


def models_for_provider(harness: str, backend: Backend, log) -> list[str]:
    """What this provider answers with *now*, then what the agent merely claims.

    A llama.cpp server lists the GGUFs currently loaded; the agent's registry
    lists ids that may not exist on this box. Live ids first, and the difference
    said out loud, so a pick is never silently an unproven one.
    """
    listed = catalog_ids_for(harness, backend.provider)
    preset = _preset_for(backend.provider)
    if not preset:
        return listed
    probe = backend.model_copy(update={"base_url": preset["base_url"]})
    live = harnesses.endpoint_models(probe)
    if live:
        extra = [m for m in listed if m not in live]
        log(f"  [green]{len(live)} model(s) loaded on {preset['base_url']} right now[/]"
            + (f" — {len(extra)} more named in {harness}'s registry, not served by this server"
               if extra else ""))
        return live + extra
    log(f"  [yellow]{preset['base_url']}/models did not answer[/] — start the server, or pick "
        f"another provider. {len(listed)} id(s) below come from {harness}'s registry and are "
        "unproven until something answers")
    return listed


def _local_backend(base_url: str | None, model: str, provider: str = "llamacpp") -> Backend:
    return Backend(provider=provider, base_url=base_url or DEFAULT_LOCAL_URL, model=model, api_key_env="")


def _endpoint_backend(base_url: str, model: str, api_key_env: str, provider: str) -> Backend:
    return Backend(provider=provider, base_url=base_url, model=model,
                   api="openai-completions", api_key_env=api_key_env)


def _update_company_seed(root: Path, harness: str, backend: Backend, log) -> None:
    """Keep the Paperclip seed's harness block in sync with the choice made here."""
    path = root / "paperclip.company.json"
    if not path.is_file():
        return
    try:
        seed = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        log(f"  [yellow]skip[/] {path}: not valid JSON — fix it, then re-run")
        return
    harness_block = seed.get("company", {}).get("harness")
    if not isinstance(harness_block, dict):
        return
    command = (f"{harness} -p --attach brief.md" if harness == "zcode"
               else f"{harness} --mode json @brief.md")
    harness_block.update({
        "name": harness,
        "command": command,
        "provider": backend.provider,
        "model": backend.model,
        "endpoint": backend.base_url,
    })
    path.write_text(json.dumps(seed, indent=2) + "\n", encoding="utf-8", newline="\n")
    log(f"  [green]seed updated[/] {path}")


def _handover_login(harness: str, log) -> None:
    """Give the terminal to the agent's own login, then take it back."""
    if not harnesses.installed(harness):
        log(f"  [red]{harness} is not installed[/] — install it first, then retry")
        return
    log(f"  handing the terminal over: {harnesses.login_hint(harness)}")
    subprocess.call(harnesses.login_argv(harness))
    log("  back with sfactory")


def _checklist(stages: list[tuple[str, bool]]) -> str:
    """The flow as ✓/✗ — a failure points at its stage, not at a red wall."""
    return "  " + "  ".join(
        f"[green]✓[/] {name}" if ok else f"[red]✗[/] {name}" for name, ok in stages
    )


def _show(config: FactoryConfig, root: Path, harness: str, log) -> int:
    """Read-only view of the effective choice — and of drift between the two
    stores that must agree: factory.toml and the harness registry.

    The stored key is deliberately not compared: it legitimately changes with
    the environment. The endpoint, API flavor and model must not.
    """
    import os

    b = config.backend
    if b.agent_credential:
        key = "the agent's own login"
    elif b.api_key_env:
        key = f"${b.api_key_env} ({'set' if os.environ.get(b.api_key_env) else 'not set'})"
    else:
        key = "mocked (none)"
    log(f"brain: {harness} · {b.provider} · {b.base_url} · {b.model} · key: {key}")
    preset = next((n for n, p in PRESETS.items() if p["base_url"] == b.base_url), "")
    if preset:
        log(f"  matches preset {preset!r}")

    if not harnesses.has_registry(harness):
        log(f"  {harness} serves its own model (its settings or `{harness} login`) — "
            "no provider registry to check")
        return 0

    path = harnesses.registry_path(harness)
    data = harnesses.registry_load(harness)
    stored = ((data or {}).get("providers") or {}).get(b.provider)
    if stored is None:
        log(f"  [yellow]registry[/] {path}: no provider {b.provider!r} — `sfactory backend` writes it")
        return 1
    block = harnesses.provider_block(b)
    drift = [f"{k}: registry {stored.get(k)!r} vs factory.toml {block.get(k)!r}"
             for k in ("baseUrl", "api") if stored.get(k) != block.get(k)]
    stored_models = [m.get("id") for m in (stored.get("models") or []) if isinstance(m, dict)]
    if stored_models and b.model not in stored_models:
        drift.append(f"model: registry {stored_models} vs factory.toml {b.model!r}")
    if drift:
        log(f"  [red]registry drift[/] {path}")
        for d in drift:
            log(f"    {d}")
        log("  fix: `sfactory backend` re-registers the provider (the file is backed up first)")
        return 1
    log(f"  [green]registry in sync[/] {path}")
    return 0


def run_backend(
    root: Path,
    harness: str = "",
    source: str = "",
    base_url: str = "",
    model: str = "",
    api_key_env: str = "",
    provider: str = "",
    login: bool = False,
    show: bool = False,
    verify: bool | None = None,
    verify_timeout_min: int = 0,
    yes: bool = False,
    log=print,
    ask: Callable[..., str] = typer.prompt,
) -> int:
    """Apply the chosen harness+backend to *root* and prove it answers.

    Questions appear only for what no flag supplied — a one-liner on a foreign
    machine passes every value and runs unattended. Returns 0 only when the
    agent actually replied; every other outcome prints the cause and the next
    command to run.
    """
    root = root.resolve()
    try:
        config = load_config(root)
    except FileNotFoundError:
        log(f"[yellow]no factory.toml in {root}[/] — using local defaults; run `sfactory init` to scaffold the rest")
        config = FactoryConfig()

    # A local GGUF spends most of a verify turn reading pi's system prompt, so
    # the budget is the project's own setting, not a constant tuned for APIs.
    verify_budget = (verify_timeout_min or config.harness.verify_timeout_min) * 60

    def _ask(text: str, default: str) -> str:
        if yes:
            log(f"  {text}: {default} (--yes)")
            return default
        return (ask(text, default=default) or "").strip() or default

    harness = harness or config.harness.name
    if show:
        return _show(config, root, harness, log)
    if harness not in harnesses.HARNESSES:
        raise SystemExit(f"unknown harness {harness!r} — use one of {list(harnesses.HARNESSES)}")
    if not harnesses.installed(harness):
        log(f"  [red]{harness} not found on PATH[/] — install it, or pick the other harness with --harness")
        return 2

    # zcode has no provider registry to write and takes no model flags (ADR-0019):
    # every source collapses to the agent-credential path — the CLI serves whatever
    # its own settings or `zcode login` define, and the proof is a real one-shot prompt.
    if harness == "zcode" and source != "harness-login":
        log("  zcode serves the model its own settings (or `zcode login`) define — "
            "configuring the agent-credential path; no provider registry is written")
        source = "harness-login"

    # Interactive only, and only when --source was not passed: probe the known
    # loopback ports first so the operator picks from what actually answers.
    picked_url = ""
    if not source and not yes:
        live = probe_local_endpoints()
        if live:
            picked_url = offer_live_endpoint(live, _ask, log)
    source = source or ("openai-endpoint" if picked_url else SOURCES[0] if yes else pick_source(_ask, log))
    preset_name = source if source in PRESETS else ""
    if picked_url and not preset_name:
        preset_name = next((n for n, p in PRESETS.items() if p["base_url"] == picked_url), "")
        if preset_name:
            log(f"  endpoint matches preset {preset_name!r} — key env {PRESETS[preset_name]['api_key_env']}")
    preset = PRESETS.get(preset_name, {})
    if preset_name:
        source = "openai-endpoint"
    model_given = bool(model)
    model = model or config.backend.model
    log(f"harness: {harness}   source: {source}" + (f" (preset: {preset_name})" if preset_name else ""))

    if source == "harness-login":
        # Provider first, then only that provider's models. pi answers
        # `--list-models` with ~950 ids over 14 providers; a flat list asks the
        # operator to know the provider from the id and to keep the two in step
        # by hand — which is how a freeflow model id ended up on a dead endpoint.
        has_catalog = harness != "zcode"
        chosen = PROVIDER_ALIASES.get(provider or config.backend.provider,
                                      provider or config.backend.provider)
        choices = provider_choices(harness) if has_catalog else []
        if has_catalog and not provider and not yes and len(choices) > 1:
            chosen = pick_provider(choices, config.backend.provider, _ask, log)
        # zcode has no provider registry and takes no model flags (ADR-0019): the
        # CLI serves whatever its own settings or `zcode login` define, so no
        # preset may claim it — that would hand it a mocked key it never uses.
        endpoint_preset = _preset_for(chosen) if has_catalog else {}
        backend = Backend(provider=chosen,
                          base_url=endpoint_preset.get("base_url", config.backend.base_url),
                          model=model,
                          api_key_env=api_key_env or endpoint_preset.get("api_key_env",
                                                                         config.backend.api_key_env),
                          # A provider the agent logs into needs no key on the command
                          # line; a local endpoint needs the mocked one or it is refused.
                          agent_credential=not endpoint_preset)
        if not (model_given or yes) and harness != "zcode":
            backend = backend.model_copy(update={
                "model": pick_model(models_for_provider(harness, backend, log), backend.model, _ask, log)})
        # zcode: no model question — there is no catalog to offer and no flag to
        # send it to; the id recorded in factory.toml is bookkeeping only.
        if endpoint_preset:
            # A preset provider is an endpoint, and an endpoint reaches the agent
            # only through its registry block: pi's built-in ``llama.cpp`` takes no
            # URL and waits silently when nothing answers.
            harnesses.register_provider(harness, backend, log)
        ready, detail = harnesses.check_auth(harness, backend)
        unmanaged = "provider_not_found" in detail
        if endpoint_preset and not login:
            log(f"  {chosen} is served at {backend.base_url} — nothing to log into; "
                "the endpoint itself is the credential")
        elif ready and not login:
            log(f"  {harness} already has a credential for this provider — no login needed")
        elif unmanaged and not login:
            why = ("the agent's own settings serve it" if harness == "zcode"
                   else "the agent's own extension serves it (freeflow and other keyless providers)")
            log(f"  {harness} does not keep credentials for {backend.provider!r} — {why}. No login to do; "
                "proving it with one real prompt instead")
        else:
            if not ready and not unmanaged:
                log(f"  {harness} reports: {detail}")
            _handover_login(harness, log)
        outcome = _verify(harness, backend, log, runtime_key=not backend.agent_credential,
                          timeout_s=verify_budget)
        _finish(config, harness, backend, root, outcome, log, registry=bool(endpoint_preset))
        return outcome

    if source == "openai-endpoint":
        if preset:
            base_url = base_url or preset["base_url"]
            api_key_env = api_key_env or preset["api_key_env"]
        else:
            base_url = base_url or (DEFAULT_OPENAI_URL if yes else _ask("OpenAI-compatible base URL", DEFAULT_OPENAI_URL))
            api_key_env = api_key_env or (config.backend.api_key_env or "OPENAI_API_KEY" if yes
                                          else _ask("env var holding the key", config.backend.api_key_env or "OPENAI_API_KEY"))
        provider = provider or (preset["provider"] if preset
                                else "openai" if "api.openai.com" in base_url else "openai-compatible")
        candidate = _endpoint_backend(base_url, model, api_key_env, provider)
        if candidate.resolve_api_key() == "none":
            from .config import export_hint
            log(f"  [yellow]${api_key_env} is not set in this shell[/] — {export_hint(api_key_env)}; "
                "until then the listing and the check will fail")
    else:
        base_url = base_url or (DEFAULT_LOCAL_URL if yes else _ask("local endpoint base URL", DEFAULT_LOCAL_URL))
        provider = provider or "llamacpp"
        candidate = _local_backend(base_url, model, provider)

    stages: list[tuple[str, bool]] = []
    listed = harnesses.endpoint_models(candidate)
    stages.append(("endpoint /models", bool(listed)))
    if listed:
        log(f"  [green]✓[/] endpoint /models — {len(listed)} model(s) listed")
    else:
        log(f"  [red]✗[/] {candidate.base_url}/models did not answer — falling back to "
            f"{harness}'s own catalog")
        listed = harnesses.harness_models(harness)
    if model_given or yes:
        log(f"  model: {candidate.model}")
    else:
        candidate = candidate.model_copy(update={"model": pick_model(listed, candidate.model, _ask, log)})

    harnesses.register_provider(harness, candidate, log)
    stages.append(("registry", True))
    if verify is False:
        outcome = 0
    elif verify is None and config.harness.name == harness and config.backend == candidate:
        # Same harness, same backend, same registry block — the last verify already
        # proved this exact setup. Re-proving it costs minutes on a local GGUF.
        log("  [dim]unchanged — already proven (--verify re-checks anyway)[/]")
        outcome = 0
    else:
        outcome = _verify(harness, candidate, log, timeout_s=verify_budget)
        stages.append(("verify prompt", outcome == 0))
    if outcome != 0:
        log(_checklist(stages))
    _finish(config, harness, candidate, root, outcome, log, registry=True)
    return outcome


def run_provider(
    root: Path,
    harness: str = "",
    yes: bool = False,
    log=print,
    ask: Callable[..., str] = typer.prompt,
) -> int:
    """Switch the worker brain (pi | omp | zcode), keeping the backend record.

    Thin and honest: the provider registry is re-registered for the new harness
    (pi/omp), the one-shot prompt proves it, and only then does factory.toml
    move. zcode needs no registry — the proof is the prompt, as always.
    """
    root = root.resolve()
    try:
        config = load_config(root)
    except FileNotFoundError:
        log(f"[yellow]no factory.toml in {root}[/] — run `sfactory init` first")
        return 2

    current = config.harness.name
    if not harness:
        if yes:
            log(f"provider: {current} (current — pass pi|omp|zcode to switch)")
            return 0
        log("which worker brain?")
        for i, name in enumerate(harnesses.HARNESSES, 1):
            log(f"  {i}. {name}" + (" (current)" if name == current else ""))
        answer = ask("provider", default=current).strip().lower()
        if answer.isdigit() and 1 <= int(answer) <= len(harnesses.HARNESSES):
            harness = harnesses.HARNESSES[int(answer) - 1]
        elif answer in harnesses.HARNESSES:
            harness = answer
        else:
            log(f"[red]unknown provider {answer!r}[/] — use one of {list(harnesses.HARNESSES)}")
            return 2
    if harness not in harnesses.HARNESSES:
        log(f"[red]unknown provider {harness!r}[/] — use one of {list(harnesses.HARNESSES)}")
        return 2
    if harness == current:
        log(f"provider is already {harness} — nothing to do")
        return 0
    if not harnesses.installed(harness):
        log(f"  [red]{harness} not found on PATH[/] — install it first")
        return 2

    backend = config.backend
    if harness != "zcode" and backend.agent_credential:
        # agent_credential is a zcode-era record: pi/omp take the key at runtime
        # again (pi ignores registry keys for custom endpoints by design).
        backend = backend.model_copy(update={"agent_credential": False})
        log("  [dim]agent_credential cleared — pi/omp take the key at runtime again[/]")
    if harnesses.has_registry(harness):
        harnesses.register_provider(harness, backend, log)
    else:
        log(f"  {harness} serves the model its own settings (or `{harness} login`) define — "
            "no provider registry to write")
    outcome = _verify(harness, backend, log, timeout_s=config.harness.verify_timeout_min * 60)
    if outcome != 0:
        log("  factory.toml left unchanged (the check failed)")
        return outcome
    config.harness.name = harness  # type: ignore[assignment]
    config.backend = backend
    save_config(config, root)
    log("  [green]saved[/] factory.toml")
    _update_company_seed(root, harness, backend, log)
    from .integrations.pi_agent import brain_label
    log(f"  [dim]brain: {brain_label(config)}[/]")
    return 0


def run_model(
    root: Path,
    model: str = "",
    yes: bool = False,
    log=print,
    ask: Callable[..., str] = typer.prompt,
) -> int:
    """Choose the model the provider serves — live catalog first, honest for zcode."""
    root = root.resolve()
    try:
        config = load_config(root)
    except FileNotFoundError:
        log(f"[yellow]no factory.toml in {root}[/] — run `sfactory init` first")
        return 2
    harness = config.harness.name
    if harness == "zcode":
        log("zcode serves the model its own settings (or `zcode login`) define — the factory "
            "cannot choose it. Set the default model in the ZCode app's model picker; requests "
            "then follow it on the next run.")
        return 1
    if not harnesses.installed(harness):
        log(f"  [red]{harness} not found on PATH[/] — install it first")
        return 2

    # Scoped to the provider already configured: the ids it can answer with, not
    # every id the agent has ever heard of.
    listed = (harnesses.endpoint_models(config.backend)
              or catalog_ids_for(harness, config.backend.provider)
              or harnesses.harness_models(harness))
    log(f"  provider {config.backend.provider!r}: {len(listed)} model(s) to choose from"
        + ("" if listed else " — nothing answered, so type the id you know it serves"))
    model_given = bool(model)
    if not model_given and not yes:
        model = pick_model(listed, config.backend.model, ask, log)
    elif not model_given:
        model = listed[0] if listed else config.backend.model
    if not model:
        log("[red]no model list from the endpoint and no model given[/] — pass one: sfactory model <id>")
        return 2
    if model_given and listed and model not in listed:
        log(f"  [yellow]{model!r} is not in the endpoint's catalog[/] — trying anyway "
            f"(catalog had: {', '.join(listed[:5])}…)")

    backend = config.backend
    if backend.agent_credential:  # registry harness: keys flow at runtime again
        backend = backend.model_copy(update={"agent_credential": False})
    candidate = backend.model_copy(update={"model": model})
    harnesses.register_provider(harness, candidate, log)
    outcome = _verify(harness, candidate, log, timeout_s=config.harness.verify_timeout_min * 60)
    if outcome != 0:
        log("  factory.toml left unchanged (the check failed)")
        return outcome
    config.backend = candidate
    save_config(config, root)
    log("  [green]saved[/] factory.toml")
    _update_company_seed(root, harness, candidate, log)
    from .integrations.pi_agent import brain_label
    log(f"  [dim]brain: {brain_label(config)}[/]")
    return 0


def _verify(harness: str, backend: Backend, log, runtime_key: bool = True, timeout_s: int = _VERIFY_TIMEOUT_SEC) -> int:
    """One real prompt through the agent — the only proof that counts."""
    argv = harnesses.verify_argv(harness, backend, runtime_key=runtime_key)
    log(f"  checking: {' '.join(harnesses.mask_argv(argv))} …")
    if harness == "zcode":
        slow = (f"budget {timeout_s // 60} min on zcode's configured model — a failure here "
                "means the endpoint in ZCode's settings is unreachable or the CLI is not "
                "logged in; a refused connection fails fast")
        quiet_note = ("zcode's configured model is quiet — the CLI retries a refused or "
                      "slow endpoint with backoff until the budget")
    elif not backend.agent_credential:
        slow = (f"budget {timeout_s // 60} min — a local GGUF spends most of it reading the "
                "agent's system prompt; raise [harness] verify_timeout_min if this box is slower")
        quiet_note = ""
    else:
        slow = (f"budget {timeout_s // 60} min on {backend.provider} — the answer should land in "
                "seconds; a timeout here means the free tier is throttling or the id is dead")
        quiet_note = ""
    log(f"  [dim]{slow}[/]")
    rc, output = _run_stream(argv, log, timeout=timeout_s, model=backend.model,
                             agent_credential=backend.agent_credential, provider=backend.provider,
                             quiet_note=quiet_note)
    if rc == 124:
        # Naming base_url for a provider the agent serves itself points the operator
        # at an endpoint that was never called.
        where = f"on provider {backend.provider}" if backend.agent_credential else f"at {backend.base_url}"
        log(f"  [red]no answer within {timeout_s}s[/] — {backend.model!r} {where} "
            "is slower than the budget, not necessarily broken")
        hint = harnesses.remedy("timed out", backend, harness)
        if hint:
            log(f"  fix: {hint}")
        return 3
    if rc != 0:
        tail = "\n".join(output.splitlines()[-8:]) or "(no output)"
        log(f"  [red]{harness} failed[/] (exit {rc}):\n{tail}")
        hint = harnesses.remedy(output, backend, harness)
        if hint:
            log(f"  fix: {hint}")
        return 4
    log("  [green]the harness answered[/] — this backend works")
    return 0


def _reproducible(harness: str, backend: Backend) -> str:
    """The exact command that reproduces this choice on another machine.

    ADR-0017 promises scriptability; handing the operator the proven one-liner
    makes the promise self-documenting instead of something to reconstruct.
    Presets are replayed as their openai-endpoint expansion — equivalent, and
    honest about the URL they stand for.
    """
    if backend.agent_credential:
        parts = ["sfactory backend", "--harness", harness, "--source", "harness-login",
                 "--provider", backend.provider, "--model", backend.model]
    elif backend.api_key_env:
        parts = ["sfactory backend", "--harness", harness, "--source", "openai-endpoint",
                 "--base-url", backend.base_url, "--api-key-env", backend.api_key_env,
                 "--model", backend.model]
        if backend.provider not in ("openai", "openai-compatible"):
            parts.append(f"--provider {backend.provider}")
    else:
        parts = ["sfactory backend", "--harness", harness, "--source", "llama-local",
                 "--base-url", backend.base_url, "--model", backend.model]
    return " ".join(parts) + " -y"


def _finish(
    config: FactoryConfig, harness: str, backend: Backend, root: Path,
    outcome: int, log, registry: bool,
) -> None:
    """Persist the choice only when it works — a broken default is still better than a broken file."""
    if outcome != 0:
        log("  factory.toml left unchanged (the check failed)")
        return
    config.harness.name = harness  # type: ignore[assignment]
    config.backend = backend
    path = save_config(config, root)
    log(f"  [green]saved[/] {path}")
    _update_company_seed(root, harness, backend, log)
    log(f"  replay elsewhere: [cyan]{_reproducible(harness, backend)}[/]")
    log("")
    log(f"next: `sfactory paperclip --provision --root {root}` to put this brain to work"
        if registry else "next: `sfactory paperclip --provision` to put this brain to work")

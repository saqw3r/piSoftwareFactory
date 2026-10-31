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

_VERIFY_TIMEOUT_SEC = 180


def _run_stream(
    argv: list[str],
    log,
    timeout: int = _VERIFY_TIMEOUT_SEC,
    model: str = "the agent",
) -> tuple[int, str]:
    """Run *argv*, echoing each line as it lands; returns (rc, captured output).

    Streaming is the point: a local model loading for a minute looks identical
    to a hang when nothing is printed, and the owner should see which it is.
    The quiet-progress line names *model*, not argv[0] — the executable never
    varies, the model does, and "which one is thinking" is the question.
    """
    rc, out, errtext = stream_agent(argv, None, timeout, model,
                                    log=log, quiet_s=30, echo_text=True)
    return (rc, out + errtext)


def pick_source(ask: Callable[..., str], log) -> str:
    log("where does the model come from?")
    for key in SOURCES:
        log(f"  {_SOURCE_LABELS[key]}")
    answer = ask("choice", default="1").strip().lower()
    by_number = {"1": "llama-local", "2": "openai-endpoint", "3": "harness-login"}
    if answer in by_number:
        return by_number[answer]
    if answer in SOURCES:
        return answer
    raise SystemExit(f"unknown source {answer!r} — pick 1, 2 or 3 (or pass --source)")


def pick_model(candidates: list[str], current: str, ask: Callable[..., str], log) -> str:
    """Offer what the backend actually serves; free text stays allowed."""
    if not candidates:
        log("  no model list from the endpoint — type the id you know it answers to")
        return ask("model", default=current)
    shown = candidates[:15]
    default_index = shown.index(current) + 1 if current in shown else 1
    for i, model in enumerate(shown, 1):
        log(f"  {i}. {model}")
    answer = ask("model", default=str(default_index)).strip()
    if answer.isdigit() and 1 <= int(answer) <= len(shown):
        return shown[int(answer) - 1]
    return answer


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
    harness_block.update({
        "name": harness,
        "command": f"{harness} --mode json @brief.md",
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


def run_backend(
    root: Path,
    harness: str = "",
    source: str = "",
    base_url: str = "",
    model: str = "",
    api_key_env: str = "",
    provider: str = "",
    login: bool = False,
    verify: bool = True,
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
    if harness not in harnesses.HARNESSES:
        raise SystemExit(f"unknown harness {harness!r} — use one of {list(harnesses.HARNESSES)}")
    if not harnesses.installed(harness):
        log(f"  [red]{harness} not found on PATH[/] — install it, or pick the other harness with --harness")
        return 2

    source = source or (SOURCES[0] if yes else pick_source(_ask, log))
    model_given = bool(model)
    model = model or config.backend.model
    log(f"harness: {harness}   source: {source}")

    if source == "harness-login":
        backend = Backend(provider=provider or config.backend.provider,
                          base_url=config.backend.base_url,
                          model=model, api_key_env=api_key_env or config.backend.api_key_env)
        if not (model_given or yes):
            catalog = harnesses.harness_models(harness)
            backend = backend.model_copy(update={"model": pick_model(catalog, backend.model, _ask, log)})
        ready, detail = harnesses.check_auth(harness, backend)
        if ready and not login:
            log(f"  {harness} already has a credential for this provider — no login needed")
        else:
            if not ready:
                log(f"  {harness} reports: {detail}")
            _handover_login(harness, log)
        outcome = _verify(harness, backend, log, runtime_key=False, timeout_s=verify_budget)
        _finish(config, harness, backend, root, outcome, log, registry=False)
        return outcome

    if source == "openai-endpoint":
        base_url = base_url or (DEFAULT_OPENAI_URL if yes else _ask("OpenAI-compatible base URL", DEFAULT_OPENAI_URL))
        api_key_env = api_key_env or (config.backend.api_key_env or "OPENAI_API_KEY" if yes
                                      else _ask("env var holding the key", config.backend.api_key_env or "OPENAI_API_KEY"))
        provider = provider or ("openai" if "api.openai.com" in base_url else "openai-compatible")
        candidate = _endpoint_backend(base_url, model, api_key_env, provider)
        if candidate.resolve_api_key() == "none":
            log(f"  [yellow]${api_key_env} is not set in this shell[/] — the listing and the check "
                "will fail until you export it, then re-run this command")
    else:
        base_url = base_url or (DEFAULT_LOCAL_URL if yes else _ask("local endpoint base URL", DEFAULT_LOCAL_URL))
        provider = provider or "llamacpp"
        candidate = _local_backend(base_url, model, provider)

    listed = harnesses.endpoint_models(candidate)
    if listed:
        log(f"  endpoint answers — {len(listed)} model(s) listed")
    else:
        log(f"  [yellow]{candidate.base_url}/models did not answer[/] — falling back to "
            f"{harness}'s own catalog")
        listed = harnesses.harness_models(harness)
    if model_given or yes:
        log(f"  model: {candidate.model}")
    else:
        candidate = candidate.model_copy(update={"model": pick_model(listed, candidate.model, _ask, log)})

    harnesses.register_provider(harness, candidate, log)
    outcome = _verify(harness, candidate, log, timeout_s=verify_budget) if verify else 0
    _finish(config, harness, candidate, root, outcome, log, registry=True)
    return outcome


def _verify(harness: str, backend: Backend, log, runtime_key: bool = True, timeout_s: int = _VERIFY_TIMEOUT_SEC) -> int:
    """One real prompt through the agent — the only proof that counts."""
    argv = harnesses.verify_argv(harness, backend, runtime_key=runtime_key)
    log(f"  checking: {' '.join(argv[:5])} …")
    log(f"  [dim]budget {timeout_s // 60} min — a local GGUF spends most of it reading the "
        "agent's system prompt; raise [harness] verify_timeout_min if this box is slower[/]")
    rc, output = _run_stream(argv, log, timeout=timeout_s, model=backend.model)
    if rc == 124:
        log(f"  [red]no answer within {timeout_s}s[/] — {backend.model!r} at {backend.base_url} "
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
    log("")
    log(f"next: `sfactory paperclip --provision --root {root}` to put this brain to work"
        if registry else "next: `sfactory paperclip --provision` to put this brain to work")

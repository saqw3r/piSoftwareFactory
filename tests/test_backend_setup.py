"""`sfactory backend` wizard tests — no real pi/omp, no network.

The two seams the outside world hides behind (`harnesses.installed` /
`_run_stream`) are stubbed, so what is under test is the decision sequence:
what gets asked, what gets written, and what the failure says.
"""

from __future__ import annotations

import json

import pytest
import yaml

from pisoftwarefactory import backend_setup, harnesses
from pisoftwarefactory.config import FactoryConfig, load_config, save_config


@pytest.fixture
def fake_harness(monkeypatch):
    monkeypatch.setattr(harnesses, "installed", lambda name: True)
    monkeypatch.setattr(harnesses, "binary", lambda name: name)
    monkeypatch.setattr(harnesses, "spawn_argv", lambda name: [name])  # zcode's node/.cjs prefix
    # No test reaches the network: the endpoint catalog is a seam like the rest.
    monkeypatch.setattr(harnesses, "endpoint_models", lambda b: [])
    # The catalog is one seam: models, providers and per-provider lists all derive
    # from it, so stubbing the pair list keeps all three honest at once.
    monkeypatch.setattr(harnesses, "harness_catalog", lambda name: [])
    # `check_auth` spawns the harness as well. The neutral answer is "the agent
    # keeps no credential for this provider" — what it says for a keyless
    # extension, for zcode, and for a machine where nothing was logged in yet.
    # Tests about the login handover stub this themselves.
    monkeypatch.setattr(harnesses, "check_auth",
                        lambda name, backend: (False, '{"reason":"provider_not_found"}'))
    # Same for the loopback probe behind the interactive source question.
    monkeypatch.setattr(backend_setup, "probe_local_endpoints", lambda: [])
    calls: list[list[str]] = []

    def _stub_run_stream(argv, log, timeout=1, model="the agent", agent_credential=False, provider="", quiet_note=""):
        calls.append(argv)
        log(f"  stub ran {argv[0]}")
        return (0, "OK")

    monkeypatch.setattr(backend_setup, "_run_stream", _stub_run_stream)
    return calls


def _ask(answers: list[str]):
    """Stands in for typer.prompt: hands back the scripted answers in order."""
    remaining = list(answers)

    def ask(text, default=None):
        if not remaining:
            raise AssertionError(f"wizard asked {text!r} with no scripted answer left")
        return remaining.pop(0)

    return ask


def _project(tmp_path) -> FactoryConfig:
    config = FactoryConfig()
    save_config(config, tmp_path)
    return config


def test_local_llama_path_registers_then_verifies_then_saves(tmp_path, fake_harness):
    _project(tmp_path)
    code = backend_setup.run_backend(
        tmp_path, harness="pi", source="llama-local",
        base_url="http://127.0.0.1:8080/v1", model="qwen3.5-9b", verify=True,
        log=lambda _m: None, ask=_ask([]),
    )
    assert code == 0
    assert fake_harness[0][:2] == ["pi", "--provider"]
    block = json.loads((tmp_path / "pi-agent" / "models.json").read_text(encoding="utf-8"))
    assert block["providers"]["llamacpp"]["baseUrl"] == "http://127.0.0.1:8080/v1"
    saved = load_config(tmp_path)
    assert (saved.backend.model, saved.harness.name) == ("qwen3.5-9b", "pi")


def test_omp_choice_writes_yaml_and_the_omp_flag_shape(tmp_path, fake_harness):
    _project(tmp_path)
    code = backend_setup.run_backend(
        tmp_path, harness="omp", source="llama-local",
        base_url="http://127.0.0.1:8080/v1", model="qwen3.5-9b", verify=True,
        log=lambda _m: None, ask=_ask([]),
    )
    assert code == 0
    assert fake_harness[0][:3] == ["omp", "--model", "llamacpp/qwen3.5-9b"]
    assert (tmp_path / "omp-agent" / "models.yml").is_file()


def test_missing_credential_reports_the_login_command_and_changes_nothing(tmp_path, fake_harness, monkeypatch):
    config = _project(tmp_path)
    before = (tmp_path / "factory.toml").read_text(encoding="utf-8")

    def _failing(argv, log, timeout=1, model="the agent", agent_credential=False, provider="", quiet_note=""):
        return (1, "Error: No API key found for llamacpp")

    monkeypatch.setattr(backend_setup, "_run_stream", _failing)
    msgs: list[str] = []
    code = backend_setup.run_backend(
        tmp_path, harness="pi", source="llama-local",
        base_url="http://127.0.0.1:8080/v1", model="qwen3.5-9b", verify=True,
        log=msgs.append, ask=_ask([]),
    )
    assert code == 4
    assert any("/login" in m and "sfactory backend" in m for m in msgs)
    assert (tmp_path / "factory.toml").read_text(encoding="utf-8") == before
    assert load_config(tmp_path).backend.model == config.backend.model


def test_endpoint_listing_drives_the_model_question(tmp_path, fake_harness, monkeypatch):
    _project(tmp_path)
    monkeypatch.setattr(harnesses, "endpoint_models", lambda b: ["qwen3.5-9b", "deepseek-r1"])
    asked: list[str] = []

    def ask(text, default=None):
        asked.append(text)
        return "2"  # pick the second listed model

    backend_setup.run_backend(
        tmp_path, harness="pi", source="llama-local", base_url="http://127.0.0.1:8080/v1",
        log=lambda _m: None, ask=ask,
    )
    assert load_config(tmp_path).backend.model == "deepseek-r1"
    assert asked == ["model"]


def test_interactive_source_choice_asks_for_endpoint_and_key_env(tmp_path, fake_harness, monkeypatch):
    _project(tmp_path)
    monkeypatch.delenv("MY_KEY", raising=False)
    backend_setup.run_backend(
        tmp_path, harness="pi",
        log=lambda _m: None,
        ask=_ask(["2", "https://gw.example/v1", "MY_KEY", "gpt-5.2"]),
    )
    saved = load_config(tmp_path)
    assert saved.backend.base_url == "https://gw.example/v1"
    assert saved.backend.api_key_env == "MY_KEY"
    assert saved.backend.model == "gpt-5.2"


def test_yes_flag_runs_the_local_path_with_no_questions_at_all(tmp_path, fake_harness):
    _project(tmp_path)
    def _no_ask(text, default=None):
        raise AssertionError(f"--yes should not have asked {text!r}")

    code = backend_setup.run_backend(
        tmp_path, harness="pi", source="llama-local", model="qwen3.5-9b", yes=True,
        log=lambda _m: None, ask=_no_ask,
    )
    assert code == 0
    assert load_config(tmp_path).backend.base_url == backend_setup.DEFAULT_LOCAL_URL


def test_company_seed_harness_block_follows_the_choice(tmp_path, fake_harness):
    _project(tmp_path)
    seed = {"company": {"harness": {"name": "pi", "model": "old"}}}
    path = tmp_path / "paperclip.company.json"
    path.write_text(json.dumps(seed), encoding="utf-8")
    backend_setup.run_backend(
        tmp_path, harness="omp", source="llama-local",
        base_url="http://127.0.0.1:8080/v1", model="qwen3.5-9b",
        log=lambda _m: None, ask=_ask([]),
    )
    block = json.loads(path.read_text(encoding="utf-8"))["company"]["harness"]
    assert block["name"] == "omp"
    assert block["command"] == "omp --mode json @brief.md"
    assert block["model"] == "qwen3.5-9b"


def test_unknown_source_number_is_told_back_not_swallowed(tmp_path, fake_harness):
    _project(tmp_path)
    with pytest.raises(SystemExit, match="pick 1, 2 or 3"):
        backend_setup.run_backend(tmp_path, harness="pi", log=lambda _m: None, ask=_ask(["7"]))


def test_a_preset_source_preanswers_the_endpoint_questions(tmp_path, fake_harness):
    """`--source qoder` means openai-endpoint with the preset's answers — no URL/key prompts."""
    _project(tmp_path)
    asked: list[str] = []

    def ask(text, default=None):
        asked.append(text)
        return default

    code = backend_setup.run_backend(
        tmp_path, harness="pi", source="qoder", model="qwen3-coder-plus",
        log=lambda _m: None, ask=ask,
    )
    assert code == 0
    assert asked == []
    saved = load_config(tmp_path)
    assert saved.backend.base_url == backend_setup.PRESETS["qoder"]["base_url"]
    assert saved.backend.api_key_env == "QODER_API_KEY"
    assert saved.backend.provider == "qoder"
    block = json.loads((tmp_path / "pi-agent" / "models.json").read_text(encoding="utf-8"))
    assert block["providers"]["qoder"]["baseUrl"].endswith(":8790/v1")


def test_explicit_flags_win_over_a_preset(tmp_path, fake_harness):
    _project(tmp_path)
    backend_setup.run_backend(
        tmp_path, harness="pi", source="qoder",
        base_url="http://127.0.0.1:9999/v1", api_key_env="MY_KEY", model="m",
        log=lambda _m: None, ask=_ask([]),
    )
    saved = load_config(tmp_path)
    assert (saved.backend.base_url, saved.backend.api_key_env) == ("http://127.0.0.1:9999/v1", "MY_KEY")


def test_yes_with_a_preset_runs_unattended(tmp_path, fake_harness):
    _project(tmp_path)

    def _no_ask(text, default=None):
        raise AssertionError(f"--yes should not have asked {text!r}")

    code = backend_setup.run_backend(
        tmp_path, harness="pi", source="dashscope", yes=True,
        log=lambda _m: None, ask=_no_ask,
    )
    assert code == 0
    saved = load_config(tmp_path)
    assert saved.backend.base_url == backend_setup.PRESETS["dashscope"]["base_url"]
    assert saved.backend.api_key_env == "DASHSCOPE_API_KEY"


def test_rerun_with_nothing_changed_skips_the_proof_but_still_saves(tmp_path, fake_harness):
    """The proof already on file is the proof: a byte-identical re-run proves nothing new.

    A local GGUF verify costs minutes; `--verify` exists precisely for when the
    operator wants to pay them again.
    """
    _project(tmp_path)
    common = {"harness": "pi", "source": "llama-local",
              "base_url": "http://127.0.0.1:8080/v1", "model": "qwen3.5-9b"}
    assert backend_setup.run_backend(tmp_path, verify=True, log=lambda _m: None, ask=_ask([]), **common) == 0
    assert fake_harness, "the first run proves the setup"
    fake_harness.clear()
    msgs: list[str] = []
    assert backend_setup.run_backend(tmp_path, log=msgs.append, ask=_ask([]), **common) == 0
    assert fake_harness == []
    assert any("unchanged" in m for m in msgs)
    assert load_config(tmp_path).backend.model == "qwen3.5-9b"


def test_verify_flag_rechecks_an_unchanged_backend(tmp_path, fake_harness):
    _project(tmp_path)
    common = {"harness": "pi", "source": "llama-local",
              "base_url": "http://127.0.0.1:8080/v1", "model": "qwen3.5-9b"}
    backend_setup.run_backend(tmp_path, log=lambda _m: None, ask=_ask([]), **common)
    fake_harness.clear()
    backend_setup.run_backend(tmp_path, verify=True, log=lambda _m: None, ask=_ask([]), **common)
    assert len(fake_harness) == 1


def test_success_prints_the_replay_one_liner(tmp_path, fake_harness):
    _project(tmp_path)
    msgs: list[str] = []
    backend_setup.run_backend(
        tmp_path, harness="pi", source="qoder", model="qwen3-coder-plus",
        log=msgs.append, ask=_ask([]),
    )
    replays = [m for m in msgs if "replay elsewhere" in m]
    assert len(replays) == 1
    assert "--source openai-endpoint" in replays[0]
    assert "--api-key-env QODER_API_KEY" in replays[0]
    assert replays[0].rstrip().endswith("-y[/]")


def test_a_live_endpoint_is_offered_and_its_preset_adopted(tmp_path, fake_harness, monkeypatch):
    """Bare `sfactory backend` probes loopback first: pick 1 and the URL — plus the
    matching preset's key env — are answered for you."""
    _project(tmp_path)
    qoder_url = backend_setup.PRESETS["qoder"]["base_url"]
    monkeypatch.setattr(backend_setup, "probe_local_endpoints",
                        lambda: [(qoder_url, ["qwen3-coder-plus", "qwen3.5", "a", "b", "c", "d"])])
    code = backend_setup.run_backend(
        tmp_path, harness="pi", log=lambda _m: None,
        ask=_ask(["1", ""]),  # pick the live endpoint; Enter on the model question
    )
    assert code == 0
    saved = load_config(tmp_path)
    assert saved.backend.base_url == qoder_url
    assert saved.backend.api_key_env == "QODER_API_KEY"
    assert saved.backend.provider == "qoder"


def test_skipping_the_live_menu_falls_through_to_the_source_question(tmp_path, fake_harness, monkeypatch):
    _project(tmp_path)
    monkeypatch.setattr(backend_setup, "probe_local_endpoints",
                        lambda: [("http://127.0.0.1:1234/v1", ["lm-studio-model"])])
    backend_setup.run_backend(
        tmp_path, harness="pi", log=lambda _m: None,
        ask=_ask(["", "2", "https://gw.example/v1", "K", "m"]),  # Enter skips the menu
    )
    saved = load_config(tmp_path)
    assert (saved.backend.base_url, saved.backend.api_key_env) == ("https://gw.example/v1", "K")


def test_harness_not_on_path_says_which_one_to_install(tmp_path, monkeypatch):
    _project(tmp_path)
    monkeypatch.setattr(harnesses, "installed", lambda name: False)
    msgs: list[str] = []
    code = backend_setup.run_backend(
        tmp_path, harness="omp", source="llama-local", model="m",
        log=msgs.append, ask=_ask([]),
    )
    assert code == 2
    assert any("omp not found on PATH" in m for m in msgs)


def test_verify_names_the_model_not_the_executable(monkeypatch):
    r"""The quiet-progress line is the operator's only company for minutes.

    It used to print argv[0] — "C:\...\pi.CMD is still reading the prompt" —
    which describes the harness, never changes, and answers nothing. The model
    is the thing that is slow.
    """
    seen: dict = {}

    def fake_stream_agent(cmd, cwd, timeout_s, model, log=None, quiet_s=30, echo_text=False, agent_credential=False, provider="", quiet_note=""):
        seen["model"] = model
        return (0, "OK", "")

    monkeypatch.setattr(backend_setup, "stream_agent", fake_stream_agent)
    backend_setup._run_stream([r"C:\Users\x\pi.CMD", "--model", "qwen3.5-9b"],
                              lambda _m: None, timeout=5, model="qwen3.5-9b")
    assert seen["model"] == "qwen3.5-9b"


def test_a_keyless_agent_provider_is_never_given_the_mocked_key(tmp_path, fake_harness, monkeypatch):
    """Live: `--api-key none` makes freeflow answer 401; without the flag it works.

    The mocked key is a llama.cpp accommodation, not a universal one. A provider
    the agent authenticates itself must get no ``--api-key`` at all, and a wizard
    that also insists on logging in for it is sending the operator off a cliff.
    """
    _project(tmp_path)
    monkeypatch.setattr(harnesses, "check_auth",
                        lambda name, backend: (False, '{"status":"not_ready","reason":"provider_not_found"}'))
    handovers: list[str] = []
    monkeypatch.setattr(backend_setup, "_handover_login", lambda harness, log: handovers.append(harness))
    msgs: list[str] = []
    code = backend_setup.run_backend(
        tmp_path, harness="pi", source="harness-login", provider="freeflow",
        model="ovh-free/gpt-oss-120b", log=msgs.append, ask=_ask([]),
    )

    assert "--api-key" not in fake_harness[-1]
    assert fake_harness[-1][:4] == ["pi", "--provider", "freeflow", "--model"]
    assert handovers == []
    assert "keyless" in " ".join(msgs)
    # the budget line explains the right slowness: this backend is not a GGUF
    assert "local GGUF" not in " ".join(msgs) and "free tier" in " ".join(msgs)
    assert code == 0
    assert load_config(tmp_path).backend.agent_credential is True


def test_a_provider_pi_reports_unauthorized_for_still_hands_over_the_login(tmp_path, fake_harness, monkeypatch):
    """The no-login shortcut is only for providers pi does not manage at all."""
    _project(tmp_path)
    monkeypatch.setattr(harnesses, "check_auth",
                        lambda name, backend: (False, '{"status":"not_ready","reason":"no_credential"}'))
    handovers: list[str] = []
    monkeypatch.setattr(backend_setup, "_handover_login", lambda harness, log: handovers.append(harness))
    backend_setup.run_backend(
        tmp_path, harness="pi", source="harness-login", provider="anthropic",
        model="claude-x", log=lambda _m: None, ask=_ask([]),
    )

    assert handovers == ["pi"]


def test_the_local_server_is_offered_once_under_the_spelling_that_answers(monkeypatch):
    """pi's table says ``llama.cpp``; the block that works says ``llamacpp``.

    Two entries for one machine is how an operator ends up choosing the built-in
    provider that takes no URL and waits silently when nothing is listening.
    """
    monkeypatch.setattr(harnesses, "harness_catalog", lambda name: [
        ("llama.cpp", "bonsai-27b"), ("llama.cpp", "qwen3.5-9b"), ("freeflow", "ovh-free/gpt-oss-120b")])
    choices = dict(backend_setup.provider_choices("pi"))
    assert "llama.cpp" not in choices
    assert choices["llamacpp"] == 2
    assert next(iter(choices)) == "llamacpp"  # busiest first, and counted, not merged away
    assert {"ollama", "qoder", "dashscope"} <= set(choices)  # every preset stays offerable


def test_the_wizard_asks_the_provider_before_any_model_list(tmp_path, fake_harness, monkeypatch):
    """One 950-row list answers neither question — and the freeflow id picked
    from it landed on a dead llama.cpp endpoint."""
    _project(tmp_path)
    monkeypatch.setattr(harnesses, "harness_catalog", lambda name: [
        ("llama.cpp", "bonsai-27b"), ("llama.cpp", "qwen3.5-9b"), ("freeflow", "ovh-free/gpt-oss-120b")])
    asked: list[str] = []

    def ask(text, default=None):
        asked.append(text)
        return {"provider": "2", "model": "1"}[text]  # freeflow, then its only model

    msgs: list[str] = []
    assert backend_setup.run_backend(tmp_path, harness="pi", source="harness-login",
                                     log=msgs.append, ask=ask) == 0
    assert asked == ["provider", "model"]
    saved = load_config(tmp_path).backend
    assert (saved.provider, saved.model, saved.agent_credential) == ("freeflow", "ovh-free/gpt-oss-120b", True)
    assert fake_harness[-1][:4] == ["pi", "--provider", "freeflow", "--model"]
    assert "--api-key" not in fake_harness[-1]


def test_a_llama_choice_lists_what_the_server_serves_right_now(tmp_path, fake_harness, monkeypatch):
    """The loaded GGUFs, not the registry's wish list — and the gap said out loud."""
    _project(tmp_path)
    monkeypatch.setattr(harnesses, "harness_catalog", lambda name: [
        ("llama.cpp", "bonsai-27b"), ("llama.cpp", "qwen3.5-9b"), ("openrouter", "x/y")])
    monkeypatch.setattr(harnesses, "endpoint_models", lambda b: ["qwen3.5-9b"])
    msgs: list[str] = []
    assert backend_setup.run_backend(
        tmp_path, harness="pi", source="harness-login", provider="llama.cpp",
        ask=_ask(["1"]), log=msgs.append) == 0
    saved = load_config(tmp_path).backend
    assert (saved.provider, saved.model) == ("llamacpp", "qwen3.5-9b")  # pi's spelling, factory's block
    assert "1 model(s) loaded on http://127.0.0.1:8080/v1 right now" in " ".join(msgs)
    assert "not served by this server" in " ".join(msgs)  # bonsai-27b is named, but not loaded
    # the endpoint answer goes first, the registry's extra ids after it
    shown = [m for m in msgs if m.strip().startswith("1. ")]
    assert shown[0].strip() == "1. qwen3.5-9b"


def test_a_down_server_is_named_instead_of_a_model_being_blamed(tmp_path, fake_harness, monkeypatch):
    """No /models answer means nothing is listening — the ids below are unproven."""
    _project(tmp_path)
    monkeypatch.setattr(harnesses, "harness_catalog", lambda name: [("llama.cpp", "bonsai-27b")])
    msgs: list[str] = []
    backend_setup.run_backend(tmp_path, harness="pi", source="harness-login", provider="llamacpp",
                              ask=_ask(["1"]), log=msgs.append)
    text = " ".join(msgs)
    assert "http://127.0.0.1:8080/v1/models did not answer" in text
    assert "start the server" in text and "unproven" in text


def test_a_long_model_list_says_what_it_is_not_showing(tmp_path, fake_harness, monkeypatch):
    """25 GGUFs loaded, 15 lines printed — the other ten must be named as absent."""
    _project(tmp_path)
    monkeypatch.setattr(harnesses, "endpoint_models",
                        lambda b: [f"model-{i}.gguf" for i in range(25)])
    msgs: list[str] = []
    backend_setup.run_backend(
        tmp_path, harness="pi", source="harness-login", provider="llamacpp",
        ask=_ask(["2"]), log=msgs.append)
    text = " ".join(msgs)
    assert "10 more — type the id" in text
    assert sum(1 for m in msgs if m.strip().startswith("16. ")) == 0  # the cut is real
    assert load_config(tmp_path).backend.model == "model-1.gguf"


def test_the_check_line_names_the_command_but_never_the_key(tmp_path, fake_harness, monkeypatch):
    _project(tmp_path)
    monkeypatch.setenv("QODER_API_KEY", "sk-live-secret")
    msgs: list[str] = []
    backend_setup.run_backend(
        tmp_path, harness="pi", source="qoder", model="m", verify=True,
        log=msgs.append, ask=_ask([]),
    )
    assert not any("sk-live-secret" in m for m in msgs)
    assert any("--api-key ***" in m for m in msgs)


def test_a_failed_verify_prints_the_stage_checklist(tmp_path, fake_harness, monkeypatch):
    """A red wall becomes 'stage 3': endpoint ✓, registry ✓, verify ✗."""
    _project(tmp_path)
    monkeypatch.setattr(harnesses, "endpoint_models", lambda b: ["qwen3-coder-plus"])

    def _failing(argv, log, timeout=1, model="the agent", agent_credential=False, provider="", quiet_note=""):
        return (1, "Error: 401 Unauthorized")

    monkeypatch.setattr(backend_setup, "_run_stream", _failing)
    msgs: list[str] = []
    code = backend_setup.run_backend(
        tmp_path, harness="pi", source="qoder", model="qwen3-coder-plus", verify=True,
        log=msgs.append, ask=_ask([]),
    )
    assert code == 4
    import re
    joined = re.sub(r"\[/?(?:green|red|yellow|cyan|dim)?\]", "", " ".join(msgs))
    assert "✓ endpoint /models" in joined and "✓ registry" in joined and "✗ verify prompt" in joined


def test_a_silent_endpoint_is_marked_but_does_not_stop_the_setup(tmp_path, fake_harness):
    _project(tmp_path)
    msgs: list[str] = []
    code = backend_setup.run_backend(
        tmp_path, harness="pi", source="llama-local",
        base_url="http://127.0.0.1:8080/v1", model="qwen3.5-9b", verify=True,
        log=msgs.append, ask=_ask([]),
    )
    assert code == 0
    import re
    stripped = re.sub(r"\[/?(?:green|red|yellow|cyan|dim)?\]", "", " ".join(msgs))
    assert "✗" in stripped and "/models did not answer" in stripped


def test_show_reports_sync_and_never_writes(tmp_path, fake_harness):
    _project(tmp_path)
    common = {"harness": "pi", "source": "llama-local",
              "base_url": "http://127.0.0.1:8080/v1", "model": "qwen3.5-9b"}
    backend_setup.run_backend(tmp_path, verify=True, log=lambda _m: None, ask=_ask([]), **common)
    registry = tmp_path / "pi-agent" / "models.json"
    before = registry.read_text(encoding="utf-8")
    msgs: list[str] = []
    code = backend_setup.run_backend(tmp_path, show=True, log=msgs.append, ask=_ask([]), **common)
    assert code == 0
    assert any("registry in sync" in m for m in msgs)
    assert registry.read_text(encoding="utf-8") == before


def test_show_detects_drift_when_the_registry_was_edited_by_hand(tmp_path, fake_harness):
    _project(tmp_path)
    common = {"harness": "pi", "source": "llama-local",
              "base_url": "http://127.0.0.1:8080/v1", "model": "qwen3.5-9b"}
    backend_setup.run_backend(tmp_path, verify=True, log=lambda _m: None, ask=_ask([]), **common)
    registry = tmp_path / "pi-agent" / "models.json"
    data = json.loads(registry.read_text(encoding="utf-8"))
    data["providers"]["llamacpp"]["baseUrl"] = "http://127.0.0.1:9999/v1"
    registry.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    msgs: list[str] = []
    code = backend_setup.run_backend(tmp_path, show=True, log=msgs.append, ask=_ask([]), **common)
    assert code == 1
    joined = " ".join(msgs)
    assert "registry drift" in joined and "127.0.0.1:9999" in joined and "re-registers" in joined


def test_zcode_skips_the_registry_and_saves_an_agent_credential(tmp_path, fake_harness):
    """`--harness zcode` collapses to the agent-credential path: the CLI serves its own model."""
    _project(tmp_path)
    msgs: list[str] = []
    code = backend_setup.run_backend(
        tmp_path, harness="zcode", source="llama-local",
        base_url="http://127.0.0.1:8080/v1", model="nominal",
        log=msgs.append, ask=_ask([]),
    )
    assert code == 0
    assert not (tmp_path / "pi-agent" / "models.json").exists()  # no registry written
    assert not (tmp_path / "omp-agent" / "models.yml").exists()
    saved = load_config(tmp_path)
    assert (saved.harness.name, saved.backend.agent_credential) == ("zcode", True)
    joined = " ".join(msgs)
    assert "no provider registry is written" in joined
    assert "the agent's own settings serve it" in joined
    assert fake_harness and fake_harness[0][0] == "zcode"  # the proof ran through zcode


def test_zcode_seed_command_uses_attach(tmp_path, fake_harness):
    _project(tmp_path)
    path = tmp_path / "paperclip.company.json"
    path.write_text(json.dumps({"company": {"harness": {"name": "pi", "model": "old"}}}), encoding="utf-8")
    backend_setup.run_backend(
        tmp_path, harness="zcode", source="llama-local", model="nominal",
        log=lambda _m: None, ask=_ask([]),
    )
    block = json.loads(path.read_text(encoding="utf-8"))["company"]["harness"]
    assert block["command"] == "zcode -p --attach brief.md"


def test_show_says_zcode_has_no_registry_to_check(tmp_path, fake_harness):
    _project(tmp_path)
    cfg = FactoryConfig()
    cfg.harness.name = "zcode"
    cfg.backend = cfg.backend.model_copy(update={"agent_credential": True})
    save_config(cfg, tmp_path)
    msgs: list[str] = []
    code = backend_setup.run_backend(tmp_path, show=True, log=msgs.append, ask=_ask([]))
    assert code == 0
    assert any("no provider registry to check" in m for m in msgs)


def test_provider_switch_reregisters_and_saves(tmp_path, fake_harness):
    """`sfactory provider omp` keeps the backend, re-registers it for omp, proves it."""
    _project(tmp_path)
    msgs: list[str] = []
    code = backend_setup.run_provider(
        tmp_path, harness="omp", yes=True, log=msgs.append, ask=_ask([]),
    )
    assert code == 0
    block = yaml.safe_load((tmp_path / "omp-agent" / "models.yml").read_text(encoding="utf-8"))
    assert block["providers"]["llamacpp"]["baseUrl"] == "http://127.0.0.1:8080/v1"
    saved = load_config(tmp_path)
    assert saved.harness.name == "omp"
    assert any("brain:" in m for m in msgs)
    assert "already" not in " ".join(msgs)[:0]  # no-op guard below


def test_provider_switch_to_zcode_writes_no_registry(tmp_path, fake_harness):
    _project(tmp_path)
    code = backend_setup.run_provider(tmp_path, harness="zcode", yes=True,
                                      log=lambda _m: None, ask=_ask([]))
    assert code == 0
    assert not (tmp_path / "pi-agent" / "models.json").exists()
    assert load_config(tmp_path).harness.name == "zcode"


def test_provider_same_choice_is_a_polite_noop(tmp_path, fake_harness):
    _project(tmp_path)  # default harness pi
    msgs: list[str] = []
    code = backend_setup.run_provider(tmp_path, harness="pi", yes=True,
                                      log=msgs.append, ask=_ask([]))
    assert code == 0
    assert any("already" in m for m in msgs)


def test_model_switch_picks_from_the_live_list_and_saves(tmp_path, fake_harness, monkeypatch):
    _project(tmp_path)
    monkeypatch.setattr(harnesses, "endpoint_models", lambda b: ["qwen3.5-9b", "deepseek-r1"])
    code = backend_setup.run_model(
        tmp_path, model="deepseek-r1", yes=True, log=lambda _m: None, ask=_ask([]),
    )
    assert code == 0
    saved = load_config(tmp_path)
    assert saved.backend.model == "deepseek-r1"
    block = json.loads((tmp_path / "pi-agent" / "models.json").read_text(encoding="utf-8"))
    assert block["providers"]["llamacpp"]["models"] == [{"id": "deepseek-r1"}]


def test_model_for_zcode_is_honestly_refused(tmp_path, fake_harness):
    """zcode serves its own model — the factory saying otherwise would be a lie."""
    _project(tmp_path)
    cfg = load_config(tmp_path)
    cfg.harness.name = "zcode"
    save_config(cfg, tmp_path)
    msgs: list[str] = []
    code = backend_setup.run_model(tmp_path, model="whatever", yes=True,
                                   log=msgs.append, ask=_ask([]))
    assert code == 1
    assert any("ZCode app" in m for m in msgs)


def test_model_off_catalog_warns_but_tries(tmp_path, fake_harness, monkeypatch):
    _project(tmp_path)
    monkeypatch.setattr(harnesses, "endpoint_models", lambda b: ["qwen3.5-9b"])
    msgs: list[str] = []
    code = backend_setup.run_model(
        tmp_path, model="mystery-model", yes=True, log=msgs.append, ask=_ask([]),
    )
    assert code == 0
    assert any("not in the endpoint's catalog" in m for m in msgs)
    assert load_config(tmp_path).backend.model == "mystery-model"

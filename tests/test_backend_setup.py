"""`sfactory backend` wizard tests — no real pi/omp, no network.

The two seams the outside world hides behind (`harnesses.installed` /
`_run_stream`) are stubbed, so what is under test is the decision sequence:
what gets asked, what gets written, and what the failure says.
"""

from __future__ import annotations

import json

import pytest

from pisoftwarefactory import backend_setup, harnesses
from pisoftwarefactory.config import FactoryConfig, load_config, save_config


@pytest.fixture
def fake_harness(monkeypatch):
    monkeypatch.setattr(harnesses, "installed", lambda name: True)
    monkeypatch.setattr(harnesses, "binary", lambda name: name)
    # No test reaches the network: the endpoint catalog is a seam like the rest.
    monkeypatch.setattr(harnesses, "endpoint_models", lambda b: [])
    monkeypatch.setattr(harnesses, "harness_models", lambda name: [])
    calls: list[list[str]] = []

    def _stub_run_stream(argv, log, timeout=1, model="the agent"):
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
        base_url="http://127.0.0.1:8080/v1", model="qwen3.5-9b",
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
        base_url="http://127.0.0.1:8080/v1", model="qwen3.5-9b",
        log=lambda _m: None, ask=_ask([]),
    )
    assert code == 0
    assert fake_harness[0][:3] == ["omp", "--model", "llamacpp/qwen3.5-9b"]
    assert (tmp_path / "omp-agent" / "models.yml").is_file()


def test_missing_credential_reports_the_login_command_and_changes_nothing(tmp_path, fake_harness, monkeypatch):
    config = _project(tmp_path)
    before = (tmp_path / "factory.toml").read_text(encoding="utf-8")

    def _failing(argv, log, timeout=1, model="the agent"):
        return (1, "Error: No API key found for llamacpp")

    monkeypatch.setattr(backend_setup, "_run_stream", _failing)
    msgs: list[str] = []
    code = backend_setup.run_backend(
        tmp_path, harness="pi", source="llama-local",
        base_url="http://127.0.0.1:8080/v1", model="qwen3.5-9b",
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

    def fake_stream_agent(cmd, cwd, timeout_s, model, log=None, quiet_s=30, echo_text=False):
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

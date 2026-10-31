"""Harness registry tests: pi (JSON) and omp (YAML) behind one writer.

The agent dirs are redirected to tmp_path by the autouse conftest fixture, so
nothing here can touch the developer's real ~/.pi or ~/.omp.
"""

from __future__ import annotations

import json

import pytest
import yaml

from pisoftwarefactory import harnesses
from pisoftwarefactory.config import Backend


def _backend(**over) -> Backend:
    return Backend.model_validate({"provider": "llamacpp", "model": "qwen3.5-9b", **over})


def test_pi_registry_is_json_with_bare_model_id(tmp_path):
    log: list[str] = []
    state = harnesses.register_provider("pi", _backend(), log=log.append)
    assert state == "created"
    path = tmp_path / "pi-agent" / "models.json"
    block = json.loads(path.read_text(encoding="utf-8"))["providers"]["llamacpp"]
    assert block["models"] == [{"id": "qwen3.5-9b"}]
    assert block["apiKey"] == "none"  # llama.cpp mock key, never a secret
    assert "created" in "".join(log)


def test_omp_registry_is_yaml_and_holds_the_served_model_id(tmp_path):
    """The registry id goes on the wire verbatim — `llamacpp/x` makes llama-router 400."""
    harnesses.register_provider("omp", _backend(), log=lambda _m: None)
    path = tmp_path / "omp-agent" / "models.yml"
    block = yaml.safe_load(path.read_text(encoding="utf-8"))["providers"]["llamacpp"]
    assert block["models"] == [{"id": "qwen3.5-9b"}]
    assert harnesses.selectable_model_id("omp", _backend()) == "llamacpp/qwen3.5-9b"


def test_second_run_is_a_no_op_and_keeps_the_file(tmp_path):
    backend = _backend()
    harnesses.register_provider("pi", backend, log=lambda _m: None)
    path = tmp_path / "pi-agent" / "models.json"
    before = path.read_text(encoding="utf-8")
    assert harnesses.register_provider("pi", backend, log=lambda _m: None) == "already-registered"
    assert path.read_text(encoding="utf-8") == before
    assert not path.with_suffix(".json.bak").exists()


def test_existing_providers_survive_the_merge(tmp_path):
    omp_dir = tmp_path / "omp-agent"
    omp_dir.mkdir(parents=True)
    (omp_dir / "models.yml").write_text(
        yaml.safe_dump({"providers": {"orcarouter": {"baseUrl": "https://x/v1", "apiKey": "sk-secret"}}}),
        encoding="utf-8",
    )
    harnesses.register_provider("omp", _backend(), log=lambda _m: None)
    data = yaml.safe_load((omp_dir / "models.yml").read_text(encoding="utf-8"))
    assert data["providers"]["orcarouter"]["apiKey"] == "sk-secret"
    assert "llamacpp" in data["providers"]
    # The pre-existing file was copied aside before the first modification.
    assert (omp_dir / "models.yml.bak").is_file()


def test_unparseable_registry_raises_with_the_fix_instead_of_overwriting(tmp_path):
    pi_dir = tmp_path / "pi-agent"
    pi_dir.mkdir(parents=True)
    broken = pi_dir / "models.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(harnesses.HarnessError, match="fix or remove"):
        harnesses.register_provider("pi", _backend(), log=lambda _m: None)
    assert broken.read_text(encoding="utf-8") == "{not json"


def test_unset_key_env_warns_before_writing_the_placeholder(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    backend = _backend(api_key_env="OPENAI_API_KEY")
    msgs: list[str] = []
    harnesses.register_provider("pi", backend, log=msgs.append)
    assert any("OPENAI_API_KEY is not set" in m for m in msgs)


def test_verify_argv_matches_each_harness_flag_shape(monkeypatch):
    monkeypatch.setattr(harnesses, "binary", lambda name: name)
    backend = _backend()
    assert harnesses.verify_argv("pi", backend) == [
        "pi", "--provider", "llamacpp", "--model", "qwen3.5-9b", "--api-key", "none", "--print",
        harnesses._VERIFY_PROMPT,
    ]
    argv = harnesses.verify_argv("omp", backend)
    assert argv[:3] == ["omp", "--model", "llamacpp/qwen3.5-9b"]


def test_subscription_check_drops_the_runtime_key_flag(monkeypatch):
    """A stored OAuth credential must be proven as-is — a mocked key would override it."""
    monkeypatch.setattr(harnesses, "binary", lambda name: name)
    argv = harnesses.verify_argv("pi", _backend(provider="openrouter"), runtime_key=False)
    assert "--api-key" not in argv


def test_spawn_uses_the_resolved_launcher_path(monkeypatch):
    """Windows runs npm's `pi.CMD` only when the extension is in the path given to it."""
    monkeypatch.setattr(harnesses.shutil, "which", lambda name: f"C:\\npm\\{name.upper()}.CMD")
    assert harnesses.verify_argv("pi", _backend())[0] == "C:\\npm\\PI.CMD"
    assert harnesses.login_argv("pi") == ["C:\\npm\\PI.CMD"]


def test_login_handoff_is_the_harness_own_command(monkeypatch):
    monkeypatch.setattr(harnesses, "binary", lambda name: name)
    assert harnesses.login_argv("omp") == ["omp", "login"]
    assert harnesses.login_argv("pi") == ["pi"]
    assert "/login" in harnesses.login_hint("pi")

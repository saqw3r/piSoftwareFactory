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


def test_mask_argv_hides_the_runtime_key():
    """The key rides in the worker command line; the first argv log must not leak it."""
    from pisoftwarefactory.harnesses import mask_argv

    argv = ["pi", "--provider", "qoder", "--model", "m", "--api-key", "sk-live-secret",
            "--print", "Reply with exactly: OK"]
    masked = mask_argv(argv)
    assert "sk-live-secret" not in " ".join(masked)
    assert masked[-2:] == argv[-2:]  # everything except the key survives
    assert argv == argv  # the caller's list is not mutated


def test_zcode_has_no_registry_and_refuses_to_invent_one():
    """sfactory never writes into zcode's configuration — its model is its own business."""
    assert harnesses.has_registry("zcode") is False
    with pytest.raises(harnesses.HarnessError, match="no provider registry"):
        harnesses.registry_path("zcode")


def test_zcode_spawns_through_the_desktop_bundle(monkeypatch, tmp_path):
    """No PATH shim: the .cjs is launched with a node prefix (ADR-0019)."""
    cjs = tmp_path / "zcode.cjs"
    cjs.write_text("/**/", encoding="utf-8")
    monkeypatch.setattr(harnesses.shutil, "which", lambda name: None)
    monkeypatch.setattr(harnesses, "_zcode_cjs", lambda: cjs)
    argv = harnesses.spawn_argv("zcode")
    assert argv == ["node", str(cjs)]
    assert harnesses.installed("zcode") is True
    monkeypatch.setattr(harnesses, "_zcode_cjs", lambda: tmp_path / "absent.cjs")
    assert harnesses.installed("zcode") is False


def test_zcode_prefers_the_path_shim_when_one_exists(monkeypatch):
    monkeypatch.setattr(harnesses.shutil, "which", lambda name: f"C:\\npm\\{name.upper()}.CMD")
    assert harnesses.spawn_argv("zcode") == ["C:\\npm\\ZCODE.CMD"]


def test_zcode_verify_login_models_and_auth_shapes(monkeypatch):
    """zcode takes no provider/model/key flags — the proof is a real one-shot prompt."""
    monkeypatch.setattr(harnesses, "spawn_argv", lambda name: ["zcode"])
    backend = _backend()
    assert harnesses.verify_argv("zcode", backend) == [
        "zcode", "-p", harnesses._VERIFY_PROMPT, "--json", "--mode", "plan"]
    assert harnesses.login_argv("zcode") == ["zcode", "login"]
    assert "zcode login" in harnesses.login_hint("zcode")
    assert harnesses.harness_models("zcode") == []
    ready, detail = harnesses.check_auth("zcode", backend)
    assert ready is False
    assert "provider_not_found" in detail  # backend_setup's skip-login path


def test_zcode_spawn_injects_both_provider_configs(monkeypatch, tmp_path):
    """Outside the Desktop app the headless CLI cannot locate its provider configs
    (无法定位 CLI ZCode Built-in Provider) — spawn_argv injects the app's own
    on-disk defaults, never overriding what the environment already says."""
    import os

    fake_home = tmp_path / "home"
    cfg = fake_home / ".zcode" / "v2" / "provider_config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{}", encoding="utf-8")
    runtime = fake_home / ".zcode" / "v2" / "runtime" / "provider" / "windows-x86_64"
    older = runtime / "3.12.2" / "endpoint-abc"
    newer = runtime / "3.14.0" / "endpoint-abc"
    for d in (older, newer):
        d.mkdir(parents=True)
        (d / "zcode-builtin.json").write_text("{}", encoding="utf-8")
    os.utime(older / "zcode-builtin.json", (1_000, 1_000))
    os.utime(newer / "zcode-builtin.json", (2_000, 2_000))
    monkeypatch.delenv("ZCODE_PERSONAL_PROVIDER_CONFIG_FILE", raising=False)
    monkeypatch.delenv("ZCODE_BUILTIN_PROVIDER_CONFIG_FILE", raising=False)
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(harnesses.shutil, "which", lambda name: None)
    monkeypatch.setattr(harnesses, "_zcode_cjs", lambda: tmp_path / "absent.cjs")

    harnesses.spawn_argv("zcode")

    assert os.environ["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"] == str(cfg)
    assert os.environ["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"] == str(newer / "zcode-builtin.json")

    monkeypatch.setenv("ZCODE_PERSONAL_PROVIDER_CONFIG_FILE", r"C:\custom\personal.json")
    monkeypatch.setenv("ZCODE_BUILTIN_PROVIDER_CONFIG_FILE", r"C:\custom\builtin.json")
    harnesses.spawn_argv("zcode")
    assert os.environ["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"] == r"C:\custom\personal.json"
    assert os.environ["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"] == r"C:\custom\builtin.json"


def test_repair_empty_keys_fixes_whole_file_rejections(monkeypatch, tmp_path):
    """pi 1.5.0 validates models.json and rejects the entire file over one empty
    apiKey — 'Unknown provider' for everything. The repair placeholders them."""
    import yaml

    pi_dir = tmp_path / "pi-agent"
    pi_dir.mkdir(parents=True)
    (pi_dir / "models.json").write_text(json.dumps({
        "providers": {
            "freeflow": {"baseUrl": "http://x/v1", "api": "openai-completions", "apiKey": ""},
            "llamacpp": {"baseUrl": "http://127.0.0.1:8080/v1", "api": "openai-completions",
                         "apiKey": "none", "models": [{"id": "qwen3.5-9b"}]},
        }
    }), encoding="utf-8")
    msgs: list[str] = []
    changed = harnesses.repair_empty_keys("pi", log=msgs.append)
    assert changed == 1
    data = json.loads((pi_dir / "models.json").read_text(encoding="utf-8"))
    assert data["providers"]["freeflow"]["apiKey"] == "none"
    assert data["providers"]["llamacpp"]["apiKey"] == "none"
    assert (pi_dir / "models.json.bak").is_file()
    assert any("repaired" in m for m in msgs)

    omp_dir = tmp_path / "omp-agent"
    omp_dir.mkdir(parents=True)
    (omp_dir / "models.yml").write_text(yaml.safe_dump(
        {"providers": {"a": {"apiKey": ""}}}), encoding="utf-8")
    assert harnesses.repair_empty_keys("omp") == 1
    assert yaml.safe_load((omp_dir / "models.yml").read_text(encoding="utf-8"))["providers"]["a"]["apiKey"] == "none"

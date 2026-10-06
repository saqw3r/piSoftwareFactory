"""Config model round-trip (ADR-0012 factory.toml)."""

from __future__ import annotations

from pisoftwarefactory.config import FactoryConfig, bank_id_for, load_config, save_config


def test_roundtrip_defaults(tmp_path):
    cfg = FactoryConfig()
    save_config(cfg, tmp_path)
    loaded = load_config(tmp_path)
    assert loaded == cfg


def test_local_first_defaults():
    cfg = FactoryConfig()
    assert cfg.backend.base_url.startswith("http://127.0.0.1")
    assert cfg.backend.context_window == 65536
    assert cfg.harness.name == "pi"
    assert cfg.release.main_branch == "main"
    assert cfg.intake.provider == "auto"


def test_bank_id_derived_from_dir(tmp_path):
    (tmp_path / "My Repo").mkdir()
    cfg = FactoryConfig()
    assert bank_id_for(tmp_path / "My Repo", cfg) == "my-repo"
    cfg.memory.bank_id = "explicit"
    assert bank_id_for(tmp_path, cfg) == "explicit"


def test_partial_toml_uses_defaults(tmp_path):
    (tmp_path / "factory.toml").write_text("[backend]\nmodel = \"other\"\n", encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.backend.model == "other"
    assert cfg.backend.context_window == 65536


def test_backend_generalized_url_plus_key(monkeypatch):
    cfg = FactoryConfig()
    # default is llama.cpp with a mocked key
    assert cfg.backend.provider == "llamacpp"
    assert cfg.backend.api_key_env == ""
    assert cfg.backend.resolve_api_key() == "none"

    # OpenAI swap: URL + env key, secret never in factory.toml
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg.backend.provider = "openai"
    cfg.backend.base_url = "https://api.openai.com/v1"
    cfg.backend.api_key_env = "OPENAI_API_KEY"
    assert cfg.backend.resolve_api_key() == "sk-test"

    monkeypatch.delenv("OPENAI_API_KEY")
    assert cfg.backend.resolve_api_key() == "none"

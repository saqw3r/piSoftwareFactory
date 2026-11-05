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


def test_agent_credential_means_no_key_at_all(monkeypatch):
    """Three states, and the middle one is the difference between 401 and an answer."""
    from pisoftwarefactory.config import Backend

    monkeypatch.delenv("SOME_KEY_ENV", raising=False)
    assert Backend().resolve_api_key() == "none"                       # local llama.cpp
    assert Backend(api_key_env="SOME_KEY_ENV").resolve_api_key() == "none"  # env var unset
    assert Backend(agent_credential=True, api_key_env="SOME_KEY_ENV").resolve_api_key() == ""


def test_env_file_loads_unexported_keys_and_never_overrides(tmp_path, monkeypatch):
    """.env is the per-project place the key survives the shell that wrote it.

    factory.toml stores only the env var's name; an `export` dies with its
    shell, so every command that loads the config also loads root/.env —
    gitignored, existing environment always winning.
    """
    from pisoftwarefactory.config import load_env_file

    monkeypatch.setenv("EXISTING", "from-shell")
    (tmp_path / ".env").write_text(
        "# comment\n\nNEW_KEY = sk-abc123\nQUOTED='in-quotes'\nEXISTING=from-file\n"
        "NOT_A_PAIR\n", encoding="utf-8")
    monkeypatch.delenv("NEW_KEY", raising=False)
    monkeypatch.delenv("QUOTED", raising=False)

    loaded = load_env_file(tmp_path)

    assert sorted(loaded) == ["NEW_KEY", "QUOTED"]
    import os
    assert os.environ["NEW_KEY"] == "sk-abc123"
    assert os.environ["QUOTED"] == "in-quotes"
    assert os.environ["EXISTING"] == "from-shell"  # the shell wins


def test_export_hint_matches_the_shell(monkeypatch):
    from pisoftwarefactory.config import export_hint

    monkeypatch.setattr("pisoftwarefactory.config.os.name", "nt")
    monkeypatch.setenv("PSModulePath", r"C:\PowerShell Modules")
    hint = export_hint("QODER_API_KEY")
    assert "$env:QODER_API_KEY" in hint and ".env" in hint

    monkeypatch.delenv("PSModulePath")
    assert "setx QODER_API_KEY" in export_hint("QODER_API_KEY")

    monkeypatch.setattr("pisoftwarefactory.config.os.name", "posix")
    assert "export QODER_API_KEY" in export_hint("QODER_API_KEY")

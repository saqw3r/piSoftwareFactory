"""Scaffold end-to-end into a temp project (offline; backend probe falls back)."""

from __future__ import annotations

import json

from pisoftwarefactory.config import load_config, save_config
from pisoftwarefactory.scaffold import run_init


def _seed_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")
    (tmp_path / "go.mod").touch()


def test_init_scaffolds_and_commits(tmp_path, capsys):
    _seed_project(tmp_path)
    run_init(target=tmp_path, auto=True, langs="", force=False, log=print)

    for rel in (
        "AGENTS.md", "factory.toml", "launch.graph.json", ".factory/gates/preflight.py",
        ".factory/gates/interlock_check.py", ".factory/lanes/lane.py", "memory/reflections.json",
        "hindsight.bootstrap.md", "paperclip.company.json",
    ):
        assert (tmp_path / rel).is_file(), rel

    cfg = load_config(tmp_path)
    assert cfg.auto is True
    assert sorted(cfg.languages) == ["go", "python"]
    assert cfg.memory.bank_id == tmp_path.name.lower()

    # models.json belongs in the pi AGENT dir (PI_CODING_AGENT_DIR fixture), not the project
    assert not (tmp_path / ".pi").exists()
    agent_dir = tmp_path / "pi-agent"
    models = json.loads((agent_dir / "models.json").read_text(encoding="utf-8"))
    assert models["providers"]["llamacpp"]["api"] == "openai-completions"

    # baseline commit exists and tree is clean (worktrees/release depend on it)
    import subprocess

    head = subprocess.run(("git", "-C", str(tmp_path), "rev-parse", "--verify", "HEAD"), capture_output=True)
    assert head.returncode == 0
    status = subprocess.run(("git", "-C", str(tmp_path), "status", "--porcelain"), capture_output=True, text=True)
    assert status.stdout.strip() == "", status.stdout

    # The machine's brain must be ignored, not merely untracked: re-pointing the
    # model is what `sfactory backend` does, and that must not be the thing that
    # blocks `sfactory release`'s clean-tree gate.
    tracked = subprocess.run(("git", "-C", str(tmp_path), "ls-files", "factory.toml"),
                             capture_output=True, text=True, check=False)
    assert tracked.stdout.strip() == ""
    (tmp_path / "factory.toml").write_text("auto = true\n", encoding="utf-8")
    after = subprocess.run(("git", "-C", str(tmp_path), "status", "--porcelain"),
                           capture_output=True, text=True, check=False)
    assert after.stdout.strip() == "", after.stdout


def test_init_idempotent_without_force(tmp_path, capsys):
    _seed_project(tmp_path)
    run_init(target=tmp_path, auto=False, langs="python", force=False, log=print)
    first = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    run_init(target=tmp_path, auto=False, langs="python", force=False, log=print)
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == first


def test_init_rejects_unknown_language(tmp_path):
    try:
        run_init(target=tmp_path, auto=False, langs="cobol", force=False, log=print)
    except SystemExit as exc:
        assert "cobol" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_init_does_not_reset_the_machines_own_settings(tmp_path, monkeypatch):
    """The bug this pins: re-running `init` silently reverted a chosen backend.

    `sfactory init` built a fresh FactoryConfig, so the operator's model and the
    time budgets sized to that box were replaced by defaults — and the next run
    went back to a model that answers with an empty message.
    """
    _seed_project(tmp_path)
    from pisoftwarefactory.integrations.llama import BackendInfo

    monkeypatch.setattr("pisoftwarefactory.integrations.llama.probe_backend",
                        lambda url: BackendInfo(base_url=url, models=["other", "qwen3.5-9b"],
                                                context_window=262144))
    run_init(target=tmp_path, auto=False, langs="", force=False, log=lambda _m: None)
    cfg = load_config(tmp_path)
    cfg.backend.model = "qwen3.5-9b"
    cfg.harness.worker_timeout_min = 120
    save_config(cfg, tmp_path)

    run_init(target=tmp_path, auto=False, langs="", force=False, log=lambda _m: None)
    after = load_config(tmp_path)
    assert after.backend.model == "qwen3.5-9b"
    assert after.harness.worker_timeout_min == 120
    # detection still wins where it is the better authority
    assert sorted(after.languages) == ["go", "python"]


def test_probe_keeps_a_model_the_endpoint_serves(monkeypatch):
    from pisoftwarefactory import scaffold
    from pisoftwarefactory.config import FactoryConfig
    from pisoftwarefactory.integrations.llama import BackendInfo

    msgs: list[str] = []
    monkeypatch.setattr("pisoftwarefactory.integrations.llama.probe_backend",
                        lambda url: BackendInfo(base_url=url, models=["zzz-first", "qwen3.5-9b"],
                                                context_window=65536))
    config = FactoryConfig(backend={"model": "qwen3.5-9b"})
    scaffold._probe_backend(config, msgs.append)

    assert config.backend.model == "qwen3.5-9b", "models[0] must not win over a served model"
    assert "is served" in msgs[0]


def test_probe_replaces_only_a_model_nobody_serves(monkeypatch):
    from pisoftwarefactory import scaffold
    from pisoftwarefactory.config import FactoryConfig
    from pisoftwarefactory.integrations.llama import BackendInfo

    msgs: list[str] = []
    monkeypatch.setattr("pisoftwarefactory.integrations.llama.probe_backend",
                        lambda url: BackendInfo(base_url=url, models=["only-model"], context_window=None))
    config = FactoryConfig(backend={"model": "gone-model"})
    scaffold._probe_backend(config, msgs.append)

    assert config.backend.model == "only-model"
    assert any("is not served" in m for m in msgs)
    assert any("sfactory backend --model" in m for m in msgs)

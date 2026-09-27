"""Scaffold end-to-end into a temp project (offline; backend probe falls back)."""

from __future__ import annotations

import json

from pisoftwarefactory.config import load_config
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

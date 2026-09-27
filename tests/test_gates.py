"""Gate runner semantics: skip-with-evidence, truncation, pass logic (ADR-0013)."""

from __future__ import annotations

from pathlib import Path

from pisoftwarefactory import gates as gates_mod
from pisoftwarefactory.config import FactoryConfig
from pisoftwarefactory.gate_profiles import PROFILES


def test_truncate_output_head_tail():
    text = "\n".join(f"line{i}" for i in range(200))
    out = gates_mod.truncate_output(text, head=5, tail=5)
    lines = out.splitlines()
    assert len(lines) == 5 + 5 + 1 and "elided" in lines[5]
    assert lines[0] == "line0" and lines[-1] == "line199"


def test_missing_tool_is_skipped_with_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(gates_mod.shutil, "which", lambda name: None)
    result = gates_mod.run_profile(PROFILES["rust"], tmp_path, FactoryConfig())
    assert all(s.status.startswith("skipped:") for s in result.steps)
    assert result.passed  # skips never fail the profile...


def test_failing_step_blocks(tmp_path, monkeypatch):
    class FakeProc:
        returncode = 3
        stdout = "boom"
        stderr = ""

    monkeypatch.setattr(gates_mod.shutil, "which", lambda name: "C:/fake/" + name)
    monkeypatch.setattr(gates_mod.subprocess, "run", lambda *a, **kw: FakeProc())
    result = gates_mod.run_profile(PROFILES["rust"], tmp_path, FactoryConfig())
    assert not result.passed
    assert result.steps[0].status == "failed"


def test_run_gates_persists_evidence(tmp_path):
    (tmp_path / "go.mod").touch()
    cfg = FactoryConfig(languages=["go"], gates={"step_timeout_sec": 600})
    results = gates_mod.run_gates(tmp_path, cfg, only_lang=None)
    # go toolchain may or may not exist here; evidence file must exist either way
    evidence = list((tmp_path / ".factory" / "run").glob("gates-*.json"))
    assert evidence, "evidence JSON must be persisted"
    assert results[0].language == "go"


def test_extra_steps_from_config(tmp_path, monkeypatch):
    calls = []

    def fake_run(argv, cwd, capture_output, text, timeout, encoding, errors):
        calls.append(argv)
        out = type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        return out

    monkeypatch.setattr(gates_mod.shutil, "which", lambda name: "C:/fake/" + name)
    monkeypatch.setattr(gates_mod.subprocess, "run", fake_run)
    cfg = FactoryConfig(gates={"extra": {"go": [["custom", "check"]]}})
    gates_mod.run_profile(PROFILES["go"], tmp_path, cfg)
    assert any(Path(argv[0]).name == "custom" for argv in calls)

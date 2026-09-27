"""Language detection and gate profile conditions (ADR-0013)."""

from __future__ import annotations

from pisoftwarefactory.gate_profiles import PROFILES, detect_languages


def test_detect_all_six(tmp_path):
    for marker in ("pyproject.toml", "package.json", "Cargo.toml", "go.mod", "CMakeLists.txt", "app.csproj"):
        (tmp_path / marker).touch()
    assert sorted(detect_languages(tmp_path)) == ["cpp", "csharp", "go", "js", "python", "rust"]


def test_detect_none(tmp_path):
    assert detect_languages(tmp_path) == []


def test_python_ruff_condition(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
    assert PROFILES["python"].applies_to(tmp_path)


def test_js_profile_has_fallback_install(tmp_path):
    steps = {s.condition: s for s in PROFILES["js"].steps if s.condition}
    assert "lockfile_present" in steps and "lockfile_absent" in steps

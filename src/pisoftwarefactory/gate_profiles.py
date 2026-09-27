"""Per-language gate profiles (ADR-0013) and marker-file language detection.

A :class:`GateStep` whose ``condition`` is not met, or whose tool binary is
missing, is recorded as ``skipped:<reason>`` — never silently passed
("skipped checks looking like passes" is an incident, not a state).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import ALL_LANGUAGES, Language


@dataclass(frozen=True)
class GateStep:
    """One deterministic verification command."""

    name: str
    argv: tuple[str, ...]
    fix_hint: str = ""
    # Known condition keys evaluated by the gate runner; None = always run.
    condition: str | None = None


@dataclass(frozen=True)
class GateProfile:
    """Build/test gate for one language ecosystem."""

    language: Language
    detect_markers: tuple[str, ...]
    steps: tuple[GateStep, ...]

    def applies_to(self, root: Path) -> bool:
        """True when any detect marker exists under *root* (top-level glob)."""
        for marker in self.detect_markers:
            if "*" in marker:
                if any(root.glob(marker)):
                    return True
            elif (root / marker).exists():
                return True
        return False


def _py_condition(key: str, root: Path) -> bool:
    if key == "has_ruff_config":
        pyproject = root / "pyproject.toml"
        if pyproject.is_file() and "[tool.ruff]" in pyproject.read_text(encoding="utf-8", errors="replace"):
            return True
        return (root / "ruff.toml").is_file() or (root / ".ruff.toml").is_file()
    return False


def _js_condition(key: str, root: Path) -> bool:
    if key.startswith("npm_script:"):
        import json

        pkg = root / "package.json"
        if not pkg.is_file():
            return False
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
        except json.JSONDecodeError:
            return False
        return key.split(":", 1)[1] in scripts
    if key == "lockfile_present":
        return any(
            (root / name).is_file()
            for name in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb")
        )
    return False


CONDITION_CHECKERS = {
    "python": _py_condition,
    "js": _js_condition,
}


PROFILES: dict[Language, GateProfile] = {
    "csharp": GateProfile(
        language="csharp",
        detect_markers=("*.csproj", "*.sln"),
        steps=(
            GateStep("dotnet-build", ("dotnet", "build", "-c", "Release"), "Fix compile errors; `dotnet build` output lists them."),
            GateStep("dotnet-test", ("dotnet", "test", "--no-build", "-c", "Release"), "Failing tests are listed with stack traces; fix before re-dispatch."),
        ),
    ),
    "python": GateProfile(
        language="python",
        detect_markers=("pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py"),
        steps=(
            GateStep("uv-sync", ("uv", "sync"), "Dependency resolution failed; fix pyproject constraints."),
            GateStep("ruff-check", ("uv", "run", "ruff", "check", "."), "Run `ruff check --fix` for auto-fixables.", condition="has_ruff_config"),
            GateStep("pytest", ("uv", "run", "pytest", "-x", "-q"), "Failing test output names the module and assertion."),
        ),
    ),
    "js": GateProfile(
        language="js",
        detect_markers=("package.json",),
        steps=(
            GateStep("npm-install", ("npm", "ci"), "package.json and lockfile are out of sync; run `npm install` locally and commit the lockfile.", condition="lockfile_present"),
            GateStep("npm-install-fallback", ("npm", "install"), "npm install failed; fix dependency resolution.", condition="lockfile_absent"),
            GateStep("npm-build", ("npm", "run", "build"), "The build error names the file and TS/bundler diagnostic.", condition="npm_script:build"),
            GateStep("npm-test", ("npm", "test"), "Failing tests are listed by the test runner summary.", condition="npm_script:test"),
        ),
    ),
    "rust": GateProfile(
        language="rust",
        detect_markers=("Cargo.toml",),
        steps=(
            GateStep("cargo-fmt", ("cargo", "fmt", "--check"), "Run `cargo fmt` and commit the formatting."),
            GateStep("cargo-clippy", ("cargo", "clippy", "--", "-D", "warnings"), "Clippy warnings are errors here; fix or allow with justification."),
            GateStep("cargo-test", ("cargo", "test"), "Failing tests print panics with source locations."),
        ),
    ),
    "cpp": GateProfile(
        language="cpp",
        detect_markers=("CMakeLists.txt", "CMakePresets.json"),
        steps=(
            GateStep("cmake-configure", ("cmake", "--preset", "default"), "Configure error; check CMakePresets.json and required deps."),
            GateStep("cmake-build", ("cmake", "--build", "--preset", "default"), "Compiler diagnostics name file/line; fix before re-dispatch."),
            GateStep("ctest", ("ctest", "--preset", "default", "--output-on-failure"), "Failing tests are listed by ctest."),
        ),
    ),
    "go": GateProfile(
        language="go",
        detect_markers=("go.mod",),
        steps=(
            GateStep("go-vet", ("go", "vet", "./..."), "go vet diagnostics name file/line."),
            GateStep("go-build", ("go", "build", "./..."), "Compiler diagnostics name file/line."),
            GateStep("go-test", ("go", "test", "./..."), "Failing tests print with `--- FAIL` markers."),
        ),
    ),
}


def detect_languages(root: Path) -> list[Language]:
    """Detect which languages the project at *root* uses (marker files)."""
    return [lang for lang in ALL_LANGUAGES if PROFILES[lang].applies_to(root)]

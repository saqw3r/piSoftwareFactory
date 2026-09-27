"""``factory.toml`` — the per-project factory configuration model.

Schema accepted by ADR-0012 (scaffold file set) and ADR-0010 (model
backend). Everything has a local-first default so an untouched config
targets the owner's llama.cpp at 127.0.0.1:8080.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal

import tomli_w
from pydantic import BaseModel, Field

Language = Literal["csharp", "python", "js", "rust", "cpp", "go"]

ALL_LANGUAGES: tuple[Language, ...] = ("csharp", "python", "js", "rust", "cpp", "go")

FACTORY_TOML = "factory.toml"


class Backend(BaseModel):
    """The single LLM backend every factory component talks to (ADR-0010)."""

    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = "qwen3.5-9b"
    context_window: int = 65536


class Harness(BaseModel):
    """Worker/reviewer harness settings (ADR-0002: pi coding agent)."""

    name: Literal["pi"] = "pi"
    worker_mode: Literal["json", "print"] = "json"
    review_mode: Literal["print", "json"] = "print"
    worker_timeout_min: int = 40
    max_retries: int = 2
    worker_tools: list[str] = Field(
        default_factory=lambda: ["read", "edit", "write", "bash", "grep", "find", "ls"]
    )
    reviewer_tools: list[str] = Field(default_factory=lambda: ["read", "grep", "find", "ls"])


class Intake(BaseModel):
    """Intake provider selection (ADR-0004: Jev optional, heuristic fallback)."""

    provider: Literal["auto", "jev", "heuristic"] = "auto"


class Lanes(BaseModel):
    """Worktree lanes for parallel waves (ADR-0007)."""

    count: int = Field(default=2, ge=1, le=8)
    base_ref: str = "main"


class Memory(BaseModel):
    """Hindsight memory integration (ADR-0005)."""

    bank_id: str = ""  # empty → derived from the repo directory name
    hindsight_url: str = "http://127.0.0.1:8888"
    recall_top_k: int = Field(default=5, ge=0, le=25)


class Release(BaseModel):
    """Release branches (ADR-0007: stage accumulates, main moves only via human merge)."""

    stage_branch: str = "stage"
    main_branch: str = "main"


class Gates(BaseModel):
    """Deterministic gate runner settings (ADR-0013)."""

    step_timeout_sec: int = Field(default=600, ge=10)
    # Projects may append extra commands per language, e.g. [gates.extra] rust = [...]
    extra: dict[str, list[str]] = Field(default_factory=dict)


class FactoryConfig(BaseModel):
    """Root of ``factory.toml``."""

    schema_version: int = 1
    auto: bool = False
    languages: list[Language] = Field(default_factory=list)
    backend: Backend = Field(default_factory=Backend)
    harness: Harness = Field(default_factory=Harness)
    intake: Intake = Field(default_factory=Intake)
    lanes: Lanes = Field(default_factory=Lanes)
    memory: Memory = Field(default_factory=Memory)
    release: Release = Field(default_factory=Release)
    gates: Gates = Field(default_factory=Gates)


def load_config(root: Path) -> FactoryConfig:
    """Load ``factory.toml`` from *root* (the factory project directory)."""
    path = root / FACTORY_TOML
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found — run `sfactory init` in the project first."
        )
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return FactoryConfig.model_validate(raw)


def save_config(config: FactoryConfig, root: Path) -> Path:
    """Write ``factory.toml`` into *root*; returns the path written."""
    path = root / FACTORY_TOML
    data: dict[str, Any] = config.model_dump(mode="json", exclude_none=True)
    path.write_bytes(tomli_w.dumps(data).encode("utf-8"))
    return path


def bank_id_for(root: Path, config: FactoryConfig) -> str:
    """Resolve the Hindsight bank id: config value or the repo directory name."""
    return config.memory.bank_id or root.resolve().name.lower().replace(" ", "-")

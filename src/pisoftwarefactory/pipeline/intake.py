"""Intake (ADR-0004): task → typed IntakeDecision.

Jev is advisory: when ``TYPESAFE_API_KEY`` is set and the ``jev`` package is
importable it classifies with confidence + abstention; otherwise (or when it
abstains) the deterministic heuristic classifier decides. The pipeline never
blocks on Jev.
"""

from __future__ import annotations

import os
import re

from pydantic import BaseModel, Field

from ..config import FactoryConfig, Language

_HIGH_RISK_WORDS = re.compile(
    r"\b(security|auth|password|secret|token|payment|migration|schema|database|deploy|delete|encrypt)\b",
    re.IGNORECASE,
)
_BUG_WORDS = re.compile(r"\b(bug|fix|error|crash|regression|failing|broken|hotfix)\b", re.IGNORECASE)
_DOCS_WORDS = re.compile(r"\b(doc|docs|documentation|readme|comment|changelog)\b", re.IGNORECASE)
_CHORE_WORDS = re.compile(r"\b(chore|refactor|upgrade|bump|dependency|cleanup|rename|ci|build system)\b", re.IGNORECASE)
_EXT_HINTS = {
    "cs": "csharp",
    "py": "python",
    "js": "js",
    "ts": "js",
    "rs": "rust",
    "cpp": "cpp",
    "cc": "cpp",
    "h": "cpp",
    "hpp": "cpp",
    "go": "go",
}


class IntakeDecision(BaseModel):
    kind: str = "feature"
    risk: str = "standard"
    language: str = ""
    lane: str = "L1"
    size: str = "medium"
    blockers: list[str] = Field(default_factory=list)
    provider: str = "heuristic"
    confidence: float | None = None
    reason: str = ""


try:  # optional dependency (ADR-0014 extras); absence is the normal offline path
    import jev  # type: ignore[import-not-found]

    _HAS_JEV = True
except ImportError:  # pragma: no cover
    jev = None
    _HAS_JEV = False


def _jev_available() -> bool:
    return _HAS_JEV and bool(os.environ.get("TYPESAFE_API_KEY"))


if _HAS_JEV:  # pragma: no cover — exercised only with the [jev] extra + API key

    @jev.fn
    def _jev_classify(task: str) -> IntakeDecision:
        """Classify a factory task: kind (feature|bug|chore|docs), risk
        (low|standard|high), size (small|medium|large). Abstain when unsure."""


_LANG_BY_WORD = {
    "python": "python",
    "rust": "rust",
    "golang": "go",
    "go": "go",
    "csharp": "csharp",
    "typescript": "js",
    "javascript": "js",
    "cpp": "cpp",
}


def heuristic_intake(task: str, config: FactoryConfig) -> IntakeDecision:
    """Deterministic keyword/path classifier — the offline default."""
    kind = "feature"
    if _BUG_WORDS.search(task):
        kind = "bug"
    elif _DOCS_WORDS.search(task):
        kind = "docs"
    elif _CHORE_WORDS.search(task):
        kind = "chore"

    risk = "high" if _HIGH_RISK_WORDS.search(task) else "standard"

    language = ""
    ext_match = re.search(r"\.([A-Za-z]{1,4})\b", task)  # file mentions: "main.rs"
    if ext_match and ext_match.group(1).lower() in _EXT_HINTS:
        language = _EXT_HINTS[ext_match.group(1).lower()]
    else:
        for word in re.findall(r"[a-z]+", task.lower()):
            if word in _LANG_BY_WORD:
                language = _LANG_BY_WORD[word]
                break
    if not language and config.languages:
        language = config.languages[0]

    words = len(task.split())
    size = "small" if words < 12 else "medium" if words < 60 else "large"

    return IntakeDecision(
        kind=kind,
        risk=risk,
        language=language,
        size=size,
        provider="heuristic",
        reason="keyword/path heuristic (offline default per ADR-0004)",
    )


def run_intake(task: str, config: FactoryConfig) -> IntakeDecision:
    """Jev when available and configured, heuristic otherwise; abstention → heuristic."""
    if config.intake.provider in ("auto", "jev") and _jev_available():  # pragma: no cover
        try:
            decision = _jev_classify(task)
            if getattr(decision, "abstained", False) or decision is None:
                return heuristic_intake(task, config)
            decision.provider = "jev"
            return decision
        except Exception:
            pass  # advisory only — never block intake on the external API
    return heuristic_intake(task, config)

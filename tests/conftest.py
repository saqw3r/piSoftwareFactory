"""Shared fixtures: isolate tests from the real harness agent dirs and network."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_harness_agent_dirs(tmp_path, monkeypatch):
    """Provider registration must never touch the developer's real ~/.pi or ~/.omp."""
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setenv("OMP_CODING_AGENT_DIR", str(tmp_path / "omp-agent"))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("LLAMACPP_API_KEY", raising=False)

"""Shared fixtures: isolate tests from the real pi agent dir and network."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_pi_agent_dir(tmp_path, monkeypatch):
    """_ensure_pi_provider must never touch the developer's real ~/.pi/agent."""
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

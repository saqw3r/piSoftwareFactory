"""`sfactory status` names the brain: agent, endpoint, model, key env — no secret.

`backend`/`doctor` prove the brain works; `status` is the "what is this thing
even using?" moment, and it used to answer with the task graph only.
"""

from __future__ import annotations

import io

from typer.testing import CliRunner

from pisoftwarefactory.cli import app
from pisoftwarefactory.config import FactoryConfig, save_config
from pisoftwarefactory.pipeline.graph import Graph, save_graph


def _project(tmp_path, **backend_updates) -> None:
    save_graph(tmp_path, Graph())
    cfg = FactoryConfig()
    cfg.backend = cfg.backend.model_copy(update=backend_updates)
    save_config(cfg, tmp_path)


def _status(root) -> str:
    """Rich wraps at the runner's 80 columns — collapse it before matching."""
    return " ".join(CliRunner().invoke(app, ["status", "--root", str(root)]).output.split())


def test_status_names_the_brain_and_the_key_env_state(tmp_path, monkeypatch):
    _project(tmp_path, provider="qoder", base_url="http://127.0.0.1:8790/v1",
             model="qwen3-coder-plus", api_key_env="QODER_API_KEY")
    from pisoftwarefactory.config import load_config
    cfg = load_config(tmp_path)
    cfg.harness.name = "omp"
    save_config(cfg, tmp_path)

    monkeypatch.delenv("QODER_API_KEY", raising=False)
    out = _status(tmp_path)
    # Both words now name one thing each (ADR-0022): brain is the agent, provider
    # is the model source. An unlabelled middle word reads as either.
    assert ("brain: omp · provider: qoder · http://127.0.0.1:8790/v1 · qwen3-coder-plus · "
            "key: $QODER_API_KEY (not set)") in out

    monkeypatch.setenv("QODER_API_KEY", "sk-secret-value")
    out = _status(tmp_path)
    assert "(set)" in out
    assert "sk-secret-value" not in out  # the env var is named, never its value


def test_status_says_where_an_agent_credential_backend_gets_its_key(tmp_path):
    _project(tmp_path, agent_credential=True)
    assert "the agent's own login" in _status(tmp_path)


class _FakeTTY:
    """input() falls back to sys.stdin.readline() when stdin is not the real tty."""

    def isatty(self):
        return True

    def readline(self):
        return "\n"


def test_pause_on_failure_holds_interactive_windows(monkeypatch):
    """A transient launcher window vanishes with the failure it printed — hold an
    interactive stdin for Enter. Pipes/scripts never pause; SFACTORY_NO_PAUSE
    opts out even in a terminal."""
    from pisoftwarefactory import cli

    assert cli._maybe_pause_on_failure(0) == 0  # success: never pause
    monkeypatch.delenv("SFACTORY_NO_PAUSE", raising=False)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    assert cli._maybe_pause_on_failure(4) == 4  # piped stdin: exit code is enough
    monkeypatch.setattr(cli.sys, "stdin", _FakeTTY())
    monkeypatch.setenv("SFACTORY_NO_PAUSE", "1")
    assert cli._maybe_pause_on_failure(4) == 4  # scripts opt out
    monkeypatch.delenv("SFACTORY_NO_PAUSE")
    assert cli._maybe_pause_on_failure(4) == 4  # interactive: hold for Enter, keep code

"""gitcmd: the one place the factory talks to git, and why it injects trust.

A lane worktree on a network drive answers `git status` with "detected dubious
ownership" and exit 128. Every caller that read that as data got a lie — an
empty diff, an unchanged lane, a `done` node. So each call trusts only its own
directory, and a refusal raises.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pisoftwarefactory import gitcmd


def _reply(monkeypatch, returncode: int, stdout: str = "", stderr: str = "") -> list[tuple]:
    argvs: list[tuple] = []

    def fake_run(argv, **_kwargs):
        argvs.append(tuple(argv))
        return type("R", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})()

    monkeypatch.setattr(gitcmd.subprocess, "run", fake_run)
    return argvs


def test_each_call_trusts_only_the_directory_it_touches(monkeypatch):
    argvs = _reply(monkeypatch, 0, "ok\n")
    gitcmd.run(Path("Z:/work/proj/.factory/worktrees/L1"), "status", "--porcelain")

    argv = argvs[0]
    assert argv[0] == "git"
    assert argv[1:3] == ("-c", "safe.directory=Z:/work/proj/.factory/worktrees/L1")
    assert "\\" not in argv[2]  # git matches the value literally and prints forward slashes
    assert argv[3:5] == ("-C", "Z:/work/proj/.factory/worktrees/L1")


def test_refusal_raises_with_git_words_and_the_fix(monkeypatch):
    _reply(monkeypatch, 128, stderr="fatal: detected dubious ownership in repository at 'Z:/work'")
    with pytest.raises(gitcmd.GitRefused, match="dubious ownership") as exc:
        gitcmd.run(Path("Z:/work"), "rev-list", "--count", "main..HEAD")

    message = str(exc.value)
    assert "git rev-list --count main..HEAD failed (rc=128)" in message
    assert "safe.directory 'Z:/work'" in message  # the exact command that clears it


def test_check_false_leaves_the_verdict_to_the_caller(monkeypatch):
    _reply(monkeypatch, 1, stderr="fatal: not a valid object name")
    proc = gitcmd.run(Path("Z:/work"), "rev-parse", "--verify", "gone", check=False)
    assert proc.returncode == 1


def test_a_non_refusal_failure_still_names_the_command(monkeypatch):
    _reply(monkeypatch, 128, stderr="fatal: bad object")
    with pytest.raises(gitcmd.GitRefused, match="git log") as exc:
        gitcmd.run(Path("Z:/work"), "log", "--oneline", "-5")
    assert "safe.directory" not in str(exc.value)  # no pointless trust advice

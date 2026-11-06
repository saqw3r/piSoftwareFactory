"""Tests for `sfactory update` source resolution and dispatch decisions."""

from pisoftwarefactory.selfupdate import _local_path, install_source, run_update


def test_local_path_file_url():
    p = _local_path("file:///F:/SoftwareFactory")
    assert p is not None and p.replace("\\", "/").endswith("F:/SoftwareFactory")


def test_local_path_plain_url():
    assert _local_path("https://example.com/pkg") is None


def test_install_source_resolves_for_this_env():
    origin = install_source()
    assert origin["url"]
    assert isinstance(origin["editable"], bool)


def test_run_update_editable_is_noop(monkeypatch, capsys):
    monkeypatch.setattr("pisoftwarefactory.selfupdate.install_source",
                        lambda: {"url": "file:///F:/SoftwareFactory", "editable": True, "version": "0.1.4"})
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv")
    seen: list[str] = []
    assert run_update(log=seen.append) == 0
    assert any("editable" in s for s in seen)


def test_run_update_without_uv_fails(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    seen: list[str] = []
    assert run_update(log=seen.append) == 1
    assert any("uv" in s for s in seen)


def test_run_update_missing_metadata_reports(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv")

    def boom():
        raise RuntimeError("no direct_url.json")

    monkeypatch.setattr("pisoftwarefactory.selfupdate.install_source", boom)
    seen: list[str] = []
    assert run_update(log=seen.append) == 1
    assert any("direct_url" in s for s in seen)


def test_reported_version_is_the_built_version():
    """Live: the release bump went into pyproject and `sfactory --version` kept
    reporting the old number — the operator could not tell which build was running.
    """
    import tomllib
    from pathlib import Path

    import pisoftwarefactory

    with (Path(__file__).resolve().parents[1] / "pyproject.toml").open("rb") as fh:
        built = tomllib.load(fh)["project"]["version"]
    assert built == pisoftwarefactory.__version__


def test_update_stub_answers_during_the_replace_gap(monkeypatch, tmp_path):
    """While uv has the exe removed, a .cmd placeholder says 'update in progress'
    instead of PowerShell's raw 'not recognized' — .exe out-ranks .cmd, so the
    stub only ever speaks during the gap."""
    from pisoftwarefactory import selfupdate

    monkeypatch.setenv("UV_TOOL_BIN_DIR", str(tmp_path))
    stub = selfupdate._write_update_stub("0.2.0")
    assert stub is not None and stub.name == "sfactory.cmd"
    text = stub.read_text(encoding="ascii")
    assert "update in progress" in text and "0.2.0" in text and "exit /b 75" in text
    assert "If this message persists" in text and "--reinstall" in text  # repair hint


def test_scheduled_helper_removes_the_stub_after_reinstall(monkeypatch, tmp_path):
    from pisoftwarefactory import selfupdate

    monkeypatch.setenv("UV_TOOL_BIN_DIR", str(tmp_path))
    stub = selfupdate._write_update_stub("0.2.0")
    captured: dict = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["ps"] = argv[-1]

    monkeypatch.setattr(selfupdate.subprocess, "Popen", FakePopen)
    selfupdate._schedule_after_exit("uv", str(tmp_path / "src"), tmp_path / "log", stub)
    ps = captured["ps"]
    # the shim is removed BEFORE uv starts (a present-but-broken shim answers
    # with a raw ModuleNotFoundError), and the stub is cleaned only on success
    assert ps.index("sfactory.exe") < ps.index("Start-Process")
    assert "--reinstall" in ps and "-PassThru" in ps
    assert "sfactory.cmd" in ps and "ExitCode -eq 0" in ps
    assert "update FAILED" in ps  # failure rewrites the stub with the repair command

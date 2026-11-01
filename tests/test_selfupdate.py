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

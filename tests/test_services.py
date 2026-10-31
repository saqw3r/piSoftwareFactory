"""Tests for service start-up UX: live log streaming, filtered tails."""

from pathlib import Path

from pisoftwarefactory.services import _log_tail, _stream_new, _wait_up


def test_wait_up_streams_new_log_lines(tmp_path: Path):
    logfile = tmp_path / "svc.log"
    logfile.write_text("INFO booting store\n", encoding="utf-8")
    seen: list[str] = []
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        if calls["n"] == 1:
            with logfile.open("a", encoding="utf-8") as fh:
                fh.write("[WORKER_STATS] noise line\n")
                fh.write("INFO Uvicorn running on :8888\n")
            return False
        return True

    assert _wait_up(check, 30, log=seen.append, label="svc", logfile=logfile)
    streamed = [s for s in seen if s.startswith("  │")]
    assert any("Uvicorn" in s for s in streamed)
    assert not any("WORKER_STATS" in s for s in streamed)


def test_wait_up_early_exit_when_process_dies(tmp_path: Path):
    seen: list[str] = []
    assert not _wait_up(lambda: False, 30, log=seen.append, label="svc", died=lambda: True)
    assert any("exited before its port answered" in s for s in seen)


def test_stream_new_advances_offset(tmp_path: Path):
    logfile = tmp_path / "svc.log"
    logfile.write_text("line one\n", encoding="utf-8")
    seen: list[str] = []
    pos = _stream_new(logfile, 0, seen.append)
    assert any("line one" in s for s in seen)
    assert _stream_new(logfile, pos, seen.append) == pos  # nothing new
    assert len(seen) == 1


def test_stream_new_missing_file_is_silent(tmp_path: Path):
    assert _stream_new(tmp_path / "absent.log", 0, lambda *_: None) == 0


def test_log_tail_filters_noise_and_caps_lines(tmp_path: Path):
    logfile = tmp_path / "svc.log"
    body = "".join(f"[WORKER_STATS] idle {i}\nINFO real {i}\n" for i in range(40))
    logfile.write_text(body, encoding="utf-8")
    tail = _log_tail(logfile, n=5)
    assert "WORKER_STATS" not in tail
    assert tail.splitlines() == [f"INFO real {i}" for i in range(35, 40)]


def test_log_tail_missing_file(tmp_path: Path):
    assert _log_tail(tmp_path / "absent.log") == "(no log file)"

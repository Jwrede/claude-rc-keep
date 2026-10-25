import json
import os
import shutil
import subprocess
import sys

import pytest

from claude_rc_keep import core
from claude_rc_keep.cli import main
from claude_rc_keep.core import START_GRACE, Paths, command, load, running, snapshot, tick, web_url


def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_RC_KEEP_CLAUDE", "claude")
    monkeypatch.setenv("CLAUDE_RC_KEEP_ARGS", "--model opus")
    p = Paths.discover()
    p.sessions.mkdir(parents=True)
    (tmp_path / "work").mkdir()
    return p


def add_session(paths, uuid, pid, bridge=None, entrypoint="cli", cwd=None, transcript="x\n"):
    cwd = cwd or str(paths.config.parent / "work")
    d = {"pid": pid, "sessionId": uuid, "cwd": cwd, "entrypoint": entrypoint, "name": uuid[:4]}
    if bridge:
        d["bridgeSessionId"] = bridge
    (paths.sessions / f"{pid}-{uuid}.json").write_text(json.dumps(d))
    proj = paths.projects / "-work"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{uuid}.jsonl").write_text(transcript)


def test_running_ignores_dead_processes_and_prefers_interactive(paths):
    add_session(paths, "aaaa-1", dead_pid(), "session_A")
    add_session(paths, "bbbb-1", os.getpid(), "session_B1", entrypoint="sdk-cli")
    (paths.sessions / "other.json").write_text(
        json.dumps({"pid": os.getppid(), "sessionId": "bbbb-1", "bridgeSessionId": "session_B2", "entrypoint": "cli"})
    )
    live = running(paths)
    assert list(live) == ["bbbb-1"]
    assert live["bbbb-1"]["bridgeSessionId"] == "session_B2"


def test_snapshot_records_web_sessions_and_keeps_newest_per_web_session():
    reg = {"old-uuid": {"bridge": "session_X", "lastSeen": 1}}
    live = {
        "new-uuid": {"bridgeSessionId": "session_X", "cwd": "/w", "entrypoint": "cli"},
        "srv-uuid": {"bridgeSessionId": "session_S", "cwd": "/w", "entrypoint": "sdk-cli"},
        "local-only": {"cwd": "/w", "entrypoint": "cli"},
    }
    snapshot(reg, live, now=100)
    assert set(reg) == {"new-uuid", "srv-uuid"}
    assert reg["new-uuid"]["server"] is False
    assert reg["srv-uuid"]["server"] is True


def test_command_forks_server_sessions_only():
    assert command("u", {"server": False}, "claude", ["-x"]) == ["claude", "-x", "--resume", "u", "--remote-control"]
    assert "--fork-session" in command("u", {"server": True}, "claude", [])


def test_tick_restarts_missing_sessions(paths):
    add_session(paths, "live-1", os.getpid(), "session_L")
    tick(paths, now=1000, launch=lambda *a: None)
    for f in paths.sessions.glob("*.json"):
        f.unlink()
    launched = []
    started = tick(paths, now=2000, launch=lambda name, cwd, argv: launched.append((name, cwd, argv)))
    assert [u for u, _ in started] == ["live-1"]
    assert launched[0][2] == ["claude", "--model", "opus", "--resume", "live-1", "--remote-control"]
    # Within the grace period a started session is not started again.
    assert tick(paths, now=2000 + START_GRACE - 1, launch=lambda *a: pytest.fail("restarted too early")) == []
    assert [u for u, _ in tick(paths, now=2000 + START_GRACE, launch=lambda *a: None)] == ["live-1"]


def test_tick_forks_server_session_once_and_forgets_it(paths):
    add_session(paths, "srv-1", os.getpid(), "session_S", entrypoint="sdk-cli")
    tick(paths, now=1000, launch=lambda *a: None)
    (next(paths.sessions.glob("*.json"))).unlink()
    launched = []
    tick(paths, now=2000, launch=lambda name, cwd, argv: launched.append(argv))
    assert "--fork-session" in launched[0]
    assert load(paths) == {}


def test_tick_skips_empty_transcripts_and_missing_folders(paths):
    add_session(paths, "empty-1", os.getpid(), "session_E", transcript="")
    add_session(paths, "gone-1", os.getpid(), "session_G", cwd=str(paths.config.parent / "deleted"))
    tick(paths, now=1000, launch=lambda *a: None)
    for f in paths.sessions.glob("*.json"):
        f.unlink()
    assert tick(paths, now=2000, launch=lambda *a: pytest.fail("should not start")) == []
    assert set(load(paths)) == {"empty-1", "gone-1"}


@pytest.mark.skipif(not shutil.which("tmux"), reason="needs tmux")
def test_tmux_start_replaces_window_of_same_name(tmp_path, monkeypatch):
    # A restarted session that is still running (e.g. stuck in a dialog) must not pile up.
    monkeypatch.setattr(core, "TMUX_SOCKET", f"rc-keep-test-{os.getpid()}")
    tmux = ["tmux", "-L", core.TMUX_SOCKET]
    try:
        core.tmux_start("other", str(tmp_path), ["sleep", "300"])
        core.tmux_start("abcd1234", str(tmp_path), ["sleep", "300"])
        core.tmux_start("abcd1234", str(tmp_path), ["sleep", "301"])
        out = subprocess.run(
            [*tmux, "list-windows", "-t", core.TMUX_SESSION, "-F", "#{window_name} #{pane_start_command}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        assert sorted(out) == ['abcd1234 "sleep 301"', 'other "sleep 300"']
    finally:
        subprocess.run([*tmux, "kill-server"], check=False, capture_output=True)


def test_rm_stops_and_forgets(paths, capsys):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        add_session(paths, "keep-1", proc.pid, "session_K")
        tick(paths, now=1000, launch=lambda *a: None)
        assert main(["rm", "keep"]) == 0
        assert proc.wait(timeout=10) != 0
        assert load(paths) == {}
        assert main(["rm", "keep"]) == 1
    finally:
        proc.kill()


def test_ls(paths, capsys):
    assert main(["ls"]) == 0
    assert "No sessions recorded" in capsys.readouterr().out
    add_session(paths, "abcd1234-x", os.getpid(), "session_01Q")
    tick(paths, now=1000, launch=lambda *a: None)
    main([])
    out = capsys.readouterr().out
    assert "abcd1234  running" in out and "https://claude.ai/code/session_01Q" in out


def test_web_url_accepts_both_id_forms():
    assert web_url("cse_01Z") == web_url("session_01Z") == "https://claude.ai/code/session_01Z"

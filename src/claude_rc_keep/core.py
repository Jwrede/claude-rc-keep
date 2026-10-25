from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

TMUX_SOCKET = "claude-rc-keep"
TMUX_SESSION = "keep"
START_GRACE = 300  # seconds before a started session counts as failed and is started again


@dataclass
class Paths:
    config: Path
    registry: Path

    @classmethod
    def discover(cls) -> Paths:
        config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
        state = Path(os.environ.get("XDG_STATE_HOME") or "~/.local/state").expanduser()
        return cls(config, state / "claude-rc-keep" / "registry.json")

    @property
    def sessions(self) -> Path:
        return self.config / "sessions"

    @property
    def projects(self) -> Path:
        return self.config / "projects"


def load(paths: Paths) -> dict:
    try:
        return json.loads(paths.registry.read_text())
    except FileNotFoundError:
        return {}


def save(paths: Paths, reg: dict) -> None:
    paths.registry.parent.mkdir(parents=True, exist_ok=True)
    tmp = paths.registry.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=1))
    tmp.replace(paths.registry)


def alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running(paths: Paths) -> dict[str, dict]:
    """Local session id -> Claude Code's session file, for live processes only."""
    out: dict[str, dict] = {}
    for path in sorted(paths.sessions.glob("*.json")):
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not d.get("sessionId") or not alive(d.get("pid")):
            continue
        # A transcript can be open in a server worker and an interactive session at once; prefer the latter.
        if d["sessionId"] not in out or d.get("entrypoint") == "cli":
            out[d["sessionId"]] = d
    return out


def transcript(paths: Paths, uuid: str) -> Path | None:
    for path in paths.projects.glob(f"*/{uuid}.jsonl"):
        if path.stat().st_size > 0:
            return path
    return None


def snapshot(reg: dict, live: dict[str, dict], now: int) -> None:
    """Record every live session that has a web session, then keep one entry per web session."""
    for uuid, d in live.items():
        if not d.get("bridgeSessionId"):
            continue
        e = reg.setdefault(uuid, {})
        e.update(
            cwd=d.get("cwd"),
            name=d.get("name"),
            bridge=d["bridgeSessionId"],
            server=d.get("entrypoint") != "cli",
            lastSeen=now,
        )
    # A resumed session can continue under a new local id; keep the newest entry per web session.
    newest: dict[str, str] = {}
    for uuid, e in reg.items():
        b = e.get("bridge")
        if b not in newest or e.get("lastSeen", 0) > reg[newest[b]].get("lastSeen", 0):
            newest[b] = uuid
    for uuid in [u for u, e in reg.items() if newest.get(e.get("bridge")) != u]:
        del reg[uuid]


def command(uuid: str, entry: dict, claude: str, extra: list[str]) -> list[str]:
    fork = ["--fork-session"] if entry.get("server") else []
    return [claude, *extra, "--resume", uuid, *fork, "--remote-control"]


def tmux_start(name: str, cwd: str, argv: list[str]) -> None:
    tmux = ["tmux", "-L", TMUX_SOCKET]
    # A previous start may still be running without a session file (e.g. stuck in a dialog); replace it.
    windows = subprocess.run(
        [*tmux, "list-windows", "-t", TMUX_SESSION, "-F", "#{window_id} #{window_name}"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout
    for line in windows.splitlines():
        wid, _, wname = line.partition(" ")
        if wname == name:
            subprocess.run([*tmux, "kill-window", "-t", wid], check=False, capture_output=True)
    has = subprocess.run([*tmux, "has-session", "-t", TMUX_SESSION], check=False, capture_output=True).returncode == 0
    target = ["new-window", "-d", "-t", f"{TMUX_SESSION}:"] if has else ["new-session", "-d", "-s", TMUX_SESSION]
    subprocess.run([*tmux, *target, "-n", name, "-c", cwd, shlex.join(argv)], check=True)


def tick(paths: Paths, now: int, launch=tmux_start) -> list[tuple[str, dict]]:
    """Snapshot live sessions and restart registered ones that are not running. Returns what was started."""
    reg = load(paths)
    live = running(paths)
    snapshot(reg, live, now)
    claude = os.environ.get("CLAUDE_RC_KEEP_CLAUDE") or shutil.which("claude") or "claude"
    extra = shlex.split(os.environ.get("CLAUDE_RC_KEEP_ARGS", ""))
    started = []
    for uuid, e in list(reg.items()):
        if uuid in live or now - e.get("lastStart", 0) < START_GRACE:
            continue
        if not transcript(paths, uuid) or not Path(e.get("cwd") or "").is_dir():
            continue
        launch(uuid[:8], e["cwd"], command(uuid, e, claude, extra))
        e["lastStart"] = now
        started.append((uuid, dict(e)))
        if e.get("server"):
            del reg[uuid]  # the fork registers itself under its new id on the next tick
    save(paths, reg)
    return started


def find(reg: dict, key: str) -> list[str]:
    return [u for u, e in reg.items() if u.startswith(key) or key in (e.get("bridge") or "") or key == e.get("name")]


def web_url(bridge: str | None) -> str:
    return "https://claude.ai/code/" + (bridge or "").replace("cse_", "session_")

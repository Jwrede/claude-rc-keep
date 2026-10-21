from __future__ import annotations

import argparse
import os
import signal
import sys
import time

from claude_rc_keep import __version__
from claude_rc_keep.core import Paths, find, load, running, save, tick, web_url


def cmd_tick(args, paths: Paths) -> int:
    for uuid, e in tick(paths, int(time.time())):
        print(f"started {uuid[:8]} in {e['cwd']}" + (" as fork" if e.get("server") else ""))
    return 0


def cmd_ls(args, paths: Paths) -> int:
    reg = load(paths)
    if not reg:
        print(f"No sessions recorded in {paths.registry}")
        return 0
    live = running(paths)
    for uuid, e in sorted(reg.items(), key=lambda kv: -kv[1].get("lastSeen", 0)):
        state = ("running" if uuid in live else "stopped") + (" (server)" if e.get("server") else "")
        seen = time.strftime("%m-%d %H:%M", time.localtime(e.get("lastSeen", 0)))
        print(f"{uuid[:8]}  {state:16}  {seen}  {e.get('cwd')}  {web_url(e.get('bridge'))}")
    return 0


def cmd_rm(args, paths: Paths) -> int:
    reg = load(paths)
    hits = find(reg, args.id)
    if len(hits) != 1:
        print(f"{len(hits)} sessions match {args.id!r}, need exactly one.")
        return 1
    uuid = hits[0]
    proc = running(paths).get(uuid)
    del reg[uuid]
    save(paths, reg)
    if proc:
        os.kill(proc["pid"], signal.SIGTERM)
    print(f"removed {uuid[:8]}" + (f", stopped pid {proc['pid']}" if proc else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="claude-rc-keep",
        description="Keep Claude Code Remote Control sessions online across restarts and reboots.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("tick", help="record running sessions and restart missing ones (run every minute)")
    sub.add_parser("ls", help="list kept sessions (default)")
    rm = sub.add_parser("rm", help="stop a session and stop keeping it")
    rm.add_argument("id", help="local session id prefix, web session id or name")
    args = parser.parse_args(argv)
    handler = {"tick": cmd_tick, "rm": cmd_rm}.get(args.cmd, cmd_ls)
    return handler(args, Paths.discover())


if __name__ == "__main__":
    sys.exit(main())

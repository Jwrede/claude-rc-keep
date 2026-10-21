# claude-rc-keep

Claude Code Remote Control lets you continue a session on a server from claude.ai/code or the
Claude mobile app. When the server reboots, or the session process is killed, the web session
shows "Can't reach <machine>. It may be asleep or offline." and stays that way. `claude
remote-control` comes back as a systemd service, but it does not reliably re-adopt the sessions
that were running before.

`claude-rc-keep` keeps every Remote Control session online until you stop it:

- every minute it records the running sessions that have a web session (local session id,
  folder, web URL)
- sessions that are no longer running are restarted as `claude --resume <id> --remote-control`
  in a detached tmux, in their original folder
- a restarted interactive session reattaches to the **same web session**, so the open tab on
  your phone or browser reconnects with the full conversation

## Why

| Issue | State | Reactions |
|---|---|---|
| [claude-code#30447](https://github.com/anthropics/claude-code/issues/30447) `claude remote-control --headless`, daemonizable remote control without TTY dependency | open | 48 |
| [claude-code#33041](https://github.com/anthropics/claude-code/issues/33041) Remote Control disconnects frequently | open | 30 |
| [claude-code#98310](https://github.com/anthropics/claude-code/issues/98310) Unarchived server-mode sessions are never re-dispatched to the running host | open | 3 |
| [claude-code#98504](https://github.com/anthropics/claude-code/issues/98504) Remote Control doesn't survive app restarts or session eviction | open | 2 |
| [claude-code#101190](https://github.com/anthropics/claude-code/issues/101190) Session shows "environment was deleted" after server exit; no way to re-attach | open | 2 |
| [claude-code#91592](https://github.com/anthropics/claude-code/issues/91592) Remote Control sessions require PC-side input to reconnect after restart | open | 1 |
| [claude-code#94049](https://github.com/anthropics/claude-code/issues/94049) Auto-update silently drops all Remote Control bridges | open | 0 |

(Reaction counts as of 2026-10-21.)

## Install

```
pipx install git+https://github.com/Jwrede/claude-rc-keep
```

Python 3.10+, no dependencies. Needs Linux with systemd user services and tmux. Tested with
Claude Code 2.1.306.

Install the units and start the timer:

```
mkdir -p ~/.config/systemd/user
curl -fsSL -o ~/.config/systemd/user/claude-rc-keep.service https://raw.githubusercontent.com/Jwrede/claude-rc-keep/main/systemd/claude-rc-keep.service
curl -fsSL -o ~/.config/systemd/user/claude-rc-keep.timer https://raw.githubusercontent.com/Jwrede/claude-rc-keep/main/systemd/claude-rc-keep.timer
curl -fsSL -o ~/.config/systemd/user/claude-rc-keep-tmux.service https://raw.githubusercontent.com/Jwrede/claude-rc-keep/main/systemd/claude-rc-keep-tmux.service
systemctl --user daemon-reload
systemctl --user enable --now claude-rc-keep.timer
sudo loginctl enable-linger "$USER"   # start user services at boot without a login
```

## Usage

Start sessions as usual: `claude remote-control` (server mode), `claude --remote-control`, or
`/remote-control` inside a session. Within a minute they are recorded.

```
$ claude-rc-keep ls
4f1c09ab  running           10-21 16:42  /home/dev/game  https://claude.ai/code/session_01Hq7Tz3VkM2pXa9LwR4cNe8
9b72d4e0  stopped           10-21 16:42  /home/dev/game  https://claude.ai/code/session_01Bv6sYc8KdJ3nQe5FtU2gWm
c35e8a17  running (server)  10-21 16:42  /home/dev/game  https://claude.ai/code/session_01Ra4Ln9XpE7cWb2Mk6TsJy3
```

Stop a session for good (local id prefix, web session id or name):

```
$ claude-rc-keep rm 9b72
removed 9b72d4e0, stopped pid 101553
```

The restarted sessions run in `tmux -L claude-rc-keep attach`. `claude-rc-keep tick` runs one
round by hand. Set `CLAUDE_RC_KEEP_ARGS` in the service to pass extra arguments to every
restarted session, and `CLAUDE_CONFIG_DIR` to use another Claude Code profile.

## How it works and limits

Claude Code writes `~/.claude/sessions/<pid>.json` for every running session, with the local
session id, the folder and, when Remote Control is on, the web session id
(`bridgeSessionId`). The file is deleted when the process ends, so `claude-rc-keep` copies the
entries to `~/.local/state/claude-rc-keep/registry.json`.

Interactive sessions (`claude --remote-control`, `/remote-control`) store their web session
with the transcript and reattach to it on `claude --resume <id> --remote-control`, even after
the web session was archived on exit.

Sessions spawned by the `claude remote-control` server cannot be restored the same way:

- `claude remote-control --session-id <id>` reattaches one of them, but only in single-session
  mode and only while no other `claude remote-control` serves the machine.
- After a restart the server sometimes re-adopts an old web session on its own. Its worker
  downloads the conversation from the server and overwrites the local transcript with it
  (observed: 77 MB down to 14 MB, everything before the last `/compact` gone from the file).

`claude-rc-keep` therefore restores a server session once with `--fork-session`: a copy with a
new local id and a new web URL. The conversation is complete, and the original transcript is
left alone if the server re-adopts the old web session. From then on the copy is an interactive
session and keeps its URL. The old tab stays offline, or shows the server's copy if it was
re-adopted; use the new one.

- The session files are undocumented and may change with any Claude Code release.
- Each restarted session is a full Claude Code process (about 270 MB of memory).
- A session you end with `/exit` in its tmux window is restarted within a minute. Use
  `claude-rc-keep rm` to stop one.

## License

MIT

# jcrefresher

A background daemon that keeps [jcodemunch](https://github.com/jcodemunch/jcodemunch-mcp) indexes up to date as you edit code.

## Why it exists

jcodemunch indexes repositories on demand but does not watch for file changes — you have to trigger a reindex manually, or the index goes stale as you work. jcrefresher fills that gap: it watches every repo that jcodemunch knows about and re-indexes changed files automatically.

## How it works

1. **Discovery** — On startup and every 30 seconds, jcrefresher scans `~/.code-index/*.db`. Each `.db` file is a jcodemunch SQLite database. It reads the `source_root` key from the `meta` table to find the repo path.

2. **Watching** — Each discovered repo is watched recursively using [watchdog](https://github.com/gorakhargosh/watchdog), which uses inotify on Linux. Transient files (vim swap files, `node_modules`, `.git`, etc.) are filtered out before any event is processed.

3. **Debouncing** — Rapid bursts of events (e.g. a save + auto-format) are coalesced: a 2-second quiet window must pass before an event is dispatched. A directory-level event always escalates to a full-folder reindex regardless of other events in the same window.

4. **Indexing** — File changes call `uvx jcodemunch-mcp index-file <path>`. Directory changes and deletions call `uvx jcodemunch-mcp index <repo_root>`. Up to 4 workers run in parallel; a per-target lock prevents concurrent jcodemunch calls on the same path.

## Requirements

- Ubuntu (or any Linux with inotify)
- Python 3.12+
- `uvx` installed and on `PATH` (comes with [uv](https://github.com/astral-sh/uv))
- `jcodemunch-mcp` resolvable via uvx (i.e. `uvx jcodemunch-mcp --help` works)

## Installation

```bash
./install.sh
```

This creates a virtualenv at `~/.local/share/jcrefresher/venv`, installs dependencies, deploys the systemd user service, and enables it to start on login.

## Usage

The daemon starts automatically at login once installed. You do not need to interact with it under normal operation.

**Troubleshooting:**

```bash
# Follow live logs
journalctl --user -u jcrefresher -f

# Verbose output (INFO level)
python3 -m jcrefresher --verbose

# Full debug output
python3 -m jcrefresher --debug
```

**Service control:**

```bash
systemctl --user status jcrefresher
systemctl --user restart jcrefresher
systemctl --user stop jcrefresher
```

## Uninstall

```bash
./uninstall.sh
```

Stops and disables the service, removes the systemd unit file, and deletes the virtualenv.

## inotify watch limit

If you have many repos with many files, the kernel's default inotify watch limit may be exhausted. Symptoms: watchdog logs `inotify watch limit reached` or new watches silently fail.

Increase the limit permanently:

```bash
echo fs.inotify.max_user_watches=524288 | sudo tee /etc/sysctl.d/40-inotify.conf
sudo sysctl --system
```

# jcrefresher

A background daemon that keeps [jcodemunch](https://github.com/jcodemunch/jcodemunch-mcp) indexes up to date as you edit code.

## Why it exists

jcodemunch indexes repositories on demand but does not watch for file changes — you have to trigger a reindex manually, or the index goes stale as you work. jcrefresher fills that gap: it watches every repo that jcodemunch knows about and re-indexes changed files automatically.

## How it works

1. **Discovery** — On startup and every 30 seconds, jcrefresher scans `~/.code-index/*.db`. Each `.db` file is a jcodemunch SQLite database. It reads the `source_root` key from the `meta` table to find the repo path.

2. **Watching** — Each discovered repo is watched recursively using [watchdog](https://github.com/gorakhargosh/watchdog), which uses inotify on Linux and ReadDirectoryChangesW on Windows. Transient files (vim swap files, `node_modules`, `.git`, etc.) are filtered out before any event is processed.

3. **Debouncing** — Rapid bursts of events (e.g. a save + auto-format) are coalesced: a 2-second quiet window must pass before an event is dispatched. A directory-level event always escalates to a full-folder reindex regardless of other events in the same window.

4. **Indexing** — File changes call `uvx jcodemunch-mcp index-file <path>`. Directory changes and deletions call `uvx jcodemunch-mcp index <repo_root>`. Up to 4 workers run in parallel; a per-target lock prevents concurrent jcodemunch calls on the same path.

## Requirements

- **Linux:** Ubuntu or any Linux with inotify, Python 3.12+, `uvx` on `PATH`
- **Windows:** Windows 10 or 11, winget (pre-installed on modern Windows — verify with `winget --version`)

In both cases, `jcodemunch-mcp` must be resolvable via uvx (i.e. `uvx jcodemunch-mcp --help` works). The Windows installer handles Python and uv automatically if they are missing.

---

## Linux

### Installation

```bash
./install.sh
```

Creates a virtualenv at `~/.local/share/jcrefresher/venv`, installs dependencies, deploys the systemd user service, and enables it to start on login.

### Usage

The daemon starts automatically at login. You do not need to interact with it under normal operation.

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

### Uninstall

```bash
./uninstall.sh
```

Stops and disables the service, removes the systemd unit file, and deletes the virtualenv.

### inotify watch limit

If you have many repos with many files, the kernel's default inotify watch limit may be exhausted. Symptoms: watchdog logs `inotify watch limit reached` or new watches silently fail.

Increase the limit permanently:

```bash
echo fs.inotify.max_user_watches=524288 | sudo tee /etc/sysctl.d/40-inotify.conf
sudo sysctl --system
```

---

## Windows

### Installation

Open the `jcrefresher` folder in File Explorer, right-click `install.ps1`, and choose **Run with PowerShell**. If prompted by UAC click **Yes**; if you see a "Windows protected your PC" screen click **More info** then **Run anyway**.

Or in PowerShell:

```powershell
.\install.ps1
```

The script installs Python 3 and uv if missing, locates your jcodemunch index folder, creates a virtualenv, and registers a Task Scheduler task called `jcrefresher` that starts automatically at login. It then starts the task immediately.

**If `.code-index` is not found:** jcrefresher checks `%USERPROFILE%\.code-index`, `%LOCALAPPDATA%\.code-index`, and `%APPDATA%\.code-index`. If none exist, the installer pauses and displays a diagnostic snippet — copy it and paste it to Claude Code to locate the right folder, then paste the path back into the installer prompt.

### Usage

The task starts automatically at login. To check its status:

```powershell
Get-ScheduledTask -TaskName jcrefresher
```

The `State` column should show `Running` or `Ready`. To see the last run time and result:

```powershell
Get-ScheduledTaskInfo -TaskName jcrefresher
```

You can also open **Task Scheduler** from the Start menu and find `jcrefresher` under **Task Scheduler Library**.

### Uninstall

Right-click `uninstall.ps1` in File Explorer and choose **Run with PowerShell**, or:

```powershell
.\uninstall.ps1
```

Stops the task, removes it from Task Scheduler, and deletes the virtualenv.

# jcrefresher Implementation Spec

## Overview

jcrefresher is a Python 3 daemon that watches jcodemunch-indexed source roots and re-invokes the jcodemunch CLI on file changes. It runs as a systemd user service.

---

## File Structure

```
/home/stark-botha/dev/fsystems/projects/jcrefresher/
├── jcrefresher/
│   ├── __init__.py
│   ├── __main__.py
│   ├── discovery.py
│   ├── watcher.py
│   ├── debounce.py
│   ├── dispatcher.py
│   ├── worker.py
│   └── filters.py
├── install.sh
├── uninstall.sh
└── jcrefresher.service
```

---

## Module Specs

### `jcrefresher/__init__.py`

**File path:** `jcrefresher/__init__.py`
**Purpose:** Package marker; exposes package version constant.
**Dependencies:** None.
**Public interface:**
```python
__version__: str  # e.g. "0.1.0"
```
**Behaviour:** Empty except for `__version__ = "0.1.0"`.

---

### `jcrefresher/__main__.py`

**File path:** `jcrefresher/__main__.py`
**Purpose:** Entry point; wires all components together and runs the daemon until a termination signal is received.

**Dependencies:**
- `signal` (stdlib)
- `logging` (stdlib)
- `threading` (stdlib)
- `jcrefresher.discovery`
- `jcrefresher.watcher`
- `jcrefresher.worker`

**Public interface:**
```python
def main() -> None: ...
```

**Behaviour — `main()`:**
1. Configure logging: call `logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)`. This routes to stderr so journald captures it via the systemd unit's `StandardOutput=journal` / `StandardError=journal`.
2. Instantiate `worker.WorkerPool` with a `max_workers` of 4.
3. Start the worker pool (calls `WorkerPool.start()`).
4. Instantiate `watcher.WatchManager`, passing the worker pool.
5. Call `watcher.WatchManager.start()` — this performs initial discovery and starts the watchdog observer and rediscovery timer.
6. Install `signal.SIGTERM` and `signal.SIGINT` handlers that call `_shutdown(watcher_manager, worker_pool)`.
7. Block the main thread with `signal.pause()` (loop forever until signal received).
8. Do NOT implement: any indexing logic; log file management.

**Behaviour — `_shutdown(watcher_manager, worker_pool)` (module-private):**
1. Log `INFO: Shutdown signal received`.
2. Call `watcher_manager.stop()`.
3. Call `worker_pool.stop()` (drains queue then exits).
4. Call `sys.exit(0)`.

---

### `jcrefresher/discovery.py`

**File path:** `jcrefresher/discovery.py`
**Purpose:** Reads `~/.code-index/` to discover all indexed repos and their source roots.

**Dependencies:**
- `pathlib` (stdlib)
- `sqlite3` (stdlib)
- `logging` (stdlib)

**Constants:**
```python
INDEX_DIR: Path = Path.home() / ".code-index"
```

**Data contracts:**
```python
# Returned by discover_repos()
@dataclass
class RepoRecord:
    db_path: Path        # absolute path to the .db file
    source_root: Path    # absolute path to the watched source directory
```

**Public interface:**
```python
def discover_repos() -> list[RepoRecord]: ...
```

**Behaviour — `discover_repos()`:**
1. If `INDEX_DIR` does not exist, log a `WARNING` and return `[]`.
2. Iterate over all entries in `INDEX_DIR` using `INDEX_DIR.iterdir()`.
3. For each entry: skip if it is not a file or does not have a `.db` suffix.
4. For each qualifying `.db` file, call `_read_source_root(db_path)`.
   - If that returns `None`, log `WARNING: skipping <db_path>: could not read source_root` and continue.
5. If the returned `source_root` path does not exist on disk (`not source_root.exists()`), log `WARNING: source root <source_root> for <db_path> does not exist on disk; skipping` and continue.
6. Resolve symlinks on the source root path using `source_root.resolve()` (follow link target). Use the resolved path going forward.
7. Append a `RepoRecord(db_path=db_path, source_root=resolved_source_root)` to the result list.
8. Return the list.

**Behaviour — `_read_source_root(db_path: Path) -> Path | None` (module-private):**
1. Open a read-only SQLite connection: `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)`.
2. Execute `SELECT value FROM meta WHERE key='source_root'`.
3. Fetch one row. If no row, close connection, return `None`.
4. Strip whitespace from the value. If empty, return `None`.
5. Return `Path(value)`.
6. On any `sqlite3.DatabaseError`, log `WARNING: <db_path> is not a valid SQLite db: <err>` and return `None`.
7. Always close the connection in a `finally` block.

---

### `jcrefresher/filters.py`

**File path:** `jcrefresher/filters.py`
**Purpose:** Determines whether a filesystem path should be ignored before entering the debounce layer.

**Dependencies:**
- `pathlib` (stdlib)

**Constants:**
```python
IGNORED_DIR_NAMES: frozenset[str] = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", ".mypy_cache",
    ".tox", ".eggs", "dist", "build", ".hg", ".svn",
})

IGNORED_SUFFIXES: frozenset[str] = frozenset({
    ".swp", ".swo", ".swn",   # vim swap
    ".tmp", ".temp",
    "~",                       # editor backup (trailing tilde)
})
```

**Public interface:**
```python
def should_ignore(path: str | Path) -> bool: ...
```

**Behaviour — `should_ignore(path)`:**
1. Convert `path` to a `pathlib.Path`.
2. Check every component of the path's parts. If any component is in `IGNORED_DIR_NAMES`, return `True`.
3. Check if the file's suffix (i.e. `path.suffix`) is in `IGNORED_SUFFIXES`, or if the name ends with `~`. If so, return `True`.
4. Check if the file name starts with `.` and has no other extension (hidden file with no suffix, e.g. `.DS_Store`). Decision: do NOT ignore hidden files by default — only the listed directory names and suffixes are ignored. This avoids suppressing legitimate dotfiles like `.env`.
5. Return `False` otherwise.

---

### `jcrefresher/debounce.py`

**File path:** `jcrefresher/debounce.py`
**Purpose:** Coalesces rapid filesystem events per path into a single deferred callback after a quiet period.

**Dependencies:**
- `threading` (stdlib)
- `logging` (stdlib)
- `typing` (stdlib)

**Data contracts:**
```python
# Type alias for the callback the debouncer fires
DebounceCallback = Callable[[str, str], None]
# Parameters: (abs_path: str, event_type: str)
# event_type is one of: "file_modify", "file_create", "file_delete", "dir_event"
```

**Public interface:**
```python
class Debouncer:
    def __init__(self, window_seconds: float, callback: DebounceCallback) -> None: ...
    def push(self, path: str, event_type: str) -> None: ...
    def flush_all(self) -> None: ...
    def shutdown(self) -> None: ...
```

**Behaviour — `Debouncer.__init__`:**
1. Store `window_seconds` and `callback`.
2. Create `_timers: dict[str, threading.Timer]` (keyed by abs path).
3. Create `_lock: threading.Lock` protecting `_timers`.
4. Create `_event_types: dict[str, str]` (keyed by abs path) — stores the most recent event type for each pending path.
5. Set `_shutdown_flag = False`.

**Behaviour — `Debouncer.push(path, event_type)`:**
1. Acquire `_lock`.
2. If `_shutdown_flag` is `True`, release lock and return immediately.
3. If a timer already exists for `path`, cancel it.
4. Update `_event_types[path]` with `event_type`. Priority rule: if existing type is `"dir_event"`, keep `"dir_event"` regardless of new type (directory-level events escalate and stay escalated for the window).
5. Create a new `threading.Timer(window_seconds, self._fire, args=[path])` and store it in `_timers[path]`. Start the timer.
6. Release `_lock`.

**Behaviour — `Debouncer._fire(path)` (private):**
1. Acquire `_lock`.
2. Remove `path` from `_timers` and `_event_types`, capturing the event type.
3. Release `_lock`.
4. Call `self.callback(path, event_type)`.

**Behaviour — `Debouncer.flush_all()`:**
1. Acquire `_lock`. Cancel all pending timers. Snapshot `_event_types`. Clear `_timers` and `_event_types`. Release `_lock`.
2. For each `(path, event_type)` in the snapshot, call `self.callback(path, event_type)`.
3. Purpose: used during shutdown to ensure no pending events are dropped.

**Behaviour — `Debouncer.shutdown()`:**
1. Acquire `_lock`. Set `_shutdown_flag = True`. Cancel all pending timers. Clear both dicts. Release `_lock`.
2. Do NOT fire callbacks — events are discarded on shutdown.

---

### `jcrefresher/dispatcher.py`

**File path:** `jcrefresher/dispatcher.py`
**Purpose:** Classifies a debounced event as either a single-file or folder-level reindex job and enqueues it.

**Dependencies:**
- `pathlib` (stdlib)
- `logging` (stdlib)
- `jcrefresher.worker` (for `Job`, `JobKind`, `WorkerPool`)

**Data contracts — see `worker.py`:**
```python
# Re-exported from worker for callers
from jcrefresher.worker import Job, JobKind
```

**Public interface:**
```python
class Dispatcher:
    def __init__(self, pool: "WorkerPool") -> None: ...
    def dispatch(self, path: str, event_type: str, source_root: str) -> None: ...
```

**Behaviour — `Dispatcher.dispatch(path, event_type, source_root)`:**
1. If `event_type == "dir_event"` OR `event_type == "file_delete"`:
   - Enqueue a `Job(kind=JobKind.FOLDER, target=source_root)` via `self._pool.enqueue(job)`.
   - Log `DEBUG: folder reindex enqueued for <source_root> (trigger: <path>, type: <event_type>)`.
2. Otherwise (`"file_modify"` or `"file_create"`):
   - Enqueue a `Job(kind=JobKind.FILE, target=path)` via `self._pool.enqueue(job)`.
   - Log `DEBUG: file reindex enqueued for <path>`.
3. No return value.

**Design note:** Dispatcher does not check whether the path still exists — that is the worker's responsibility.

---

### `jcrefresher/worker.py`

**File path:** `jcrefresher/worker.py`
**Purpose:** Manages a pool of worker threads that pull reindex jobs from a queue and invoke the jcodemunch CLI.

**Dependencies:**
- `queue` (stdlib)
- `threading` (stdlib)
- `subprocess` (stdlib)
- `logging` (stdlib)
- `enum` (stdlib)
- `dataclasses` (stdlib)

**Data contracts:**
```python
from enum import Enum, auto
from dataclasses import dataclass

class JobKind(Enum):
    FILE = auto()    # invoke: uvx jcodemunch-mcp index-file <target>
    FOLDER = auto()  # invoke: uvx jcodemunch-mcp index <target>

@dataclass
class Job:
    kind: JobKind
    target: str   # absolute path string
```

**Constants:**
```python
JCODEMUNCH_CMD: str = "uvx"
JCODEMUNCH_ARGS_FILE: list[str] = ["jcodemunch-mcp", "index-file"]
JCODEMUNCH_ARGS_FOLDER: list[str] = ["jcodemunch-mcp", "index"]
SUBPROCESS_TIMEOUT_SECONDS: int = 300  # 5 minutes; avoids hung subprocesses
```

**Public interface:**
```python
class WorkerPool:
    def __init__(self, max_workers: int = 4) -> None: ...
    def start(self) -> None: ...
    def enqueue(self, job: Job) -> None: ...
    def stop(self) -> None: ...
```

**Behaviour — `WorkerPool.__init__`:**
1. Store `max_workers`.
2. Create `_queue: queue.Queue[Job | None]` (sentinel `None` signals worker exit).
3. Create `_threads: list[threading.Thread] = []`.
4. Create `_per_file_locks: dict[str, threading.Lock]` protected by `_meta_lock: threading.Lock`.
   - Purpose: serialise reindex invocations per-file; allow parallel invocations across different files/repos.
5. Set `_running = False`.

**Behaviour — `WorkerPool.start()`:**
1. Set `_running = True`.
2. Spawn `max_workers` daemon threads, each running `_worker_loop`. Append to `_threads`. Start each.

**Behaviour — `WorkerPool.enqueue(job)`:**
1. If `not _running`, log `WARNING: pool not running; job dropped` and return.
2. Put `job` onto `_queue` (non-blocking; queue is unbounded).

**Behaviour — `WorkerPool.stop()`:**
1. Set `_running = False`.
2. Put `max_workers` sentinel `None` values onto `_queue` to unblock all workers.
3. Join each thread with `thread.join(timeout=60)`. If a thread does not finish within 60 s, log `WARNING: worker thread did not exit cleanly`.

**Behaviour — `WorkerPool._worker_loop()` (private):**
1. Loop: call `_queue.get(block=True)`.
2. If item is `None`, break (exit thread).
3. Call `_run_job(item)`.
4. Call `_queue.task_done()`.

**Behaviour — `WorkerPool._run_job(job: Job)` (private):**
1. Acquire the per-file lock for `job.target` (create it if absent, using `_meta_lock` to guard the dict).
2. Build the subprocess command:
   - `JobKind.FILE` → `["uvx", "jcodemunch-mcp", "index-file", job.target]`
   - `JobKind.FOLDER` → `["uvx", "jcodemunch-mcp", "index", job.target]`
3. Log `INFO: running <cmd>`.
4. Call `subprocess.run(cmd, timeout=SUBPROCESS_TIMEOUT_SECONDS, capture_output=True, text=True)`.
5. If `returncode != 0`, log `WARNING: jcodemunch exited <returncode>; stderr: <stderr[:500]>`.
6. If `returncode == 0`, log `DEBUG: indexed <job.target> successfully`.
7. On `subprocess.TimeoutExpired`, log `WARNING: jcodemunch timed out for <job.target>`.
8. On any other exception, log `ERROR: unexpected error running jcodemunch: <exc>`.
9. Always release the per-file lock in a `finally` block.

---

### `jcrefresher/watcher.py`

**File path:** `jcrefresher/watcher.py`
**Purpose:** Owns the watchdog observer, manages per-repo watches, and runs the periodic rediscovery loop.

**Dependencies:**
- `threading` (stdlib)
- `logging` (stdlib)
- `pathlib` (stdlib)
- `watchdog.observers` (third-party: `watchdog`)
- `watchdog.events` (third-party: `watchdog`)
- `jcrefresher.discovery` (`discover_repos`, `RepoRecord`)
- `jcrefresher.filters` (`should_ignore`)
- `jcrefresher.debounce` (`Debouncer`)
- `jcrefresher.dispatcher` (`Dispatcher`)
- `jcrefresher.worker` (`WorkerPool`)

**Constants:**
```python
REDISCOVERY_INTERVAL_SECONDS: int = 30
DEBOUNCE_WINDOW_SECONDS: float = 2.0
```

**Public interface:**
```python
class WatchManager:
    def __init__(self, pool: WorkerPool) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
```

**Behaviour — `WatchManager.__init__`:**
1. Store `pool`.
2. Create `_dispatcher = Dispatcher(pool)`.
3. Create `_debouncer = Debouncer(DEBOUNCE_WINDOW_SECONDS, self._on_debounced_event)`.
4. Create `_observer = watchdog.observers.Observer()`.
5. Create `_watches: dict[str, tuple[watchdog.observers.api.ObservedWatch, RepoRecord]]` keyed by `source_root` string.
6. Create `_lock: threading.Lock` protecting `_watches`.
7. Create `_rediscovery_timer: threading.Timer | None = None`.

**Behaviour — `WatchManager.start()`:**
1. Call `_observer.start()`.
2. Call `_sync_watches()` (initial discovery).
3. Call `_schedule_rediscovery()`.
4. Log `INFO: WatchManager started`.

**Behaviour — `WatchManager.stop()`:**
1. If `_rediscovery_timer` is not `None`, cancel it.
2. Call `_debouncer.flush_all()` (fire any pending debounced events before stopping).
3. Call `_debouncer.shutdown()`.
4. Call `_observer.stop()`.
5. Call `_observer.join()`.
6. Log `INFO: WatchManager stopped`.

**Behaviour — `WatchManager._schedule_rediscovery()` (private):**
1. Create a `threading.Timer(REDISCOVERY_INTERVAL_SECONDS, self._rediscovery_tick)`.
2. Set `timer.daemon = True`.
3. Store in `_rediscovery_timer`. Start it.

**Behaviour — `WatchManager._rediscovery_tick()` (private):**
1. Call `_sync_watches()`.
2. Call `_schedule_rediscovery()` (reschedule — this creates a one-shot timer that reschedules itself, forming a loop).

**Behaviour — `WatchManager._sync_watches()` (private):**
1. Call `discovery.discover_repos()` to get `current_records: list[RepoRecord]`.
2. Build `current_roots: set[str]` from `{str(r.source_root) for r in current_records}`.
3. Acquire `_lock`.
4. **Add new watches:** for each record whose `source_root` is not in `_watches`:
   a. Create a `_RepoEventHandler(source_root=str(record.source_root), debouncer=self._debouncer)`.
   b. Call `watch = self._observer.schedule(handler, str(record.source_root), recursive=True)`.
   c. Store `_watches[str(record.source_root)] = (watch, record)`.
   d. Log `INFO: watching <source_root>`.
5. **Remove stale watches:** for each `root` in `_watches` not in `current_roots`:
   a. Retrieve `(watch, _)` from `_watches`.
   b. Call `self._observer.unschedule(watch)`.
   c. Delete `_watches[root]`.
   d. Log `INFO: stopped watching <root>`.
6. Release `_lock`.

**Behaviour — `WatchManager._on_debounced_event(path: str, event_type: str)` (private):**
1. Acquire `_lock`.
2. Find which watched `source_root` is a prefix of `path`. Iterate `_watches` keys and match via `path.startswith(root)`. Take the longest matching root to handle nested mounts.
3. Release `_lock`.
4. If no matching root found, log `WARNING: debounced event for unmatched path <path>` and return.
5. Call `self._dispatcher.dispatch(path, event_type, source_root=matched_root)`.

---

### `_RepoEventHandler` (inside `watcher.py`)

**Purpose:** watchdog `FileSystemEventHandler` subclass; filters events and pushes them to the debouncer.

**Not part of public interface** — only instantiated by `WatchManager._sync_watches`.

**Class definition:**
```python
class _RepoEventHandler(watchdog.events.FileSystemEventHandler):
    def __init__(self, source_root: str, debouncer: Debouncer) -> None: ...
    def on_modified(self, event: watchdog.events.FileSystemEvent) -> None: ...
    def on_created(self, event: watchdog.events.FileSystemEvent) -> None: ...
    def on_deleted(self, event: watchdog.events.FileSystemEvent) -> None: ...
    def on_moved(self, event: watchdog.events.FileSystemEvent) -> None: ...
```

**Behaviour — all `on_*` methods follow this pattern:**
1. Determine the relevant path:
   - `on_modified` / `on_created` / `on_deleted`: use `event.src_path`.
   - `on_moved`: use `event.dest_path` (the new location is what needs reindexing).
2. Call `filters.should_ignore(path)`. If `True`, return immediately.
3. Determine `event_type`:
   - `on_modified` and `event.is_directory == False` → `"file_modify"`
   - `on_created` and `event.is_directory == False` → `"file_create"`
   - `on_deleted` → `"file_delete"`
   - `on_moved` → `"file_create"` (treat destination as new file) if dest is file; `"dir_event"` if dest is directory.
   - Any event where `event.is_directory == True` → `"dir_event"`.
4. Call `self._debouncer.push(path, event_type)`.

---

## Systemd Unit File

**File path:** `jcrefresher.service` (repo root; `install.sh` copies it to `~/.config/systemd/user/`)

**Content:**
```ini
[Unit]
Description=jcrefresher - jcodemunch index refresh daemon
After=default.target

[Service]
Type=simple
ExecStart=/usr/bin/env python3 -m jcrefresher
WorkingDirectory=%h
Restart=on-failure
RestartSec=10s
StandardOutput=journal
StandardError=journal
SyslogIdentifier=jcrefresher
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
```

**Notes:**
- `%h` expands to the user's home directory.
- `PYTHONUNBUFFERED=1` ensures log lines reach journald immediately without buffering.
- `Restart=on-failure` with `RestartSec=10s` satisfies the auto-restart-on-failure requirement.
- The unit uses `WantedBy=default.target` (not `graphical.target`) so it also runs in headless/SSH sessions.
- `python3 -m jcrefresher` requires the package to be installed or on `PYTHONPATH`. The install script handles this.

---

## Install Script

**File path:** `install.sh`

**Steps the script must perform (in order):**
1. `#!/usr/bin/env bash` with `set -euo pipefail`.
2. Resolve the repo root: `REPO_DIR="$(cd "$(dirname "$0")" && pwd)"`.
3. Ensure `watchdog` is installed: `pip3 install --user watchdog`. If pip fails, print error and exit 1.
4. Create systemd user dir: `mkdir -p ~/.config/systemd/user/`.
5. Copy unit file: `cp "$REPO_DIR/jcrefresher.service" ~/.config/systemd/user/jcrefresher.service`.
6. Write a launcher wrapper or set `PYTHONPATH`: patch the service file's `ExecStart` if needed, OR add `Environment=PYTHONPATH=<repo_dir>` to the copied unit file using `sed`. Decision: inject `Environment=PYTHONPATH=$REPO_DIR` line into the `[Service]` section of the copied file so the module is importable without a pip install of jcrefresher itself.
7. Reload systemd user daemon: `systemctl --user daemon-reload`.
8. Enable the service: `systemctl --user enable jcrefresher`.
9. Start the service: `systemctl --user start jcrefresher`.
10. Print: `jcrefresher installed and started. Check status with: systemctl --user status jcrefresher`.

---

## Uninstall Script

**File path:** `uninstall.sh`

**Steps:**
1. `#!/usr/bin/env bash` with `set -euo pipefail`.
2. Stop: `systemctl --user stop jcrefresher || true`.
3. Disable: `systemctl --user disable jcrefresher || true`.
4. Remove unit file: `rm -f ~/.config/systemd/user/jcrefresher.service`.
5. Reload: `systemctl --user daemon-reload`.
6. Print: `jcrefresher uninstalled.`
7. Do NOT uninstall `watchdog` or remove the repo directory — leave environment clean-up to the user.

---

## Threading Model Summary

| Thread | Owner | Role |
|---|---|---|
| Main thread | `__main__` | Blocks on `signal.pause()`; handles shutdown |
| Observer thread | `watchdog` (internal) | Delivers `FileSystemEvent` to handlers |
| Debounce timers | `threading.Timer` (one per pending path) | Fire `_fire()` after quiet window |
| Worker threads × N | `WorkerPool` | Pull from queue; run subprocesses |
| Rediscovery timer | `threading.Timer` (self-rescheduling) | Calls `_sync_watches()` every 30 s |

Concurrency guarantees:
- Per-file serialisation: `WorkerPool._per_file_locks` ensures at most one subprocess per target path at a time.
- Cross-repo parallelism: different target paths proceed concurrently up to `max_workers`.
- `_watches` dict is always accessed under `WatchManager._lock`.
- `Debouncer._timers` / `_event_types` are always accessed under `Debouncer._lock`.

---

## Error Handling Summary

| Condition | Behaviour |
|---|---|
| `~/.code-index/` absent | Log WARNING; return empty list; no watches registered |
| `.db` file is not valid SQLite | Log WARNING; skip that db |
| `meta` table missing `source_root` | Log WARNING; skip that db |
| Source root path missing on disk | Log WARNING; skip watch |
| jcodemunch CLI exits non-zero | Log WARNING with first 500 chars of stderr; continue |
| jcodemunch CLI times out (5 min) | Log WARNING; continue |
| Unexpected subprocess exception | Log ERROR; continue |
| Debouncer push after shutdown | Return immediately; no-op |
| Enqueue after pool stopped | Log WARNING; drop job |

---

## Decisions Made (Resolving Architecture Open Questions)

1. **Missing source root on disk** → log WARNING and skip; daemon does not crash.
2. **Symlinks** → resolved via `Path.resolve()` before registering watch; watchdog watches the link target.
3. **Concurrency** → parallel reindexes across repos/files allowed; serialised per target path via `_per_file_locks`.
4. **Startup catch-up** → no full reindex on boot; existing index trusted.
5. **Change storms beyond debounce** → 2 s debounce window is the only rate-limiting mechanism; no per-minute ceiling.

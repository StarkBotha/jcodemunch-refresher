# jcrefresher Verification Report

**Date:** 2026-04-21
**Spec:** /home/stark-botha/dev/fsystems/projects/jcrefresher/docs/spec.md
**Build result:** PASS — all modules compile without errors (`python3 -m py_compile` exit 0)

---

## Pass

All files exist at the required paths.

### `__init__.py`
- `__version__ = "0.1.0"` present. File is otherwise empty. PASS.

### `__main__.py`
- `main()` signature matches.
- `logging.basicConfig` called with correct level, format string, and `stream=sys.stderr`.
- `WorkerPool` instantiated with `max_workers=4`, started before `WatchManager`.
- `WatchManager` instantiated with the pool and started.
- `SIGTERM` and `SIGINT` both installed via `signal.signal`; both call `_shutdown`.
- `signal.pause()` blocks the main thread.
- `_shutdown` logs INFO "Shutdown signal received", calls `watcher_manager.stop()`, `worker_pool.stop()`, then `sys.exit(0)`.
- No indexing logic or log file management present.

### `discovery.py`
- `INDEX_DIR` constant correct.
- `RepoRecord` dataclass with `db_path: Path` and `source_root: Path`.
- `discover_repos()` signature and all steps match: missing dir warning, `.db` filter, `_read_source_root` call, missing-source-root warning, `source_root.resolve()`, appends resolved record.
- `_read_source_root` uses `mode=ro` URI, queries `meta` table, handles empty/None row, strips whitespace, returns `Path`, catches `sqlite3.DatabaseError`, closes in `finally`.

### `filters.py`
- `IGNORED_DIR_NAMES` and `IGNORED_SUFFIXES` constants match spec exactly (all members present).
- `should_ignore(path: str | Path) -> bool` signature correct.
- Checks all parts of path against `IGNORED_DIR_NAMES`.
- Checks `p.suffix` against `IGNORED_SUFFIXES`.
- Checks `p.name.endswith("~")` separately (covers trailing-tilde case).
- Does NOT ignore hidden files beyond listed names/suffixes — matches spec decision.

### `debounce.py`
- `DebounceCallback` type alias correct.
- `Debouncer.__init__`: stores window, callback; creates `_timers`, `_lock`, `_event_types`, `_shutdown_flag`.
- `push`: acquires lock, checks shutdown flag, cancels existing timer, applies priority rule (keeps `dir_event`), creates and starts new timer.
- `_fire`: acquires lock, pops path from both dicts, releases, fires callback only if event_type is not None.
- `flush_all`: cancels all timers, snapshots, clears both dicts, fires all callbacks after lock release.
- `shutdown`: sets flag, cancels timers, clears dicts — does NOT fire callbacks.

### `dispatcher.py`
- `Dispatcher.__init__(pool)` and `dispatch(path, event_type, source_root)` signatures match.
- `dir_event` or `file_delete` → `Job(kind=JobKind.FOLDER, target=source_root)` enqueued with correct DEBUG log.
- Otherwise → `Job(kind=JobKind.FILE, target=path)` enqueued with correct DEBUG log.
- `from jcrefresher.worker import Job, JobKind, WorkerPool` present (re-export available to callers via this module).

### `worker.py`
- All constants present with correct values.
- `JobKind` enum with `FILE` and `FOLDER` via `auto()`.
- `Job` dataclass with `kind: JobKind` and `target: str`.
- `WorkerPool.__init__`: stores `max_workers`, creates queue, threads list, `_per_file_locks`, `_meta_lock`, sets `_running = False`.
- `start()`: sets `_running = True`, spawns `max_workers` daemon threads.
- `enqueue()`: checks `_running`, logs warning and returns if not running, otherwise puts job.
- `stop()`: sets `_running = False`, puts `max_workers` sentinels, joins with 60s timeout, logs warning if thread still alive.
- `_worker_loop()`: blocks on `get`, breaks on `None`, calls `_run_job`, calls `task_done`.
- `_run_job()`: acquires `_meta_lock` to get/create per-file lock, builds correct command per kind, logs INFO before run, runs subprocess with `capture_output=True, text=True, timeout=300`, logs WARNING on non-zero returncode with `stderr[:500]`, logs DEBUG on success, catches `TimeoutExpired` with WARNING, catches other exceptions with ERROR, releases file lock in `finally`.

### `watcher.py`
- Constants `REDISCOVERY_INTERVAL_SECONDS = 30` and `DEBOUNCE_WINDOW_SECONDS = 2.0` correct.
- `WatchManager.__init__`: all attributes created as specified.
- `start()`: observer started, `_sync_watches()` called, `_schedule_rediscovery()` called, logs INFO.
- `stop()`: cancels timer if not None, calls `flush_all()`, then `shutdown()`, stops and joins observer, logs INFO.
- `_schedule_rediscovery()`: creates daemon timer, stores, starts.
- `_rediscovery_tick()`: calls `_sync_watches()` then `_schedule_rediscovery()`.
- `_sync_watches()`: calls `discover_repos()`, builds `current_roots` set, acquires lock, adds new watches with handler + schedule, logs INFO; removes stale watches with unschedule + delete, logs INFO; releases lock.
- `_on_debounced_event()`: acquires lock, finds longest matching root via `startswith`, releases lock, logs WARNING if unmatched, calls `dispatcher.dispatch`.
- `_RepoEventHandler`: extends `FileSystemEventHandler`, all four `on_*` methods present.
  - `on_modified`/`on_created`: uses `src_path`, checks `should_ignore`, routes to `dir_event` or `file_modify`/`file_create` based on `is_directory`.
  - `on_deleted`: uses `src_path`, always `file_delete` (no `is_directory` branch — matches spec; spec says `on_deleted` → `"file_delete"` regardless).
  - `on_moved`: uses `dest_path`, checks `should_ignore`, routes to `file_create` or `dir_event` based on `is_directory`.

### `jcrefresher.service`
- All fields match spec exactly: Description, After, Type, ExecStart, WorkingDirectory, Restart, RestartSec, StandardOutput, StandardError, SyslogIdentifier, Environment, WantedBy.

### `install.sh`
- Shebang `#!/usr/bin/env bash` and `set -euo pipefail` present.
- `REPO_DIR` resolved correctly.
- `pip3 install --user watchdog` with error check and `exit 1`.
- `mkdir -p ~/.config/systemd/user/`.
- `cp` unit file to correct destination.
- `sed -i` injects `Environment=PYTHONPATH=$REPO_DIR` after `[Service]` line.
- `systemctl --user daemon-reload`, `enable`, `start` in order.
- Correct final print message.

### `uninstall.sh`
- Shebang and `set -euo pipefail` present.
- `stop || true`, `disable || true`, `rm -f`, `daemon-reload` in order.
- Correct final print message.
- Does not uninstall watchdog or remove repo.

---

## Fail

One deviation found.

### `on_deleted` — missing `is_directory` branch

**Spec (watcher.py `_RepoEventHandler`):**
> Determine `event_type`:
> - `on_deleted` → `"file_delete"`
> - Any event where `event.is_directory == True` → `"dir_event"`.

The spec lists both rules. The `"dir_event"` escalation for `is_directory == True` is stated as a general rule applying to all event handlers, including `on_deleted`. The implementation sets `event_type = "file_delete"` unconditionally in `on_deleted`, with no check for `event.is_directory`. If a directory is deleted, it will be classified as `"file_delete"` instead of `"dir_event"`.

However, the spec also specifically says `on_deleted → "file_delete"` without qualification, so there is an ambiguity in the spec itself. The implementation follows the specific rule over the general rule. This is flagged as a potential deviation for human review.

**Spec:** `on_deleted` with `is_directory == True` should yield `"dir_event"` (general rule).
**Implementation:** `on_deleted` always yields `"file_delete"` regardless of `is_directory`.

---

## Stubs

None. No stub comments or `pass`/`NotImplementedError` placeholders found in any file.

---

## Build Result

PASS. All eight Python modules compile cleanly with `python3 -m py_compile`. Exit code 0.

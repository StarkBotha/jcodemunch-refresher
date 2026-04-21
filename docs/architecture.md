# jcrefresher Architecture

## Goal
A lightweight Linux user-space daemon that keeps jcodemunch indexes fresh by watching all indexed source roots and re-invoking the jcodemunch CLI on file changes, so symbol search results stay current without manual reindexing.

## Boundaries
**Owns:** Discovery of indexed repos from the jcodemunch index directory, filesystem watching of discovered source roots, debouncing of change events, invocation of jcodemunch CLI subcommands, systemd user service lifecycle.

**Does not own:** The indexing logic itself (delegated to `uvx jcodemunch-mcp`), MCP protocol communication, the index storage format, initial repo indexing (user must run `index` once before daemon tracks it), editor integration.

## Key Decisions
1. **Filesystem-only repo discovery** — read the jcodemunch index directory rather than call MCP tools. Keeps the daemon decoupled from MCP transport and avoids spawning an MCP session just to enumerate repos.
2. **watchdog over raw inotify** — cross-platform abstraction, mature debouncing primitives, handles recursive watches and watch-descriptor exhaustion more gracefully.
3. **CLI subprocess invocation** — `uvx jcodemunch-mcp index-file` / `index` as separate processes. Matches the stdio-only transport constraint and isolates failures.
4. **Periodic rediscovery loop** — poll the index directory on a fixed interval (e.g. 30s) rather than watch it with inotify. Simpler, avoids race conditions with index creation, cheap.
5. **Per-path debounce window** — coalesce rapid saves (editors, formatters, git checkouts) into a single reindex per path after a quiet period (~2s).
6. **Event-type routing** — single file modify/create goes to `index-file`; directory-level events, bulk deletes, or rename storms escalate to a folder-level `index` call.
7. **Systemd user unit** — runs as the logged-in user, no root required, auto-restart on failure, honours user session lifecycle.

## Tradeoffs
- Polling the index directory adds a small discovery latency (up to poll interval) — acceptable because new repos are rare.
- Spawning a subprocess per change is heavier than an in-process API call — acceptable because debouncing keeps invocation rate low and process isolation improves robustness.
- Filesystem-only discovery may drift from MCP's authoritative view if jcodemunch changes its on-disk layout — mitigated by centralising the parsing logic so it can be updated in one place.

## Constraints
- Python 3 (version matching Ubuntu LTS default).
- `watchdog` as the sole filesystem-watching dependency.
- No persistent state on disk beyond logs; daemon must be restart-safe and idempotent.
- Must not require root; install path under `~/.config/systemd/user/`.
- Must not block the watch loop on subprocess execution — reindex work runs off the event thread.
- Logging to stdout/stderr for journald capture; no custom log files.
- Ignore common noise paths (`.git/`, `node_modules/`, `__pycache__/`, `.venv/`, editor swap files) before debouncing.

## Data Flow
1. Startup: daemon reads the jcodemunch index directory, parses each repo's metadata to extract its source root path, registers a recursive watch for each.
2. Runtime: filesystem events from watched roots enter a debounce layer keyed by path; after the quiet window, a dispatcher classifies the event and enqueues a reindex job.
3. Worker: pulls jobs from the queue, invokes the appropriate jcodemunch CLI subcommand as a subprocess, logs outcome.
4. Rediscovery: a periodic timer re-reads the index directory; new repos gain watches, removed repos have watches torn down.
5. Shutdown: systemd signal stops watches, drains the queue, exits cleanly.

## Confirmed: Index Directory Layout
- `~/.code-index/` contains one `.db` file per repo, named `local-<reponame>-<8charhash>.db` (or `<GithubUser>-<reponame>.db` for remote repos)
- Each `.db` is a SQLite database with a `meta` table (`key`, `value` columns)
- Source root is stored as `SELECT value FROM meta WHERE key = 'source_root'`
- There are also plain directories mirroring source structure (no metadata there — ignore them)

## Open Questions (Resolved/Remaining)
- **Behaviour when source root no longer exists** on disk (repo deleted but index retained) — skip silently or log warning?
- **Handling of symlinked source roots** — follow or watch the link target?
- **Concurrency policy** — should simultaneous reindexes for different repos be allowed, or serialised to avoid CPU contention?
- **Initial catch-up on startup** — should the daemon trigger a full `index` on each repo at boot in case changes happened while it was down, or trust the existing index?
- **Rate-limit policy for pathological change storms** (e.g. `npm install`) beyond simple debouncing — is a max-jobs-per-minute ceiling needed?

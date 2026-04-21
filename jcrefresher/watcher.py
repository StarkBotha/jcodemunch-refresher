import logging
import threading
from pathlib import Path

import watchdog.observers
import watchdog.observers.api
import watchdog.events

from jcrefresher import discovery
from jcrefresher.debounce import Debouncer
from jcrefresher.discovery import RepoRecord, discover_repos
from jcrefresher.dispatcher import Dispatcher
from jcrefresher.filters import should_ignore
from jcrefresher.worker import WorkerPool

logger = logging.getLogger(__name__)

# How often to re-scan ~/.code-index/ for new or removed repos
REDISCOVERY_INTERVAL_SECONDS: int = 30
# Quiet period after the last event on a path before we actually dispatch a job;
# keeps burst edits (e.g. a save + format on write) from spawning many redundant jobs
DEBOUNCE_WINDOW_SECONDS: float = 2.0


class _RepoEventHandler(watchdog.events.FileSystemEventHandler):
    def __init__(self, source_root: str, debouncer: Debouncer) -> None:
        super().__init__()
        self._source_root = source_root
        # All repos share a single Debouncer instance so cross-repo event coalescing
        # works correctly (the path key is globally unique).
        self._debouncer = debouncer

    def on_modified(self, event: watchdog.events.FileSystemEvent) -> None:
        path = event.src_path
        logger.debug("raw event: modified path=%s is_directory=%s", path, event.is_directory)
        if should_ignore(path):
            return
        # Directory modify events (e.g. mtime change when a file inside is added) get
        # escalated to dir_event so the dispatcher triggers a full folder reindex.
        if event.is_directory:
            event_type = "dir_event"
        else:
            event_type = "file_modify"
        self._debouncer.push(path, event_type)

    def on_created(self, event: watchdog.events.FileSystemEvent) -> None:
        path = event.src_path
        logger.debug("raw event: created path=%s is_directory=%s", path, event.is_directory)
        if should_ignore(path):
            return
        if event.is_directory:
            event_type = "dir_event"
        else:
            event_type = "file_create"
        self._debouncer.push(path, event_type)

    def on_deleted(self, event: watchdog.events.FileSystemEvent) -> None:
        path = event.src_path
        logger.debug("raw event: deleted path=%s is_directory=%s", path, event.is_directory)
        if should_ignore(path):
            return
        event_type = "dir_event" if event.is_directory else "file_delete"
        self._debouncer.push(path, event_type)

    def on_moved(self, event: watchdog.events.FileSystemEvent) -> None:
        logger.debug(
            "raw event: moved src=%s dest=%s is_directory=%s",
            event.src_path,
            event.dest_path,
            event.is_directory,
        )
        # Index the destination path — the source path no longer exists, so indexing
        # it would be a no-op or an error.
        path = event.dest_path
        if should_ignore(path):
            return
        if event.is_directory:
            event_type = "dir_event"
        else:
            event_type = "file_create"
        self._debouncer.push(path, event_type)


class WatchManager:
    def __init__(self, pool: WorkerPool) -> None:
        self._pool = pool
        self._dispatcher = Dispatcher(pool)
        # Single shared Debouncer: one callback handles events from all watched repos
        self._debouncer = Debouncer(DEBOUNCE_WINDOW_SECONDS, self._on_debounced_event)
        # watchdog.Observer runs an inotify thread internally; we call schedule/unschedule
        # from our own threads, which is safe per watchdog's documented API.
        self._observer = watchdog.observers.Observer()
        # Maps source_root string → (ObservedWatch, RepoRecord) for unschedule bookkeeping
        self._watches: dict[str, tuple[watchdog.observers.api.ObservedWatch, RepoRecord]] = {}
        # _lock guards _watches; the observer's own internal lock is separate
        self._lock = threading.Lock()
        self._rediscovery_timer: threading.Timer | None = None

    def start(self) -> None:
        self._observer.start()
        self._sync_watches()
        self._schedule_rediscovery()
        logger.info("WatchManager started")

    def stop(self) -> None:
        if self._rediscovery_timer is not None:
            self._rediscovery_timer.cancel()
        # flush_all fires any buffered events synchronously before shutdown so we don't
        # silently drop changes that arrived just before a SIGTERM.
        self._debouncer.flush_all()
        self._debouncer.shutdown()
        self._observer.stop()
        self._observer.join()
        logger.info("WatchManager stopped")

    def _schedule_rediscovery(self) -> None:
        # Use a one-shot Timer that reschedules itself rather than a repeating thread,
        # so a slow discover_repos() call (e.g. many DB files) never causes overlapping
        # rediscovery runs.
        timer = threading.Timer(REDISCOVERY_INTERVAL_SECONDS, self._rediscovery_tick)
        timer.daemon = True
        self._rediscovery_timer = timer
        timer.start()

    def _rediscovery_tick(self) -> None:
        logger.info("rediscovery tick: starting")
        self._sync_watches()
        logger.info("rediscovery tick: complete, watching %d repos", len(self._watches))
        self._schedule_rediscovery()

    def _sync_watches(self) -> None:
        current_records = discover_repos()
        current_roots: set[str] = {str(r.source_root) for r in current_records}

        with self._lock:
            # Add watches for repos that appeared since the last sync
            for record in current_records:
                root_str = str(record.source_root)
                if root_str not in self._watches:
                    handler = _RepoEventHandler(
                        source_root=root_str,
                        debouncer=self._debouncer,
                    )
                    watch = self._observer.schedule(handler, root_str, recursive=True)
                    self._watches[root_str] = (watch, record)
                    logger.info("watch added: source_root=%s db=%s", root_str, record.db_path.name)

            # Unschedule watches for repos whose DB files were removed from ~/.code-index/
            stale = [root for root in self._watches if root not in current_roots]
            for root in stale:
                watch, _ = self._watches[root]
                self._observer.unschedule(watch)
                del self._watches[root]
                logger.info("watch removed: source_root=%s (no longer in index)", root)

    def _on_debounced_event(self, path: str, event_type: str) -> None:
        # Find the longest matching watched root for this path; longest-match wins
        # so a repo nested inside another repo is always attributed to its own watch.
        with self._lock:
            matched_root: str | None = None
            for root in self._watches:
                if path.startswith(root):
                    if matched_root is None or len(root) > len(matched_root):
                        matched_root = root

        if matched_root is None:
            # Can happen if a watch was removed between the event firing and this callback
            logger.warning("debounced event for unmatched path: path=%s event_type=%s", path, event_type)
            return

        logger.debug(
            "debounced event dispatching: path=%s event_type=%s matched_root=%s",
            path,
            event_type,
            matched_root,
        )
        self._dispatcher.dispatch(path, event_type, source_root=matched_root)

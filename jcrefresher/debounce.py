import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

DebounceCallback = Callable[[str, str], None]


class Debouncer:
    def __init__(self, window_seconds: float, callback: DebounceCallback) -> None:
        self._window_seconds = window_seconds
        self._callback = callback
        # One Timer per path; a new event for the same path cancels and replaces it
        self._timers: dict[str, threading.Timer] = {}
        # _lock guards both _timers and _event_types together — they must stay in sync
        self._lock = threading.Lock()
        # Stores the "winning" event type that will be passed to the callback when the timer fires
        self._event_types: dict[str, str] = {}
        self._shutdown_flag = False

    def push(self, path: str, event_type: str) -> None:
        with self._lock:
            if self._shutdown_flag:
                logger.debug("push ignored (shutdown): path=%s event_type=%s", path, event_type)
                return

            if path in self._timers:
                # Cancel the old timer before creating a new one; the cancel() call is
                # safe even if the timer has already fired — it's a no-op in that case.
                # However, _fire() acquires _lock before reading _event_types, so if
                # _fire() already holds the lock it has already popped the entry and
                # the cancel() here is harmless.
                self._timers[path].cancel()
                logger.debug("timer reset for path=%s (new event_type=%s)", path, event_type)

            # dir_event means a directory itself changed, which requires a full folder
            # reindex.  Once escalated to dir_event it must stay that way regardless of
            # subsequent file-level events in the same debounce window.
            existing = self._event_types.get(path)
            if existing == "dir_event":
                self._event_types[path] = "dir_event"
            else:
                self._event_types[path] = event_type

            logger.debug("push queued: path=%s event_type=%s", path, event_type)
            timer = threading.Timer(self._window_seconds, self._fire, args=[path])
            self._timers[path] = timer
            timer.start()

    def _fire(self, path: str) -> None:
        # _fire runs on a Timer thread; acquire the lock to atomically remove both
        # the timer record and the event type so push() cannot race with a stale entry
        with self._lock:
            event_type = self._event_types.pop(path, None)
            self._timers.pop(path, None)

        if event_type is not None:
            logger.debug("timer fired: path=%s event_type=%s — calling callback", path, event_type)
            self._callback(path, event_type)

    def flush_all(self) -> None:
        # Called during graceful shutdown to drain pending events synchronously
        # so no indexing work is silently dropped on exit.
        with self._lock:
            pending_count = len(self._timers)
            for timer in self._timers.values():
                timer.cancel()
            snapshot = dict(self._event_types)
            self._timers.clear()
            self._event_types.clear()

        logger.info("flush_all: flushing %d pending debounced events", pending_count)
        for path, event_type in snapshot.items():
            logger.debug("flush_all: firing path=%s event_type=%s", path, event_type)
            self._callback(path, event_type)

    def shutdown(self) -> None:
        # Set the flag under the lock so push() never starts a new timer after this point
        with self._lock:
            self._shutdown_flag = True
            pending_count = len(self._timers)
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            self._event_types.clear()
        logger.info("shutdown: cancelled %d pending timers, accepting no further events", pending_count)

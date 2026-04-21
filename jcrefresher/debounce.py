import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

DebounceCallback = Callable[[str, str], None]


class Debouncer:
    def __init__(self, window_seconds: float, callback: DebounceCallback) -> None:
        self._window_seconds = window_seconds
        self._callback = callback
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        self._event_types: dict[str, str] = {}
        self._shutdown_flag = False

    def push(self, path: str, event_type: str) -> None:
        with self._lock:
            if self._shutdown_flag:
                logger.debug("push ignored (shutdown): path=%s event_type=%s", path, event_type)
                return

            if path in self._timers:
                self._timers[path].cancel()
                logger.debug("timer reset for path=%s (new event_type=%s)", path, event_type)

            # Priority rule: dir_event escalates and stays escalated
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
        with self._lock:
            event_type = self._event_types.pop(path, None)
            self._timers.pop(path, None)

        if event_type is not None:
            logger.debug("timer fired: path=%s event_type=%s — calling callback", path, event_type)
            self._callback(path, event_type)

    def flush_all(self) -> None:
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
        with self._lock:
            self._shutdown_flag = True
            pending_count = len(self._timers)
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            self._event_types.clear()
        logger.info("shutdown: cancelled %d pending timers, accepting no further events", pending_count)

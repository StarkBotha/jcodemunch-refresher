import logging
import queue
import subprocess
import threading
from dataclasses import dataclass
from enum import Enum, auto

logger = logging.getLogger(__name__)

# uvx resolves and runs jcodemunch-mcp without requiring a fixed install path,
# which means the daemon works regardless of which virtualenv is active.
JCODEMUNCH_CMD: str = "uvx"
JCODEMUNCH_ARGS_FILE: list[str] = ["jcodemunch-mcp", "index-file"]
JCODEMUNCH_ARGS_FOLDER: list[str] = ["jcodemunch-mcp", "index"]
# 300s is generous; a large repo full-reindex can be slow on cold disk cache
SUBPROCESS_TIMEOUT_SECONDS: int = 300


class JobKind(Enum):
    FILE = auto()
    FOLDER = auto()


@dataclass
class Job:
    kind: JobKind
    target: str  # absolute path — either a single file or a repo root


class WorkerPool:
    def __init__(self, max_workers: int = 4) -> None:
        self._max_workers = max_workers
        # Unbounded queue: backpressure is handled by the debounce window upstream,
        # not here — we never want to block the watchdog observer thread.
        self._queue: queue.Queue[Job | None] = queue.Queue()
        self._threads: list[threading.Thread] = []
        # Per-target locks prevent two workers from running jcodemunch on the same
        # file/folder simultaneously, which would cause interleaved index writes.
        self._per_file_locks: dict[str, threading.Lock] = {}
        # _meta_lock guards _per_file_locks dict itself (not the per-file locks)
        self._meta_lock = threading.Lock()
        self._running = False

    def start(self) -> None:
        self._running = True
        for _ in range(self._max_workers):
            t = threading.Thread(target=self._worker_loop, daemon=True)
            self._threads.append(t)
            t.start()

    def enqueue(self, job: Job) -> None:
        if not self._running:
            logger.warning("pool not running; job dropped")
            return
        self._queue.put(job)

    def stop(self) -> None:
        self._running = False
        # Send one sentinel (None) per worker thread to unblock each queue.get()
        for _ in range(self._max_workers):
            self._queue.put(None)
        for t in self._threads:
            t.join(timeout=60)
            if t.is_alive():
                logger.warning("worker thread did not exit cleanly")

    def _worker_loop(self) -> None:
        thread_name = threading.current_thread().name
        logger.debug("worker thread started: %s", thread_name)
        while True:
            item = self._queue.get(block=True)
            if item is None:
                # Sentinel received — this thread's work is done
                logger.debug("worker thread %s received sentinel; draining and exiting", thread_name)
                self._queue.task_done()
                break
            self._run_job(item)
            self._queue.task_done()

    def _run_job(self, job: Job) -> None:
        # Acquire _meta_lock only long enough to look up or create the per-target lock;
        # the actual jcodemunch subprocess runs under the narrower per-target lock.
        with self._meta_lock:
            if job.target not in self._per_file_locks:
                self._per_file_locks[job.target] = threading.Lock()
            file_lock = self._per_file_locks[job.target]

        logger.debug("acquiring lock for target=%s", job.target)
        file_lock.acquire()
        logger.debug("lock acquired for target=%s", job.target)
        try:
            if job.kind == JobKind.FILE:
                cmd = [JCODEMUNCH_CMD] + JCODEMUNCH_ARGS_FILE + [job.target]
            else:
                cmd = [JCODEMUNCH_CMD] + JCODEMUNCH_ARGS_FOLDER + [job.target]

            logger.info("job start: kind=%s target=%s cmd=%s", job.kind.name, job.target, cmd)
            try:
                result = subprocess.run(
                    cmd,
                    timeout=SUBPROCESS_TIMEOUT_SECONDS,
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    logger.error(
                        "job failed: kind=%s target=%s returncode=%d stderr=%s",
                        job.kind.name,
                        job.target,
                        result.returncode,
                        result.stderr[:500],
                    )
                else:
                    stdout_snippet = result.stdout[:200].strip() if result.stdout.strip() else ""
                    logger.info(
                        "job complete: kind=%s target=%s returncode=0%s",
                        job.kind.name,
                        job.target,
                        f" stdout={stdout_snippet!r}" if stdout_snippet else "",
                    )
                    logger.debug("indexed %s successfully", job.target)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "job timed out after %ds: kind=%s target=%s",
                    SUBPROCESS_TIMEOUT_SECONDS,
                    job.kind.name,
                    job.target,
                )
            except Exception as exc:
                logger.error(
                    "unexpected error running jcodemunch: kind=%s target=%s error=%s",
                    job.kind.name,
                    job.target,
                    exc,
                )
        finally:
            logger.debug("releasing lock for target=%s", job.target)
            file_lock.release()

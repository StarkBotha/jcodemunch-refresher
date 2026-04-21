import logging
from pathlib import Path

from jcrefresher.worker import Job, JobKind, WorkerPool

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(self, pool: WorkerPool) -> None:
        self._pool = pool

    def dispatch(self, path: str, event_type: str, source_root: str) -> None:
        if event_type in ("dir_event", "file_delete"):
            # Directory changes and deletions both invalidate the index in ways that
            # a single-file reindex cannot fix (e.g. a directory rename moves many
            # files at once, a deletion leaves stale index entries).  The safest
            # recovery is a full folder reindex of the repo root.
            job = Job(kind=JobKind.FOLDER, target=source_root)
            logger.info(
                "dispatch: path=%s event_type=%s -> FOLDER job for source_root=%s"
                " (reason: dir/delete events require full folder reindex)",
                path,
                event_type,
                source_root,
            )
            from jcrefresher.worker import JCODEMUNCH_CMD, JCODEMUNCH_ARGS_FOLDER
            cmd = [JCODEMUNCH_CMD] + JCODEMUNCH_ARGS_FOLDER + [source_root]
            logger.debug("enqueuing CLI command: %s", cmd)
            self._pool.enqueue(job)
        else:
            # file_modify and file_create only affect the single changed file,
            # so a targeted index-file call is cheaper than a full folder reindex.
            job = Job(kind=JobKind.FILE, target=path)
            logger.info(
                "dispatch: path=%s event_type=%s -> FILE job"
                " (reason: single-file modification)",
                path,
                event_type,
            )
            from jcrefresher.worker import JCODEMUNCH_CMD, JCODEMUNCH_ARGS_FILE
            cmd = [JCODEMUNCH_CMD] + JCODEMUNCH_ARGS_FILE + [path]
            logger.debug("enqueuing CLI command: %s", cmd)
            self._pool.enqueue(job)

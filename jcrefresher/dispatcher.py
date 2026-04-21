import logging
from pathlib import Path

from jcrefresher.worker import Job, JobKind, WorkerPool

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(self, pool: WorkerPool) -> None:
        self._pool = pool

    def dispatch(self, path: str, event_type: str, source_root: str) -> None:
        if event_type in ("dir_event", "file_delete"):
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

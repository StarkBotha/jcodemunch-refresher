import argparse
import logging
import signal
import sys
import threading

from jcrefresher import __version__, watcher, worker
from jcrefresher.discovery import INDEX_DIR


def _shutdown(
    signum: int,
    watcher_manager: watcher.WatchManager,
    worker_pool: worker.WorkerPool,
) -> None:
    logger = logging.getLogger(__name__)
    sig_name = signal.Signals(signum).name
    logger.info("signal received: %s (%d) — beginning shutdown", sig_name, signum)

    # Stop watcher first so no new jobs are enqueued after the pool drains
    logger.info("shutdown step 1/2: stopping WatchManager")
    watcher_manager.stop()
    logger.info("shutdown step 2/2: stopping WorkerPool")
    worker_pool.stop()
    logger.info("shutdown complete")
    sys.exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jcrefresher",
        description="Filesystem watcher that keeps jcodemunch indexes up to date.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Enable INFO-level logging (default: WARNING)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable DEBUG-level logging (overrides --verbose)",
    )
    args = parser.parse_args()

    # --debug takes precedence over --verbose; no flag → WARNING (quiet by default)
    if args.debug:
        log_level = logging.DEBUG
    elif args.verbose:
        log_level = logging.INFO
    else:
        log_level = logging.WARNING

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    logger = logging.getLogger(__name__)
    # Log at INFO so startup is visible under --verbose but silent in production
    logger.info(
        "jcrefresher starting: version=%s index_dir=%s log_level=%s",
        __version__,
        INDEX_DIR,
        logging.getLevelName(log_level),
    )

    pool = worker.WorkerPool(max_workers=4)
    pool.start()
    logger.info("WorkerPool started with max_workers=4")

    watcher_manager = watcher.WatchManager(pool)
    watcher_manager.start()

    # Use a closure so the signal handler can reference the live objects without globals
    def handle_signal(signum, frame):
        _shutdown(signum, watcher_manager, pool)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    logger.info("signal handlers registered for SIGTERM and SIGINT; entering watch loop")

    # signal.pause() suspends the main thread until a signal arrives;
    # all real work happens in daemon threads spawned by WatchManager and WorkerPool
    signal.pause()


if __name__ == "__main__":
    main()

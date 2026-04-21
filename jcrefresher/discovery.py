import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# jcodemunch stores one SQLite DB per indexed repo under ~/.code-index/
# We poll this directory rather than watch it because inotify on ~/.code-index/
# would race with jcodemunch itself creating/removing DB files, and the 30-second
# rediscovery interval is good enough — new repos are watched within half a minute.
INDEX_DIR: Path = Path.home() / ".code-index"


@dataclass
class RepoRecord:
    db_path: Path
    source_root: Path


def discover_repos() -> list[RepoRecord]:
    if not INDEX_DIR.exists():
        logger.warning("Index directory %s does not exist", INDEX_DIR)
        return []

    results: list[RepoRecord] = []
    for entry in INDEX_DIR.iterdir():
        if not entry.is_file() or entry.suffix != ".db":
            continue

        logger.debug("found db file: %s", entry)

        source_root = _read_source_root(entry)
        if source_root is None:
            logger.warning("skipping %s: could not read source_root", entry)
            continue

        logger.debug("extracted source_root %s from %s", source_root, entry)

        # Skip repos whose source trees have been deleted — no point watching them
        if not source_root.exists():
            logger.warning(
                "source root %s for %s does not exist on disk; skipping",
                source_root,
                entry,
            )
            continue

        # Resolve symlinks so watch comparisons use canonical paths throughout
        resolved = source_root.resolve()
        logger.info("discovered repo: db=%s source_root=%s", entry.name, resolved)
        results.append(RepoRecord(db_path=entry, source_root=resolved))

    return results


def _read_source_root(db_path: Path) -> Path | None:
    conn = None
    try:
        # Open read-only via URI so we never accidentally modify a jcodemunch DB
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.execute("SELECT value FROM meta WHERE key='source_root'")
        row = cursor.fetchone()
        if row is None:
            return None
        value = row[0].strip()
        if not value:
            return None
        return Path(value)
    except sqlite3.DatabaseError as err:
        # Catches corrupt files and non-SQLite files that happen to have a .db extension
        logger.warning("%s is not a valid SQLite db: %s", db_path, err)
        return None
    finally:
        if conn is not None:
            conn.close()

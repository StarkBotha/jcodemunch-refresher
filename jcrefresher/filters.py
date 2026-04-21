import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Directories whose contents we never want indexed — checking every path component
# means a nested path like /project/.git/COMMIT_EDITMSG is caught even if the
# event reports the full absolute path.
IGNORED_DIR_NAMES: frozenset[str] = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", ".mypy_cache",
    ".tox", ".eggs", "dist", "build", ".hg", ".svn",
    "test-results", "coverage", ".nyc_output", "playwright-report",
})

# Transient files written by editors; indexing them would waste jcodemunch calls
# and could cause it to read a file mid-write.
IGNORED_SUFFIXES: frozenset[str] = frozenset({
    ".swp", ".swo", ".swn",   # vim swap
    ".tmp", ".temp",
    "~",                       # editor backup (trailing tilde)
    # Binary/media/archive formats that jcodemunch cannot index
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".webm", ".mp4", ".mp3", ".wav", ".ogg",
    ".pdf",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat",
    ".db", ".sqlite",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pyc", ".pyo",
})


def should_ignore(path: str | Path) -> bool:
    p = Path(path)

    # Walk every component so that e.g. /repo/node_modules/foo/bar.js is filtered
    for part in p.parts:
        if part in IGNORED_DIR_NAMES:
            logger.debug("ignoring %s: path component %r matches ignored dir name", p, part)
            return True
        if part.startswith(".playwright"):
            logger.debug("ignoring %s: path component %r starts with .playwright", p, part)
            return True

    if p.suffix in IGNORED_SUFFIXES:
        logger.debug("ignoring %s: suffix %r matches ignored suffix", p, p.suffix)
        return True

    # p.suffix only returns the last extension; a bare trailing tilde (e.g. file~)
    # has no suffix, so we need a separate name check.
    if p.name.endswith("~"):
        logger.debug("ignoring %s: filename ends with ~ (editor backup)", p)
        return True

    return False

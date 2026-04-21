import logging
from pathlib import Path

logger = logging.getLogger(__name__)

IGNORED_DIR_NAMES: frozenset[str] = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", ".mypy_cache",
    ".tox", ".eggs", "dist", "build", ".hg", ".svn",
})

IGNORED_SUFFIXES: frozenset[str] = frozenset({
    ".swp", ".swo", ".swn",   # vim swap
    ".tmp", ".temp",
    "~",                       # editor backup (trailing tilde)
})


def should_ignore(path: str | Path) -> bool:
    p = Path(path)

    for part in p.parts:
        if part in IGNORED_DIR_NAMES:
            logger.debug("ignoring %s: path component %r matches ignored dir name", p, part)
            return True

    if p.suffix in IGNORED_SUFFIXES:
        logger.debug("ignoring %s: suffix %r matches ignored suffix", p, p.suffix)
        return True

    if p.name.endswith("~"):
        logger.debug("ignoring %s: filename ends with ~ (editor backup)", p)
        return True

    return False

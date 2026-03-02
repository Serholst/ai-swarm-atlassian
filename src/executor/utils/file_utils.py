"""
Atomic file write utilities.

Prevents partial writes from corrupting output files when a process
is interrupted mid-write or two processes race on the same file.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def atomic_write(filepath: Path, content: str, encoding: str = "utf-8") -> None:
    """
    Write content to a temp file, then atomically rename.

    On POSIX systems, ``Path.replace()`` is atomic within the same
    filesystem — the file is either fully written or not present at all.

    Args:
        filepath: Target file path
        content: Text content to write
        encoding: File encoding (default utf-8)
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    tmp = filepath.with_suffix(filepath.suffix + ".tmp")
    tmp.write_text(content, encoding=encoding)
    tmp.replace(filepath)  # Atomic on POSIX

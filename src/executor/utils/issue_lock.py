"""
File-based locking per Jira issue key.

Prevents concurrent pipeline executions on the same issue from
corrupting output files, duplicating Jira artifacts, or racing
on status transitions.
"""

import fcntl
import os
import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

LOCK_DIR = Path("outputs/.locks")


class IssueLockError(RuntimeError):
    """Raised when another process holds the lock for an issue key."""


@contextmanager
def acquire_issue_lock(issue_key: str, lock_dir: Path | None = None) -> Iterator[None]:
    """
    File-based exclusive lock per issue key.

    Uses fcntl.LOCK_EX | LOCK_NB so the call fails immediately
    rather than blocking forever.

    Args:
        issue_key: Jira issue key (e.g. PROJ-123)
        lock_dir: Override lock directory (for testing)

    Raises:
        IssueLockError: If another process holds the lock.

    Yields:
        None — the lock is held for the duration of the context.
    """
    target_dir = lock_dir or LOCK_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    lock_path = target_dir / f"{issue_key}.lock"

    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        raise IssueLockError(
            f"Another pipeline is already running for {issue_key}. "
            f"Wait for it to finish or remove {lock_path} if stale."
        )

    try:
        lock_file.write(f"pid={os.getpid()}\nstarted={datetime.now().isoformat()}\n")
        lock_file.flush()
        yield
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
        lock_path.unlink(missing_ok=True)

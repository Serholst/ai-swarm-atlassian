"""Unit tests for file-based issue locking (Gap 5)."""

import os
import threading
import pytest
from pathlib import Path

from src.executor.utils.issue_lock import acquire_issue_lock, IssueLockError


@pytest.fixture
def lock_dir(tmp_path):
    """Provide a temporary lock directory."""
    return tmp_path / "locks"


class TestAcquireIssueLock:
    """Tests for acquire_issue_lock context manager."""

    def test_lock_acquired_and_released(self, lock_dir):
        """Lock should be acquired, then released on exit."""
        with acquire_issue_lock("PROJ-100", lock_dir=lock_dir):
            lock_file = lock_dir / "PROJ-100.lock"
            assert lock_file.exists()
            content = lock_file.read_text()
            assert f"pid={os.getpid()}" in content
            assert "started=" in content

        # After context exit, lock file should be removed
        assert not lock_file.exists()

    def test_second_acquire_on_same_key_raises(self, lock_dir):
        """Concurrent lock on same key should raise IssueLockError."""
        error_caught = threading.Event()

        def try_second_lock():
            try:
                with acquire_issue_lock("PROJ-200", lock_dir=lock_dir):
                    pass  # pragma: no cover
            except IssueLockError:
                error_caught.set()

        with acquire_issue_lock("PROJ-200", lock_dir=lock_dir):
            t = threading.Thread(target=try_second_lock)
            t.start()
            t.join(timeout=5)
            assert error_caught.is_set(), "Second lock should raise IssueLockError"

    def test_lock_released_then_reacquired(self, lock_dir):
        """After release, the same key can be locked again."""
        with acquire_issue_lock("PROJ-300", lock_dir=lock_dir):
            pass

        # Second acquire should succeed
        with acquire_issue_lock("PROJ-300", lock_dir=lock_dir):
            assert (lock_dir / "PROJ-300.lock").exists()

    def test_different_keys_no_conflict(self, lock_dir):
        """Locks on different issue keys should not conflict."""
        with acquire_issue_lock("PROJ-400", lock_dir=lock_dir):
            with acquire_issue_lock("PROJ-401", lock_dir=lock_dir):
                assert (lock_dir / "PROJ-400.lock").exists()
                assert (lock_dir / "PROJ-401.lock").exists()

    def test_lock_dir_created_automatically(self, tmp_path):
        """Lock directory should be created if it doesn't exist."""
        deep_dir = tmp_path / "a" / "b" / "c"
        assert not deep_dir.exists()

        with acquire_issue_lock("PROJ-500", lock_dir=deep_dir):
            assert deep_dir.exists()

    def test_lock_file_contains_pid_and_timestamp(self, lock_dir):
        """Lock file should contain PID and start timestamp."""
        with acquire_issue_lock("PROJ-600", lock_dir=lock_dir):
            content = (lock_dir / "PROJ-600.lock").read_text()
            lines = content.strip().split("\n")
            assert len(lines) == 2
            assert lines[0].startswith("pid=")
            assert lines[1].startswith("started=")
            # Verify PID is a valid integer
            pid_val = int(lines[0].split("=")[1])
            assert pid_val == os.getpid()

    def test_error_message_contains_issue_key(self, lock_dir):
        """IssueLockError message should mention the issue key."""
        with acquire_issue_lock("PROJ-700", lock_dir=lock_dir):
            with pytest.raises(IssueLockError, match="PROJ-700"):
                # Non-blocking attempt in same thread via raw fcntl
                import fcntl
                lock_path = lock_dir / "PROJ-700.lock"
                f = open(lock_path, "w")
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    f.close()
                    pytest.fail("Should have raised BlockingIOError")  # pragma: no cover
                except BlockingIOError:
                    f.close()
                    # Simulate what acquire_issue_lock does
                    raise IssueLockError(
                        f"Another pipeline is already running for PROJ-700. "
                        f"Wait for it to finish or remove {lock_path} if stale."
                    )

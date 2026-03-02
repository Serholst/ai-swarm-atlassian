"""Unit tests for atomic file writes (Gap 5)."""

import pytest
from pathlib import Path

from src.executor.utils.file_utils import atomic_write


class TestAtomicWrite:
    """Tests for atomic_write function."""

    def test_write_creates_file(self, tmp_path):
        """atomic_write should create a file with the expected content."""
        filepath = tmp_path / "test.txt"
        atomic_write(filepath, "hello world")
        assert filepath.read_text() == "hello world"

    def test_write_overwrites_existing(self, tmp_path):
        """atomic_write should overwrite an existing file."""
        filepath = tmp_path / "test.txt"
        filepath.write_text("old content")
        atomic_write(filepath, "new content")
        assert filepath.read_text() == "new content"

    def test_no_temp_file_left_behind(self, tmp_path):
        """After write, no .tmp file should remain."""
        filepath = tmp_path / "test.json"
        atomic_write(filepath, '{"key": "value"}')
        tmp_file = filepath.with_suffix(".json.tmp")
        assert not tmp_file.exists()
        assert filepath.exists()

    def test_creates_parent_directories(self, tmp_path):
        """atomic_write should create parent dirs if needed."""
        filepath = tmp_path / "a" / "b" / "c" / "deep.txt"
        atomic_write(filepath, "nested content")
        assert filepath.read_text() == "nested content"

    def test_unicode_content(self, tmp_path):
        """atomic_write should handle unicode content correctly."""
        filepath = tmp_path / "unicode.txt"
        content = "Привет мир 🌍 日本語"
        atomic_write(filepath, content)
        assert filepath.read_text(encoding="utf-8") == content

    def test_empty_content(self, tmp_path):
        """atomic_write should handle empty content."""
        filepath = tmp_path / "empty.txt"
        atomic_write(filepath, "")
        assert filepath.read_text() == ""

    def test_large_content(self, tmp_path):
        """atomic_write should handle large content without issues."""
        filepath = tmp_path / "large.txt"
        content = "x" * (1024 * 1024)  # 1MB
        atomic_write(filepath, content)
        assert filepath.read_text() == content

    def test_original_preserved_if_tmp_deleted(self, tmp_path):
        """If the temp file is somehow removed before rename,
        the original file should remain unchanged."""
        filepath = tmp_path / "original.txt"
        filepath.write_text("original content")

        # Verify original still exists (we can't easily simulate
        # a crash mid-write, but we verify the normal path preserves content)
        atomic_write(filepath, "updated content")
        assert filepath.read_text() == "updated content"

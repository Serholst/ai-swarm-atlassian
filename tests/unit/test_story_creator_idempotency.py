"""Unit tests for story creator idempotency and creation report (Gaps 1 + 2)."""

import json
import pytest
from unittest.mock import MagicMock, patch

from src.executor.models.decomposition import DecomposedStory
from src.executor.phases.story_creator import (
    StoryCreationOutcome,
    StoryCreationReport,
    create_jira_stories,
    _load_manifest,
    _save_manifest,
    _get_existing_child_summaries,
)


def _make_story(order: int, layer: str = "BE", title: str = "Test Story") -> DecomposedStory:
    """Helper to create a DecomposedStory."""
    return DecomposedStory(
        layer=layer,
        title=f"{title} {order}",
        description=f"Description for story {order}",
        acceptance=f"Acceptance criteria {order}",
        order=order,
        depends_on=[order - 1] if order > 1 else [],
    )


def _mock_mcp(create_results=None, search_result="Found 0 issues"):
    """Create a mock MCPClientManager."""
    mcp = MagicMock()
    if create_results is None:
        create_results = []
    mcp.jira_create_issue.side_effect = create_results
    mcp.jira_search_issues.return_value = search_result
    mcp.jira_link_issues.return_value = "Link created"
    return mcp


class TestStoryCreationReport:
    """Tests for StoryCreationReport dataclass."""

    def test_created_property(self):
        """created should return only successful outcomes."""
        story1 = _make_story(1)
        story2 = _make_story(2)
        report = StoryCreationReport(
            outcomes=[
                StoryCreationOutcome(story=story1, jira_key="PROJ-10", status="created"),
                StoryCreationOutcome(story=story2, status="failed", error="timeout"),
            ]
        )
        assert len(report.created) == 1
        assert report.created[0] == (story1, "PROJ-10")

    def test_failed_property(self):
        """failed should return only failed outcomes."""
        story = _make_story(1)
        report = StoryCreationReport(
            outcomes=[
                StoryCreationOutcome(story=story, status="failed", error="timeout"),
            ]
        )
        assert len(report.failed) == 1
        assert report.failed[0].error == "timeout"

    def test_skipped_property(self):
        """skipped should return only skipped outcomes."""
        story = _make_story(1)
        report = StoryCreationReport(
            outcomes=[
                StoryCreationOutcome(story=story, jira_key="PROJ-5", status="skipped"),
            ]
        )
        assert len(report.skipped) == 1
        assert report.skipped[0].jira_key == "PROJ-5"

    def test_created_or_skipped(self):
        """created_or_skipped should include both created and skipped."""
        s1 = _make_story(1)
        s2 = _make_story(2)
        s3 = _make_story(3)
        report = StoryCreationReport(
            outcomes=[
                StoryCreationOutcome(story=s1, jira_key="PROJ-10", status="created"),
                StoryCreationOutcome(story=s2, jira_key="PROJ-11", status="skipped"),
                StoryCreationOutcome(story=s3, status="failed", error="err"),
            ]
        )
        existing = report.created_or_skipped
        assert len(existing) == 2


class TestManifest:
    """Tests for manifest load/save."""

    def test_save_and_load_manifest(self, tmp_path):
        """Manifest should round-trip correctly."""
        story = _make_story(1)
        report = StoryCreationReport(
            outcomes=[
                StoryCreationOutcome(story=story, jira_key="PROJ-10", status="created"),
            ]
        )
        path = _save_manifest("PROJ-100", report, output_dir=str(tmp_path))
        assert path.exists()

        loaded = _load_manifest("PROJ-100", output_dir=str(tmp_path))
        assert loaded["parent_key"] == "PROJ-100"
        assert len(loaded["stories"]) == 1
        assert loaded["stories"][0]["jira_key"] == "PROJ-10"
        assert loaded["stories"][0]["status"] == "created"

    def test_load_missing_manifest(self, tmp_path):
        """Loading a non-existent manifest should return empty dict."""
        result = _load_manifest("PROJ-999", output_dir=str(tmp_path))
        assert result == {}

    def test_load_corrupt_manifest(self, tmp_path):
        """Loading a corrupt manifest should return empty dict."""
        issue_dir = tmp_path / "PROJ-888"
        issue_dir.mkdir()
        (issue_dir / "PROJ-888_stories_manifest.json").write_text("not json")
        result = _load_manifest("PROJ-888", output_dir=str(tmp_path))
        assert result == {}

    def test_manifest_records_failed_stories(self, tmp_path):
        """Failed stories should be recorded in the manifest."""
        story = _make_story(1)
        report = StoryCreationReport(
            outcomes=[
                StoryCreationOutcome(
                    story=story, status="failed", error="API timeout"
                ),
            ]
        )
        _save_manifest("PROJ-101", report, output_dir=str(tmp_path))
        loaded = _load_manifest("PROJ-101", output_dir=str(tmp_path))
        assert loaded["stories"][0]["status"] == "failed"
        assert loaded["stories"][0]["error"] == "API timeout"


class TestCreateJiraStoriesIdempotency:
    """Tests for create_jira_stories with duplicate detection."""

    def test_fresh_run_creates_all(self, tmp_path):
        """First run with no existing stories should create all."""
        stories = [_make_story(1), _make_story(2)]
        mcp = _mock_mcp(
            create_results=[
                "Created issue **PROJ-10** successfully",
                "Created issue **PROJ-11** successfully",
            ]
        )
        report = create_jira_stories(
            mcp=mcp,
            parent_key="PROJ-100",
            project_key="PROJ",
            stories=stories,
            config={"jira": {"parent_link_type": "Parent"}},
            output_dir=str(tmp_path),
        )
        assert len(report.created) == 2
        assert len(report.failed) == 0
        assert len(report.skipped) == 0

    def test_rerun_skips_existing_from_manifest(self, tmp_path):
        """Re-run should skip stories that exist in the manifest."""
        stories = [_make_story(1), _make_story(2)]

        # First run
        mcp = _mock_mcp(
            create_results=[
                "Created issue **PROJ-10** successfully",
                "Created issue **PROJ-11** successfully",
            ]
        )
        report1 = create_jira_stories(
            mcp=mcp,
            parent_key="PROJ-100",
            project_key="PROJ",
            stories=stories,
            config={"jira": {"parent_link_type": "Parent"}},
            output_dir=str(tmp_path),
        )
        assert len(report1.created) == 2

        # Re-run — both should be skipped from manifest
        mcp2 = _mock_mcp()
        report2 = create_jira_stories(
            mcp=mcp2,
            parent_key="PROJ-100",
            project_key="PROJ",
            stories=stories,
            config={"jira": {"parent_link_type": "Parent"}},
            output_dir=str(tmp_path),
        )
        assert len(report2.skipped) == 2
        assert len(report2.created) == 0
        # No create calls should have been made
        mcp2.jira_create_issue.assert_not_called()

    def test_partial_failure_rerun_retries_failed(self, tmp_path):
        """Re-run should retry only failed stories from the manifest."""
        stories = [_make_story(1), _make_story(2), _make_story(3)]

        # First run: 1 and 2 succeed, 3 fails
        mcp = _mock_mcp(
            create_results=[
                "Created issue **PROJ-10** successfully",
                "Created issue **PROJ-11** successfully",
                Exception("API timeout"),
            ]
        )
        report1 = create_jira_stories(
            mcp=mcp,
            parent_key="PROJ-100",
            project_key="PROJ",
            stories=stories,
            config={"jira": {"parent_link_type": "Parent"}},
            output_dir=str(tmp_path),
        )
        assert len(report1.created) == 2
        assert len(report1.failed) == 1

        # Re-run: stories 1+2 skipped, story 3 retried
        mcp2 = _mock_mcp(
            create_results=["Created issue **PROJ-12** successfully"]
        )
        report2 = create_jira_stories(
            mcp=mcp2,
            parent_key="PROJ-100",
            project_key="PROJ",
            stories=stories,
            config={"jira": {"parent_link_type": "Parent"}},
            output_dir=str(tmp_path),
        )
        assert len(report2.skipped) == 2
        assert len(report2.created) == 1
        assert report2.created[0][1] == "PROJ-12"

    def test_all_fail_returns_all_failed(self, tmp_path):
        """When all creations fail, all outcomes should be 'failed'."""
        stories = [_make_story(1), _make_story(2)]
        mcp = _mock_mcp(
            create_results=[
                Exception("Timeout"),
                Exception("403 Forbidden"),
            ]
        )
        report = create_jira_stories(
            mcp=mcp,
            parent_key="PROJ-100",
            project_key="PROJ",
            stories=stories,
            config={"jira": {"parent_link_type": "Parent"}},
            output_dir=str(tmp_path),
        )
        assert len(report.failed) == 2
        assert len(report.created) == 0

    def test_manifest_saved_after_creation(self, tmp_path):
        """Manifest should be saved after story creation."""
        stories = [_make_story(1)]
        mcp = _mock_mcp(
            create_results=["Created issue **PROJ-10** successfully"]
        )
        create_jira_stories(
            mcp=mcp,
            parent_key="PROJ-100",
            project_key="PROJ",
            stories=stories,
            config={"jira": {"parent_link_type": "Parent"}},
            output_dir=str(tmp_path),
        )
        manifest = _load_manifest("PROJ-100", output_dir=str(tmp_path))
        assert manifest["parent_key"] == "PROJ-100"
        assert len(manifest["stories"]) == 1


class TestStoryCreationOutcomeManifest:
    """Tests for StoryCreationOutcome.to_manifest_dict."""

    def test_created_outcome_dict(self):
        story = _make_story(1, layer="FE", title="Build form")
        outcome = StoryCreationOutcome(story=story, jira_key="PROJ-10", status="created")
        d = outcome.to_manifest_dict()
        assert d["order"] == 1
        assert d["jira_key"] == "PROJ-10"
        assert d["status"] == "created"
        assert "[FE]" in d["summary"]

    def test_failed_outcome_dict(self):
        story = _make_story(2, layer="QA", title="Write tests")
        outcome = StoryCreationOutcome(story=story, status="failed", error="403")
        d = outcome.to_manifest_dict()
        assert d["jira_key"] is None
        assert d["status"] == "failed"
        assert d["error"] == "403"

"""Unit tests for human_plan_review_handler (--task on Human Plan Review status)."""

import pytest
from unittest.mock import MagicMock, patch

from src.executor.models.decomposition import DecomposedStory
from src.executor.phases.story_creator import (
    StoryCreationOutcome,
    StoryCreationReport,
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


def _mock_mcp():
    """Create a mock MCPClientManager."""
    mcp = MagicMock()
    mcp.jira_transition_issue.return_value = "Transitioned"
    mcp.jira_link_issues.return_value = "Link created"
    return mcp


CONFIG = {
    "jira": {
        "statuses": {
            "ready_for_dev": "Ready for Dev",
        },
        "parent_link_type": "Parent",
    },
}


SC = "executor.phases.story_creator"


@patch("executor.phases.review_handler.get_issue_status", return_value="Ready for Dev")
@patch("executor.phases.review_handler.extract_project_key", return_value="PROJ")
class TestHumanPlanReviewHandler:
    """Tests for human_plan_review_handler."""

    @patch(f"{SC}.create_dependency_links", return_value=0)
    @patch(f"{SC}.create_jira_stories")
    @patch(f"{SC}.extract_stories_from_comment")
    @patch(f"{SC}.ensure_review_blocking_link", return_value=True)
    @patch(f"{SC}.check_review_approved", return_value=(True, "PROJ-50"))
    def test_review_done_creates_stories_and_transitions(
        self,
        mock_review,
        mock_link,
        mock_extract,
        mock_create,
        mock_deps,
        mock_project_key,
        mock_status,
    ):
        """When [PLAN REVIEW] is Done, stories are created and issue transitions."""
        from executor.phases.review_handler import human_plan_review_handler

        stories = [_make_story(1), _make_story(2)]
        mock_extract.return_value = stories
        mock_create.return_value = StoryCreationReport(
            outcomes=[
                StoryCreationOutcome(story=stories[0], jira_key="PROJ-101", status="created"),
                StoryCreationOutcome(story=stories[1], jira_key="PROJ-102", status="created"),
            ]
        )

        mcp = _mock_mcp()
        result = human_plan_review_handler(mcp, "PROJ-10", CONFIG)

        assert result == 0
        mock_create.assert_called_once()
        mcp.jira_transition_issue.assert_called_once_with("PROJ-10", "Ready for Dev")

    @patch(f"{SC}.check_review_approved", return_value=(False, "PROJ-50"))
    def test_review_not_done_informs_user(
        self,
        mock_review,
        mock_project_key,
        mock_status,
    ):
        """When [PLAN REVIEW] is not Done, exit 0 with info message."""
        from executor.phases.review_handler import human_plan_review_handler

        mcp = _mock_mcp()
        result = human_plan_review_handler(mcp, "PROJ-10", CONFIG)

        assert result == 0
        mcp.jira_transition_issue.assert_not_called()
        mcp.jira_create_issue.assert_not_called()

    @patch(f"{SC}.check_review_approved", return_value=(False, None))
    def test_no_review_task_returns_error(
        self,
        mock_review,
        mock_project_key,
        mock_status,
    ):
        """When no [PLAN REVIEW] task found, exit 1."""
        from executor.phases.review_handler import human_plan_review_handler

        mcp = _mock_mcp()
        result = human_plan_review_handler(mcp, "PROJ-10", CONFIG)

        assert result == 1
        mcp.jira_transition_issue.assert_not_called()

    @patch(f"{SC}.create_dependency_links", return_value=0)
    @patch(f"{SC}.create_jira_stories")
    @patch(f"{SC}.extract_stories_from_comment")
    @patch(f"{SC}.ensure_review_blocking_link", return_value=True)
    @patch(f"{SC}.check_review_approved", return_value=(True, "PROJ-50"))
    def test_partial_failure_no_transition(
        self,
        mock_review,
        mock_link,
        mock_extract,
        mock_create,
        mock_deps,
        mock_project_key,
        mock_status,
    ):
        """Partial story failure returns exit 2, no transition."""
        from executor.phases.review_handler import human_plan_review_handler

        stories = [_make_story(1), _make_story(2)]
        mock_extract.return_value = stories
        mock_create.return_value = StoryCreationReport(
            outcomes=[
                StoryCreationOutcome(story=stories[0], jira_key="PROJ-101", status="created"),
                StoryCreationOutcome(story=stories[1], status="failed", error="API timeout"),
            ]
        )

        mcp = _mock_mcp()
        result = human_plan_review_handler(mcp, "PROJ-10", CONFIG)

        assert result == 2
        mcp.jira_transition_issue.assert_not_called()

    @patch(f"{SC}.create_dependency_links", return_value=0)
    @patch(f"{SC}.create_jira_stories")
    @patch(f"{SC}.extract_stories_from_comment")
    @patch(f"{SC}.ensure_review_blocking_link", return_value=True)
    @patch(f"{SC}.check_review_approved", return_value=(True, "PROJ-50"))
    def test_all_skipped_still_transitions(
        self,
        mock_review,
        mock_link,
        mock_extract,
        mock_create,
        mock_deps,
        mock_project_key,
        mock_status,
    ):
        """All stories already exist (skipped) — still transitions to Ready for Dev."""
        from executor.phases.review_handler import human_plan_review_handler

        stories = [_make_story(1), _make_story(2)]
        mock_extract.return_value = stories
        mock_create.return_value = StoryCreationReport(
            outcomes=[
                StoryCreationOutcome(story=stories[0], jira_key="PROJ-101", status="skipped"),
                StoryCreationOutcome(story=stories[1], jira_key="PROJ-102", status="skipped"),
            ]
        )

        mcp = _mock_mcp()
        result = human_plan_review_handler(mcp, "PROJ-10", CONFIG)

        assert result == 0
        mcp.jira_transition_issue.assert_called_once_with("PROJ-10", "Ready for Dev")

    @patch(f"{SC}.create_dependency_links", return_value=0)
    @patch(f"{SC}.create_jira_stories")
    @patch(f"{SC}.extract_stories_from_comment")
    @patch(f"{SC}.ensure_review_blocking_link", return_value=True)
    @patch(f"{SC}.check_review_approved", return_value=(True, "PROJ-50"))
    def test_all_failed_returns_error(
        self,
        mock_review,
        mock_link,
        mock_extract,
        mock_create,
        mock_deps,
        mock_project_key,
        mock_status,
    ):
        """All stories failed returns exit 1."""
        from executor.phases.review_handler import human_plan_review_handler

        stories = [_make_story(1), _make_story(2)]
        mock_extract.return_value = stories
        mock_create.return_value = StoryCreationReport(
            outcomes=[
                StoryCreationOutcome(story=stories[0], status="failed", error="API error"),
                StoryCreationOutcome(story=stories[1], status="failed", error="API error"),
            ]
        )

        mcp = _mock_mcp()
        result = human_plan_review_handler(mcp, "PROJ-10", CONFIG)

        assert result == 1
        mcp.jira_transition_issue.assert_not_called()


class TestCreateStoriesFlagRemoved:
    """Test that --create-stories CLI flag is removed."""

    def test_create_stories_flag_rejected(self):
        """Argparse should reject --create-stories."""
        from unittest.mock import patch as _patch
        import execute

        with pytest.raises(SystemExit) as exc_info:
            with _patch(
                "sys.argv",
                ["execute.py", "--create-stories", "PROJ-123"],
            ):
                execute.main()

        assert exc_info.value.code == 2  # argparse error

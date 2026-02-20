"""
Heuristic confidence scoring for decomposed stories.

Scores each story 0.0–1.0 based on data availability signals
without requiring an additional LLM call.
"""

import re
import logging
from typing import Optional

from ..models.decomposition import DecomposedStory
from ..models.execution_context import ExecutionContext
from ..phases.validation import VAGUE_PATTERNS

logger = logging.getLogger(__name__)

# Default threshold (can be overridden via config agent.min_confidence)
DEFAULT_CONFIDENCE_THRESHOLD = 0.7


def score_story_confidence(
    story: DecomposedStory,
    execution_context: Optional[ExecutionContext] = None,
) -> tuple[float, list[str]]:
    """
    Score confidence for a single story based on heuristic signals.

    Scoring breakdown (max 1.0):
    - Has non-empty Files field: +0.2
    - Has specific acceptance criteria (not vague): +0.2
    - Layer is not GEN: +0.1
    - Files reference paths found in GitHub context tree: +0.2
    - Has related Confluence documentation: +0.15
    - Title is specific (>5 significant words): +0.15

    Args:
        story: DecomposedStory to score
        execution_context: Optional context for cross-referencing

    Returns:
        Tuple of (score, list_of_flags_explaining_deductions)
    """
    score = 0.0
    flags: list[str] = []

    # Signal 1: Has non-empty Files field (+0.2)
    if story.files and any(f.strip() for f in story.files):
        score += 0.2
    else:
        flags.append("No files specified")

    # Signal 2: Has specific acceptance criteria (+0.2)
    if story.acceptance and story.acceptance.strip():
        vague = False
        compiled = [re.compile(p, re.IGNORECASE) for p in VAGUE_PATTERNS]
        for pattern in compiled:
            if pattern.search(story.acceptance):
                vague = True
                break
        if vague:
            flags.append("Vague acceptance criteria")
        else:
            score += 0.2
    else:
        flags.append("No acceptance criteria")

    # Signal 3: Layer is not GEN (+0.1)
    if story.layer != "GEN":
        score += 0.1
    else:
        flags.append("Generic layer (GEN)")

    # Signal 4: Files match GitHub context (+0.2)
    if execution_context and execution_context.github and execution_context.github.is_available():
        github_tree = execution_context.github.structure.tree if execution_context.github.structure else ""
        if story.files and github_tree:
            matched = 0
            for file_path in story.files:
                # Extract filename or key path component
                basename = file_path.strip().split("/")[-1] if "/" in file_path else file_path.strip()
                if basename and basename in github_tree:
                    matched += 1
            if matched > 0:
                score += 0.2
            else:
                flags.append("Files not found in repository tree")
        else:
            flags.append("Cannot verify files against repository")
    else:
        # No GitHub context — give partial credit since we can't verify
        score += 0.1
        if not execution_context or not execution_context.github:
            flags.append("No GitHub context available (partial credit)")

    # Signal 5: Related Confluence documentation (+0.15)
    if execution_context and execution_context.refined_confluence:
        rc = execution_context.refined_confluence
        all_docs = rc.core_documents + rc.supporting_documents
        if all_docs:
            score += 0.15
        else:
            flags.append("No Confluence documentation available")
    elif execution_context and execution_context.confluence:
        score += 0.15
    else:
        flags.append("No Confluence documentation available")

    # Signal 6: Title is specific — >5 significant words (+0.15)
    stopwords = {"the", "a", "an", "and", "or", "for", "to", "in", "of", "with", "on", "is", "are"}
    title_words = [w for w in re.findall(r"\w+", story.title.lower()) if w not in stopwords]
    if len(title_words) > 5:
        score += 0.15
    elif len(title_words) > 3:
        score += 0.08
        flags.append("Title could be more specific")
    else:
        flags.append("Title is too generic")

    # Clamp to [0.0, 1.0]
    score = min(1.0, max(0.0, round(score, 2)))

    return score, flags


def score_overall_confidence(stories: list[DecomposedStory]) -> float:
    """
    Compute overall confidence as weighted average of story confidences.

    Args:
        stories: List of scored DecomposedStory objects

    Returns:
        Overall confidence score (0.0–1.0)
    """
    if not stories:
        return 0.0

    total = sum(s.confidence for s in stories)
    return round(total / len(stories), 2)


def flag_low_confidence(
    stories: list[DecomposedStory],
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> list[int]:
    """
    Return story orders that are below the confidence threshold.

    Args:
        stories: List of scored DecomposedStory objects
        threshold: Minimum acceptable confidence

    Returns:
        List of story order numbers below threshold
    """
    return [s.order for s in stories if s.confidence < threshold]

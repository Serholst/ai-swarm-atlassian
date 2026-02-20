"""
Data models for Analysis & Decomposition stage.

Defines structures for:
- Decomposed Stories with [LAYER] taxonomy
- Clarification Questions (optional)
- Chain of Thoughts content
- Overall decomposition result
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DecomposedStory:
    """
    A story extracted from LLM work plan.

    Stories are documented but NOT created as Jira issues until
    architecture is approved.
    """
    layer: str          # BE, FE, INFRA, DB, QA, DOCS, GEN
    title: str          # Story title
    description: str    # Technical specification
    acceptance: str     # Acceptance criteria
    files: list[str] = field(default_factory=list)  # Expected files to modify/create
    order: int = 0      # Sequence in work plan
    depends_on: list[int] = field(default_factory=list)  # Step numbers this depends on
    confidence: float = 0.0  # Heuristic confidence score (0.0–1.0)
    confidence_flags: list[str] = field(default_factory=list)  # Reasons for low confidence


@dataclass
class ClarificationQuestion:
    """
    A question requiring human input.

    Only created if LLM has unresolved questions in concerns section.
    """
    question: str                   # The question text
    context: str                    # Why this needs clarification
    related_story: Optional[str] = None  # Story title if tied to specific story


@dataclass
class DecompositionResult:
    """
    Result of parsing LLM response into decomposition artifacts.

    Used to create Jira artifacts:
    - Blocking review Story
    - Technical Decomposition comment
    - Executor Rationale (CoT) comment
    - Clarification Questions comment (optional)
    """
    # Technical decomposition
    stories: list[DecomposedStory] = field(default_factory=list)

    # Clarifications (empty list if no questions)
    questions: list[ClarificationQuestion] = field(default_factory=list)

    # Chain of Thoughts fields (from LLMResponse)
    cot_context: str = ""       # From understanding - task situation
    cot_decision: str = ""      # From analysis - chosen approach
    cot_alternatives: str = ""  # From analysis - discarded options (may be empty)

    # Metadata
    complexity: str = "M"       # S, M, L, XL
    feature_title: str = ""     # Feature summary for context

    # Confidence scoring
    overall_confidence: float = 0.0  # Average story confidence (0.0–1.0)
    low_confidence_stories: list[int] = field(default_factory=list)  # Story orders below threshold

    # Created Jira artifact keys
    review_task_key: Optional[str] = None  # Key of created review Story

    def has_questions(self) -> bool:
        """Check if there are clarification questions."""
        return len(self.questions) > 0

    def has_stories(self) -> bool:
        """Check if stories were extracted."""
        return len(self.stories) > 0

    def get_stories_by_layer(self) -> dict[str, list[DecomposedStory]]:
        """Group stories by layer for display."""
        by_layer: dict[str, list[DecomposedStory]] = {}
        for story in self.stories:
            if story.layer not in by_layer:
                by_layer[story.layer] = []
            by_layer[story.layer].append(story)
        return by_layer

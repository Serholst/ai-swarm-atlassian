"""
LLM Executor - Stage 5 of the execution pipeline.

Handles:
- Prompt construction and saving
- DeepSeek API call
- Response parsing and saving
- Output file generation
"""

import os
import re
import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI

from ..models.execution_context import ExecutionContext
from ..prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Response from LLM execution."""

    # Raw response
    raw_content: str

    # Parsed sections
    understanding: str = ""
    concerns: str = ""
    analysis: str = ""
    work_plan: str = ""
    definition_of_ready: str = ""

    # Metadata
    model: str = ""
    tokens_used: int = 0
    finish_reason: str = ""


@dataclass
class ExecutionOutput:
    """Output files from execution."""

    context_file: Path
    prompt_file: Path
    reasoning_file: Path
    plan_file: Path
    selection_file: Optional[Path] = None  # LLM document selection log


class LLMExecutor:
    """Stage 5: LLM Execution with DeepSeek API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "deepseek-chat",
        api_base: str = "https://api.deepseek.com/v1",
        temperature: float = 0.2,
        max_tokens: int = 8192,
        output_dir: str = "outputs",
    ):
        """
        Initialize LLM Executor.

        Args:
            api_key: DeepSeek API key (defaults to DEEPSEEK_API_KEY env var)
            model: Model name (deepseek-chat or deepseek-coder)
            api_base: API base URL
            temperature: Sampling temperature
            max_tokens: Maximum response tokens
            output_dir: Directory for output files
        """
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY not found in environment")

        self.model = model
        self.api_base = api_base
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.output_dir = Path(output_dir)

        # Initialize OpenAI client with DeepSeek endpoint
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
        )

        logger.info(f"LLM Executor initialized: model={model}, base={api_base}")

    def execute(self, context: ExecutionContext) -> tuple[LLMResponse, ExecutionOutput]:
        """
        Execute Stage 5: LLM call and output generation.

        Args:
            context: ExecutionContext from Stage 4

        Returns:
            Tuple of (LLMResponse, ExecutionOutput)
        """
        issue_key = context.issue_key
        logger.info(f"Stage 5: Executing LLM for {issue_key}")

        # Create output directory
        issue_dir = self.output_dir / issue_key
        issue_dir.mkdir(parents=True, exist_ok=True)

        # Build prompts
        user_prompt = build_user_prompt(context)

        # Step 1: Save context
        context_file = self._save_context(issue_dir, context)
        logger.info(f"Saved context: {context_file}")

        # Step 1.5: Save selection log (if available from Stage 3)
        selection_file = self._save_selection(issue_dir, context)
        if selection_file:
            logger.info(f"Saved selection: {selection_file}")

        # Step 2: Save prompt BEFORE LLM call
        prompt_file = self._save_prompt(issue_dir, context, user_prompt)
        logger.info(f"Saved prompt: {prompt_file}")

        # Step 3: Call DeepSeek API
        logger.info(f"Calling DeepSeek API ({self.model})...")
        response = self._call_llm(user_prompt)
        logger.info(f"LLM response received: {response.tokens_used} tokens, {response.finish_reason}")

        # Step 4: Save reasoning (full response)
        reasoning_file = self._save_reasoning(issue_dir, context, response)
        logger.info(f"Saved reasoning: {reasoning_file}")

        # Step 5: Extract and save work plan
        plan_file = self._save_plan(issue_dir, context, response)
        logger.info(f"Saved plan: {plan_file}")

        output = ExecutionOutput(
            context_file=context_file,
            prompt_file=prompt_file,
            reasoning_file=reasoning_file,
            plan_file=plan_file,
            selection_file=selection_file,
        )

        logger.info(f"Stage 5 complete: {issue_key}")
        return response, output

    def _call_llm(self, user_prompt: str) -> LLMResponse:
        """Call DeepSeek API and return parsed response."""
        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

            raw_content = completion.choices[0].message.content or ""
            tokens_used = completion.usage.total_tokens if completion.usage else 0
            finish_reason = completion.choices[0].finish_reason or ""

            response = LLMResponse(
                raw_content=raw_content,
                model=self.model,
                tokens_used=tokens_used,
                finish_reason=finish_reason,
            )

            # Parse sections from response
            self._parse_response_sections(response)

            return response

        except Exception as e:
            logger.error(f"LLM API call failed: {e}")
            raise

    def _parse_response_sections(self, response: LLMResponse) -> None:
        """Parse response into sections."""
        content = response.raw_content

        # Extract sections using headers
        sections = {
            "understanding": r"###?\s*1\.\s*Understanding.*?\n(.*?)(?=###?\s*2\.|$)",
            "concerns": r"###?\s*2\.\s*Concerns.*?\n(.*?)(?=###?\s*3\.|$)",
            "analysis": r"###?\s*3\.\s*Analysis.*?\n(.*?)(?=###?\s*4\.|$)",
            "work_plan": r"###?\s*4\.\s*Work Plan.*?\n(.*?)(?=###?\s*5\.|$)",
            "definition_of_ready": r"###?\s*5\.\s*Definition of Ready.*?\n(.*?)(?=$)",
        }

        for field, pattern in sections.items():
            match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
            if match:
                setattr(response, field, match.group(1).strip())

    def _save_context(self, issue_dir: Path, context: ExecutionContext) -> Path:
        """Save raw context to file."""
        filepath = issue_dir / f"{context.issue_key}_context.md"

        content = f"""# Context for {context.issue_key}

Generated: {context.timestamp.isoformat()}

---

{context.build_prompt_context()}
"""
        filepath.write_text(content, encoding="utf-8")
        return filepath

    def _save_prompt(self, issue_dir: Path, context: ExecutionContext, user_prompt: str) -> Path:
        """Save full prompt to file BEFORE LLM call."""
        filepath = issue_dir / f"{context.issue_key}_prompt.md"

        content = f"""# LLM Prompt for {context.issue_key}

Generated: {datetime.now().isoformat()}
Model: {self.model}
Temperature: {self.temperature}
Max Tokens: {self.max_tokens}

---

## System Prompt

```
{SYSTEM_PROMPT}
```

---

## User Prompt

{user_prompt}
"""
        filepath.write_text(content, encoding="utf-8")
        return filepath

    def _save_reasoning(self, issue_dir: Path, context: ExecutionContext, response: LLMResponse) -> Path:
        """Save full LLM response with metadata."""
        filepath = issue_dir / f"{context.issue_key}_reasoning.md"

        content = f"""# Agent Reasoning for {context.issue_key}

Generated: {datetime.now().isoformat()}
Model: {response.model}
Tokens Used: {response.tokens_used}
Finish Reason: {response.finish_reason}

---

{response.raw_content}
"""
        filepath.write_text(content, encoding="utf-8")
        return filepath

    def _save_plan(self, issue_dir: Path, context: ExecutionContext, response: LLMResponse) -> Path:
        """Extract and save work plan section."""
        filepath = issue_dir / f"{context.issue_key}_plan.md"

        # Build plan document
        summary = context.jira.summary if context.jira else context.issue_key

        content = f"""# Work Plan: {context.issue_key}

**Task:** {summary}
**Generated:** {datetime.now().isoformat()}
**Model:** {response.model}

---

## Understanding

{response.understanding or '[Section not found in response]'}

---

## Concerns & Uncertainties

{response.concerns or '[Section not found in response]'}

---

## Analysis

{response.analysis or '[Section not found in response]'}

---

## Steps

{response.work_plan or '[Section not found in response]'}

---

## Definition of Ready

{response.definition_of_ready or '[Section not found in response]'}
"""
        filepath.write_text(content, encoding="utf-8")
        return filepath

    def _save_selection(self, issue_dir: Path, context: ExecutionContext) -> Optional[Path]:
        """Save LLM document selection log if available."""
        if not context.refined_confluence or not context.refined_confluence.selection_log:
            return None

        selection_log = context.refined_confluence.selection_log
        filepath = issue_dir / f"{context.issue_key}_selection.md"

        content = f"""# Document Selection Log: {context.issue_key}

**Generated:** {datetime.now().isoformat()}
**Model:** {selection_log.model}
**Tokens Used:** {selection_log.tokens_used}

---

{selection_log.format_markdown()}
"""
        filepath.write_text(content, encoding="utf-8")
        return filepath


def execute_llm_pipeline(
    context: ExecutionContext,
    api_key: Optional[str] = None,
    model: str = "deepseek-chat",
    output_dir: str = "outputs",
) -> tuple[LLMResponse, ExecutionOutput]:
    """
    Convenience function to execute Stage 5.

    Args:
        context: ExecutionContext from Stage 4
        api_key: DeepSeek API key (optional, uses env var)
        model: Model name
        output_dir: Output directory

    Returns:
        Tuple of (LLMResponse, ExecutionOutput)
    """
    executor = LLMExecutor(
        api_key=api_key,
        model=model,
        output_dir=output_dir,
    )
    return executor.execute(context)

"""CLI entry point for AI-Swarm executor."""
import argparse
import json
import sys
from pathlib import Path

# Add src to path if running directly
# src/executor/cli/main.py -> parents: cli, executor, src, AI-swarm
# We need AI-swarm/src on the path
_project_src = Path(__file__).parent.parent.parent  # goes up to src/
if str(_project_src) not in sys.path:
    sys.path.insert(0, str(_project_src))

from rich.console import Console
import executor.cli.shared as _shared
from executor.cli.pipeline_cmd import execute_pipeline
from executor.cli.phase_zero_cmd import phase_zero_pipeline
from executor.cli.refine_cmd import refinement_pipeline
from executor.phases import parse_issue_key
from executor.utils.issue_lock import acquire_issue_lock, IssueLockError
from executor.utils.structured_logging import setup_structured_logging


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AI-SWARM Executor - 5-Stage SDLC Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 execute.py --phase0 PROJ-123             # Phase 0: Backlog Analysis
  python3 execute.py --phase0 PROJ-123 --dry-run   # Phase 0: skip Jira comment
  python3 execute.py --task PROJ-123               # Full pipeline (Stages 1-5)
  python3 execute.py --task PROJ-123 --dry-run
  python3 execute.py --task PROJ-123 --json-logs
  python3 execute.py --refine PROJ-123 --feedback "Split step 3 into BE and FE"
  python3 execute.py -t PROJ-123 -o ./my_outputs
  python3 execute.py --task PROJ-123 --output-format json

Phases:
  0. Phase 0       - Backlog Analysis (Use Case + DoR)

Stages:
  1. Trigger       - Parse Jira issue key
  2. Enrichment    - Extract Jira context
  3. Knowledge     - Retrieve Confluence data
  4. Aggregation   - Build unified context
  5. Execution     - Generate plan via DeepSeek LLM
        """
    )

    parser.add_argument("--task", "-t", help="Jira issue key or URL (for full pipeline)")
    parser.add_argument("--phase0", metavar="ISSUE_KEY", help="Run Phase 0: Backlog Analysis")
    parser.add_argument("--refine", metavar="ISSUE_KEY", help="Refine a previous plan with human feedback")
    parser.add_argument("--feedback", help="Feedback text for --refine mode (required with --refine)")
    parser.add_argument("--dry-run", "-d", action="store_true", help="Skip LLM execution")
    parser.add_argument("--output-dir", "-o", default="outputs", help="Output directory (default: outputs)")
    parser.add_argument("--json-logs", action="store_true", help="Use JSON structured logging")
    parser.add_argument("--force", "-f", action="store_true", help="Force full pipeline re-execution")
    parser.add_argument(
        "--output-format",
        choices=["text", "json"],
        default="text",
        help="Output format: 'text' (default, rich UI) or 'json' (machine-readable, stdout)",
    )

    args = parser.parse_args()

    # In JSON mode redirect rich console to stderr so stdout is clean for JSON
    if args.output_format == "json":
        _shared.console = Console(stderr=True)

    if args.json_logs:
        setup_structured_logging(level="INFO", json_output=True)

    # Dispatch
    if args.phase0:
        try:
            issue_key = parse_issue_key(args.phase0)
            with acquire_issue_lock(issue_key):
                return phase_zero_pipeline(
                    task_input=args.phase0,
                    dry_run=args.dry_run,
                    output_dir=args.output_dir,
                )
        except IssueLockError as e:
            _shared.console.print(f"[red]Error: {e}[/red]")
            return 1
        except ValueError as e:
            _shared.console.print(f"[red]Error: {e}[/red]")
            return 1
        except KeyboardInterrupt:
            _shared.console.print("\n[yellow]Cancelled by user[/yellow]")
            return 130

    if args.refine:
        if not args.feedback:
            parser.error("--feedback is required with --refine")
        try:
            issue_key = parse_issue_key(args.refine)
            with acquire_issue_lock(issue_key):
                return refinement_pipeline(
                    task_input=args.refine,
                    feedback=args.feedback,
                    output_dir=args.output_dir,
                    json_logs=args.json_logs,
                )
        except IssueLockError as e:
            _shared.console.print(f"[red]Error: {e}[/red]")
            return 1
        except ValueError as e:
            _shared.console.print(f"[red]Error: {e}[/red]")
            return 1
        except KeyboardInterrupt:
            _shared.console.print("\n[yellow]Cancelled by user[/yellow]")
            return 130

    if not args.task:
        parser.error("--task is required (or use --phase0 / --refine)")

    try:
        issue_key = parse_issue_key(args.task)
        with acquire_issue_lock(issue_key):
            exit_code, result = execute_pipeline(
                task_input=args.task,
                dry_run=args.dry_run,
                output_dir=args.output_dir,
                json_logs=args.json_logs,
                force=args.force,
            )
            if args.output_format == "json":
                sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            return exit_code
    except IssueLockError as e:
        _shared.console.print(f"[red]Error: {e}[/red]")
        return 1
    except ValueError as e:
        _shared.console.print(f"[red]Error: {e}[/red]")
        return 1
    except KeyboardInterrupt:
        _shared.console.print("\n[yellow]Execution cancelled by user[/yellow]")
        return 130


# For pyproject.toml entry_point: orchestrator = "executor.cli.main:app"
def app() -> None:
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())

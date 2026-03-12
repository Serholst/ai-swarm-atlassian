#!/usr/bin/env python3
"""
AI-Swarm MCP Server

Exposes the AI-Swarm pipeline as MCP tools so any AI agent (Claude, GPT, Gemini, etc.)
can use AI-Swarm as a building block via the Model Context Protocol.

Usage:
    python src/executor/mcp/pipeline_server.py

Add to your MCP client config:
    {
      "mcpServers": {
        "ai-swarm": {
          "command": "python",
          "args": ["src/executor/mcp/pipeline_server.py"],
          "cwd": "/path/to/AI-swarm"
        }
      }
    }

Available tools:
    run_pipeline   — Full 5-stage pipeline: Jira issue → Work Plan
    run_phase0     — Phase 0: Backlog Analysis (Use Cases + DoR)
    refine_plan    — Refine a previous plan with feedback
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Ensure project root is on the path
_project_root = Path(__file__).parent.parent.parent.parent  # mcp -> executor -> src -> AI-swarm
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp import stdio_server

server = Server("ai-swarm")

_EXECUTE_PY = str(_project_root / "execute.py")
_PYTHON = sys.executable


def _run_execute(args: list[str], cwd: str | None = None) -> dict:
    """
    Run execute.py with the given args and --output-format json.
    Returns the parsed JSON result dict, or an error dict on failure.
    """
    cmd = [_PYTHON, _EXECUTE_PY, "--output-format", "json"] + args
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd or str(_project_root),
            timeout=300,  # 5 minutes max
        )
        # stdout has the JSON line; stderr has the rich UI (ignored)
        stdout = proc.stdout.strip()
        if stdout:
            # Find the last JSON object in stdout (in case of extra output)
            for line in reversed(stdout.splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    return json.loads(line)
        # Fallback: return error with stderr snippet
        return {
            "status": "error",
            "error": proc.stderr[-2000:] if proc.stderr else "No output from pipeline",
            "exit_code": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "Pipeline timed out after 300 seconds"}
    except json.JSONDecodeError as e:
        return {"status": "error", "error": f"Failed to parse pipeline output: {e}", "raw": stdout}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="run_pipeline",
            description=(
                "Run the full AI-Swarm 5-stage pipeline on a Jira issue. "
                "Fetches Jira context, Confluence knowledge, GitHub context, "
                "then generates a detailed work plan via DeepSeek LLM. "
                "Returns a structured JSON with the work plan, extracted stories, "
                "confidence score, and output file paths."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Jira issue key, e.g. PROJ-123",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, skip LLM call (Stages 1-4 only). Default: false.",
                        "default": False,
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Bypass pre-flight artifact check. Default: false.",
                        "default": False,
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Output directory. Default: 'outputs'.",
                        "default": "outputs",
                    },
                },
                "required": ["issue_key"],
            },
        ),
        Tool(
            name="run_phase0",
            description=(
                "Run Phase 0: Backlog Analysis on a Jira issue. "
                "Generates Use Cases and Definition of Ready (DoR) with clarification questions. "
                "Writes structured analysis into the Jira description. "
                "If assignee feedback exists from a previous Phase 0, auto-runs Phase 0.5 "
                "(feedback incorporation + DoR re-evaluation)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Jira issue key, e.g. PROJ-123",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, skip writing back to Jira. Default: false.",
                        "default": False,
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Output directory. Default: 'outputs'.",
                        "default": "outputs",
                    },
                },
                "required": ["issue_key"],
            },
        ),
        Tool(
            name="refine_plan",
            description=(
                "Refine a previously generated work plan using human feedback. "
                "Re-runs Stage 5 (LLM) with the original context plus feedback. "
                "Requires a previous pipeline run for the issue (context_store must exist)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Jira issue key, e.g. PROJ-123",
                    },
                    "feedback": {
                        "type": "string",
                        "description": "Human feedback text, e.g. 'Split step 3 into BE and FE tasks'",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Output directory. Default: 'outputs'.",
                        "default": "outputs",
                    },
                },
                "required": ["issue_key", "feedback"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "run_pipeline":
        issue_key = arguments["issue_key"]
        args = ["--task", issue_key]
        if arguments.get("dry_run"):
            args.append("--dry-run")
        if arguments.get("force"):
            args.append("--force")
        if arguments.get("output_dir"):
            args += ["--output-dir", arguments["output_dir"]]
        result = _run_execute(args)

    elif name == "run_phase0":
        issue_key = arguments["issue_key"]
        args = ["--phase0", issue_key]
        if arguments.get("dry_run"):
            args.append("--dry-run")
        if arguments.get("output_dir"):
            args += ["--output-dir", arguments["output_dir"]]
        result = _run_execute(args)

    elif name == "refine_plan":
        issue_key = arguments["issue_key"]
        feedback = arguments["feedback"]
        args = ["--refine", issue_key, "--feedback", feedback]
        if arguments.get("output_dir"):
            args += ["--output-dir", arguments["output_dir"]]
        result = _run_execute(args)

    else:
        result = {"status": "error", "error": f"Unknown tool: {name}"}

    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

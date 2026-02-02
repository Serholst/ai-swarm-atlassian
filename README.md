# AI-Swarm

AI-driven SDLC Orchestrator — a multi-agent system that automates software development lifecycle phases.

## What is AI-Swarm?

AI-Swarm is an **Executor Agent** that orchestrates AI agents to automate analysis, planning, and implementation phases of software development. It follows a strict workflow protocol and integrates with enterprise tools.

### Core Principles

1. **SSOT (Single Source of Truth)**
   - Documentation system = State (Architecture, Contracts, Logic)
   - Version control = Implementation (Code)
   - Task tracker = Flow (Process management)

2. **Spec-First Development**
   - No code execution without approved architectural specification
   - Human review gates between phases

3. **Clean Data Pipeline**
   - Custom MCP servers with data cleaning at source
   - Prevents "garbage in, garbage out"

4. **Transparent Agent Identity**
   - All agent actions are traceable
   - Clear audit trail in all systems

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     CLI Entry Point                         │
│                  ./execute.py --task ISSUE-123              │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                   Executor Agent Core                       │
│              ┌──────────────────────────┐                   │
│              │   State Machine Engine   │                   │
│              │   (Phase Router)         │                   │
│              └──────────────────────────┘                   │
└─────────────────────────┬───────────────────────────────────┘
                          │
         ┌────────────────┼────────────────┐
         ▼                ▼                ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│   Phase 1   │  │   Phase 2   │  │   Phase 4   │
│   Context   │  │  Planning   │  │    Code     │
│   Loading   │  │ (WRITE-LOCK)│  │ Generation  │
└─────────────┘  └─────────────┘  └─────────────┘
         │                │                │
         └────────────────┼────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              MCP Integration Layer                          │
│    ┌──────────────────┐      ┌──────────────────┐          │
│    │ Task Tracker MCP │      │  Documentation   │          │
│    │  (Data Cleaning) │      │   MCP Server     │          │
│    └──────────────────┘      └──────────────────┘          │
└─────────────────────────────────────────────────────────────┘
```

## Workflow Phases

| Phase | Name | Description |
|-------|------|-------------|
| 1 | Context Loading | Validate requirements, load project context |
| 2 | Planning | Generate implementation plan (READ-ONLY mode) |
| 3 | Human Review | Human approves or rejects the plan |
| 4 | Implementation | Execute approved plan, generate code |
| 5 | Finalization | Code review, merge, close tasks |

## Project Structure

```
AI-swarm/
├── config/                  # Workflow configuration
├── src/executor/
│   ├── core/               # State machine & phase router
│   ├── phases/             # Phase handlers
│   ├── mcp/
│   │   ├── client.py       # MCP client manager
│   │   └── servers/        # Custom MCP servers
│   ├── models/             # Data models (Pydantic)
│   └── utils/              # Utilities (cleaning, formatting)
└── tests/                  # Test suite
```

## Key Components

### MCP Servers

Custom Model Context Protocol servers that provide:
- **Data Cleaning**: Convert rich formats (HTML, ADF) to clean Markdown
- **Structured Output**: Type-safe data models
- **Tool Interface**: Standardized tools for agent interaction

### State Machine

Manages workflow state and phase transitions:
- Detects current phase from task status
- Routes to appropriate phase handler
- Enforces quality gates between phases

### Phase Handlers

Modular handlers for each workflow phase:
- Context gathering and validation
- Plan generation with AI
- Code generation and PR creation
- Review and finalization

## Getting Started

See [QUICKSTART.md](QUICKSTART.md) for setup instructions.

## Development Status

**Current: MVP v0.1.0**
- ✅ Custom MCP servers
- ✅ Data models and validation
- ✅ CLI interface
- ✅ Integration tests
- 🚧 State machine engine
- 🚧 Phase handlers
- 📋 Full workflow automation

## License

MIT

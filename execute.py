#!/usr/bin/env python3
"""
AI-SWARM Executor - thin CLI wrapper.

The actual implementation lives in src/executor/cli/.
This file exists for backwards compatibility: `python execute.py --task PROJ-123`
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from executor.cli.main import main

if __name__ == "__main__":
    sys.exit(main())

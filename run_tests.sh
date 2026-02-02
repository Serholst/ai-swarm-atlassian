#!/bin/bash

# Test runner for AI SDLC Executor
# This script runs the MCP integration tests

set -e  # Exit on error

echo "🚀 AI SDLC Executor - Test Runner"
echo "=================================="
echo ""

# Check if virtual environment is activated
if [ -z "$VIRTUAL_ENV" ]; then
    echo "⚠️  Warning: Virtual environment not activated"
    echo ""
    echo "It's recommended to activate the virtual environment first:"
    echo "  source venv/bin/activate"
    echo ""
    read -p "Continue anyway? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Exiting. Please activate venv and try again."
        exit 1
    fi
    echo ""
fi

# Check if .env exists
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found"
    echo ""
    echo "Please create .env file with your credentials:"
    echo "  cp .env.example .env"
    echo "  # Edit .env with your Jira/Confluence credentials"
    echo ""
    exit 1
fi

# Check if dependencies are installed
if ! python -c "import pydantic" 2>/dev/null; then
    echo "⚠️  Warning: Dependencies not installed"
    echo ""
    echo "Installing dependencies..."
    pip install -r requirements.txt || {
        echo "❌ Failed to install dependencies"
        exit 1
    }
    echo "✅ Dependencies installed"
    echo ""
fi

# Run integration tests
echo "Running MCP Integration Tests..."
echo ""

if [ -z "$1" ]; then
    # Run all tests
    python tests/test_mcp_integration.py
else
    # Run tests for specific issue
    echo "Testing specific issue: $1"
    python tests/test_mcp_integration.py "$1"
fi

EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ All tests passed!"
else
    echo "❌ Some tests failed (exit code: $EXIT_CODE)"
fi

exit $EXIT_CODE

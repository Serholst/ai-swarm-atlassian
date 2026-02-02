#!/bin/bash

# Setup script for AI SDLC Executor
# This script creates a virtual environment and installs dependencies

set -e  # Exit on error

echo "🚀 AI SDLC Executor - Setup Script"
echo "===================================="
echo ""

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
REQUIRED_VERSION="3.11"

echo "📋 Checking Python version..."
if python3 -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)"; then
    echo "✅ Python $PYTHON_VERSION detected"
else
    echo "❌ Error: Python 3.11+ required (found: $PYTHON_VERSION)"
    echo ""
    echo "Install Python 3.11+:"
    echo "  - macOS: brew install python@3.11"
    echo "  - Ubuntu: sudo apt install python3.11"
    echo ""
    exit 1
fi

echo ""
echo "📦 Creating virtual environment..."

# Create venv if it doesn't exist
if [ -d "venv" ]; then
    echo "⚠️  Virtual environment already exists (venv/)"
    read -p "   Delete and recreate? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "   Deleting existing venv..."
        rm -rf venv
    else
        echo "   Using existing venv..."
    fi
fi

if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✅ Virtual environment created (venv/)"
else
    echo "✅ Using existing virtual environment"
fi

echo ""
echo "🔧 Activating virtual environment..."

# Activate venv
source venv/bin/activate

echo "✅ Virtual environment activated"
echo ""
echo "📥 Installing dependencies..."

# Upgrade pip
pip install --upgrade pip setuptools wheel

# Install requirements
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    echo "✅ Dependencies installed from requirements.txt"
else
    echo "❌ Error: requirements.txt not found"
    exit 1
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📝 Next Steps:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "1. Activate virtual environment:"
echo "   $ source venv/bin/activate"
echo ""
echo "2. Configure credentials (.env file):"
echo "   $ cp .env.example .env"
echo "   $ nano .env  # Add bot account credentials"
echo ""
echo "   ⚠️  IMPORTANT: Create bot account first!"
echo "   📖 See: docs/BOT_ACCOUNT_SETUP.md"
echo ""
echo "3. Run integration tests:"
echo "   $ ./run_tests.sh"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "💡 Tips:"
echo "  - Always activate venv before working: source venv/bin/activate"
echo "  - Deactivate when done: deactivate"
echo "  - Check if active: which python (should show venv/bin/python)"
echo ""

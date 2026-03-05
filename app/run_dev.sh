#!/usr/bin/env bash
#
# Quick-start: Run the monitor directly without building a .app bundle.
# Great for development or if py2app gives you trouble.
#
# Usage:
#   chmod +x run_dev.sh
#   ./run_dev.sh
#

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# Create venv if needed
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip > /dev/null 2>&1
    pip install -r "$SCRIPT_DIR/requirements.txt" 2>&1 | tail -3
else
    source "$VENV_DIR/bin/activate"
fi

echo "Starting Claude Usage Monitor..."
python3 "$SCRIPT_DIR/claude_usage_monitor.py"

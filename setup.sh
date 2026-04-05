#!/usr/bin/env bash
set -e

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║     BTC Sessions - Video Planner     ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# Detect OS
OS="$(uname -s)"
case "$OS" in
    Darwin) PLATFORM="macOS" ;;
    Linux)  PLATFORM="Linux" ;;
    *)      PLATFORM="$OS" ;;
esac
echo "  Platform: $PLATFORM"

# Check Python 3.9+
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VERSION=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
        MAJOR=$("$cmd" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
        MINOR=$("$cmd" -c "import sys; print(sys.version_info.minor)" 2>/dev/null)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 9 ]; then
            PYTHON="$cmd"
            echo "  Python:   $VERSION ($cmd)"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo ""
    echo "  ERROR: Python 3.9+ is required but not found."
    if [ "$PLATFORM" = "macOS" ]; then
        echo "  Install with: brew install python"
    else
        echo "  Install Python 3.9+ for your system."
    fi
    exit 1
fi

# Create virtual environment if needed
if [ ! -d "venv" ]; then
    echo ""
    echo "  Creating virtual environment..."
    "$PYTHON" -m venv venv
fi

# Activate
source venv/bin/activate
echo "  Venv:     activated"

# Install/update dependencies
echo ""
echo "  Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "  Dependencies installed."

# Create data directory
mkdir -p data

# Check for data bundle to import
if [ -f "data-export.zip" ] && [ ! -f "data/settings.json" ]; then
    echo ""
    echo "  Found data-export.zip — importing your data..."
    "$PYTHON" -c "
import zipfile, os
with zipfile.ZipFile('data-export.zip', 'r') as z:
    z.extractall('data/')
print('  Data imported successfully.')
"
fi

# Check for API keys
echo ""
if [ -f "data/settings.json" ]; then
    HAS_GOOGLE=$("$PYTHON" -c "
import json
d = json.load(open('data/settings.json'))
print('yes' if d.get('google_key') else 'no')
" 2>/dev/null || echo "no")
    HAS_ANTHROPIC=$("$PYTHON" -c "
import json
d = json.load(open('data/settings.json'))
print('yes' if d.get('anthropic_key') else 'no')
" 2>/dev/null || echo "no")
else
    HAS_GOOGLE="no"
    HAS_ANTHROPIC="no"
fi

# Also check env vars
[ -n "$GOOGLE_API_KEY" ] && HAS_GOOGLE="yes"
[ -n "$ANTHROPIC_API_KEY" ] && HAS_ANTHROPIC="yes"

if [ "$HAS_GOOGLE" = "yes" ] && [ "$HAS_ANTHROPIC" = "yes" ]; then
    echo "  API keys: configured"
else
    echo "  API keys needed (enter in Settings page after launch):"
    [ "$HAS_GOOGLE" != "yes" ] && echo "    - Google API Key (YouTube Data API v3)"
    [ "$HAS_ANTHROPIC" != "yes" ] && echo "    - Anthropic API Key (AI features)"
fi

# Launch
echo ""
echo "  Starting app..."
echo "  Open http://localhost:5000 in your browser"
echo ""

"$PYTHON" app.py

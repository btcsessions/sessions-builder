#!/bin/bash
cd "$(dirname "$0")"
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "claude/youtube-planning-app-B52L2")
git pull origin "$BRANCH" 2>/dev/null

# Kill any existing instance on port 5000
lsof -ti:5000 2>/dev/null | xargs kill 2>/dev/null

# Activate virtual environment
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
elif [ ! -d "venv" ]; then
    echo "Virtual environment not found. Run setup.sh first."
    exit 1
fi

# Open browser after server starts
(sleep 3 && xdg-open http://127.0.0.1:5000 2>/dev/null || open http://127.0.0.1:5000 2>/dev/null) &

python app.py

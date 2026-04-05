#!/bin/bash
cd "$(dirname "$0")"
git pull origin claude/youtube-planning-app-B52L2 2>/dev/null

# Activate virtual environment
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
elif [ ! -d "venv" ]; then
    echo "Virtual environment not found. Run setup.sh first."
    exit 1
fi

python app.py

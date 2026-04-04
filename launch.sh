#!/bin/bash
cd "$(dirname "$0")"
git pull origin claude/youtube-planning-app-B52L2 2>/dev/null
source venv/bin/activate 2>/dev/null || source venv/bin/activate.fish 2>/dev/null
python app.py

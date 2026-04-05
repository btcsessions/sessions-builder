#!/usr/bin/env fish
cd (dirname (status filename))
git pull origin claude/youtube-planning-app-B52L2 2>/dev/null

# Activate virtual environment
if test -f venv/bin/activate.fish
    source venv/bin/activate.fish
else if not test -d venv
    echo "Virtual environment not found. Run: bash setup.sh"
    exit 1
end

python app.py

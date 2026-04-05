#!/bin/bash
# Double-click this file to launch BTC Sessions Planner
# It opens in the background and opens your browser automatically

cd "$(dirname "$0")"

# Activate venv
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Kill any existing instance
pkill -f "python app.py" 2>/dev/null

# Launch app in background
python app.py &
APP_PID=$!

# Wait for server to start
echo "Starting BTC Sessions Planner..."
for i in {1..15}; do
    if curl -s http://localhost:5000 >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

# Open browser
if command -v open &>/dev/null; then
    open http://localhost:5000
elif command -v xdg-open &>/dev/null; then
    xdg-open http://localhost:5000
fi

echo ""
echo "BTC Sessions Planner is running at http://localhost:5000"
echo "Close this window or press Ctrl+C to stop."
echo ""

# Keep running until closed
wait $APP_PID

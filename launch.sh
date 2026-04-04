#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate 2>/dev/null || source venv/bin/activate.fish 2>/dev/null
python app.py

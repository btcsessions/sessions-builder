#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ $(uname -s) != Linux ]]; then
    echo 'This installer is for Linux. Use the existing Mac installer on macOS.'
    exit 1
fi
command -v python3 >/dev/null || { echo 'Install Python using your package manager first.'; exit 1; }
command -v git >/dev/null || { echo 'Install Git using your package manager first.'; exit 1; }
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python - <<'PY'
import os
from pathlib import Path
root = Path.cwd()
# Desktop Exec quoting also requires escaping the desktop-entry string layer.
def quote(value):
    value = str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
    return '"' + value.replace('\\', '\\\\') + '"'
folder = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'applications'
folder.mkdir(parents=True, exist_ok=True)
entry = folder / 'sessions-builder.desktop'
entry.write_text('[Desktop Entry]\nType=Application\nName=Sessions Builder\nComment=YouTube tutorial planner\nExec=' + quote(root / 'venv/bin/python') + ' ' + quote(root / 'launch_linux.py') + '\nIcon=applications-office\nTerminal=false\nCategories=Office;\nStartupNotify=false\n')
print('Installed Sessions Builder in your application menu.')
PY
echo 'Open Sessions Builder from the application menu. No voice components were installed.'

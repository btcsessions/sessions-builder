#!/usr/bin/env python3
"""Graphical Linux entry point; owns only the server it starts."""
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get('PLANNER_PORT', '5000'))
URL = f'http://127.0.0.1:{PORT}'


def notify(message):
    print(message, flush=True)
    try:
        subprocess.run(['kdialog', '--error', message], timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        pass


def ready():
    try:
        with urllib.request.urlopen(URL + '/api/health', timeout=1) as response:
            return json.load(response).get('application') == 'sessions-builder'
    except (OSError, ValueError):
        return False


def main():
    os.umask(0o077)
    os.chdir(ROOT)
    state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'sessions-builder'
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (state / f'launcher-{PORT}.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        for _ in range(60):
            if ready():
                webbrowser.open(URL)
                return 0
            time.sleep(1)
        notify('The planner is still starting. Try again shortly.')
        return 1
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', PORT)) == 0:
            notify(f'Port {PORT} is already in use. Close the older planner before opening this version.')
            return 1
    # Updates are optional while offline. Validation failures leave current code intact.
    if os.environ.get('PLANNER_SKIP_UPDATE') != '1':
        try:
            result = subprocess.run([sys.executable, '-c',
                'from updater import prepare_update; from pathlib import Path; print(prepare_update(Path.cwd()))'],
                capture_output=True, text=True, timeout=180)
            with (state / 'update.log').open('w') as log:
                log.write(result.stdout + result.stderr)
            if result.returncode:
                print('Update unavailable; launching installed version. See update.log.', flush=True)
        except subprocess.TimeoutExpired:
            print('Update timed out; launching installed version.', flush=True)
    env = dict(os.environ, LAUNCHED_FROM_APP='1', PLANNER_HOST='127.0.0.1', PYTHONUNBUFFERED='1')
    with (state / 'planner.log').open('a') as log:
        server = subprocess.Popen([sys.executable, str(ROOT / 'app.py')], env=env, stdout=log, stderr=log)
        try:
            for _ in range(60):
                if server.poll() is not None:
                    notify(f'Planner could not start. Details: {state / "planner.log"}')
                    return 1
                if ready():
                    webbrowser.open(URL)
                    return server.wait()
                time.sleep(0.5)
            notify(f'Planner did not become ready. Details: {state / "planner.log"}')
            return 1
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == '__main__':
    raise SystemExit(main())

"""Auto-start/stop the local Whisper transcription server.

The planner owns the whisper-server lifecycle so voice dictation "just
works": app.py calls maybe_start_whisper() at boot, and the server is
stopped again when the planner exits (atexit + SIGTERM — the .app's Swift
wrapper and /api/shutdown both stop the planner with SIGTERM, which would
skip atexit on its own).

Everything is conditional — if any prerequisite is missing this module
silently does nothing and dictation just reports the server as down:
  - whisper_url points at this machine (never manage a remote server)
  - the port is free (a server the user runs themselves wins)
  - the whisper-server binary exists (built once by setup_whisper.sh)
  - a ggml model file exists in data/whisper/
"""
from __future__ import annotations

import atexit
import glob
import os
import shutil
import signal
import socket
import subprocess
from urllib.parse import urlparse

import llm

APP_DIR = os.path.dirname(os.path.abspath(__file__))
WHISPER_DIR = os.path.join(APP_DIR, "data", "whisper")

# Our own build first, then anything on PATH, then Homebrew locations
# (Finder-launched .app bundles get a minimal PATH).
_BINARY_CANDIDATES = [
    os.path.join(WHISPER_DIR, "whisper.cpp", "build", "bin", "whisper-server"),
    "/opt/homebrew/bin/whisper-server",
    "/usr/local/bin/whisper-server",
]

_proc = None  # the managed whisper-server process, if we started one


def _local_port(settings: dict):
    """Port to manage, or None if whisper_url points at another machine."""
    url = (settings.get("whisper_url") or llm.DEFAULT_WHISPER_URL).strip()
    parsed = urlparse(url if "//" in url else "//" + url)
    if parsed.hostname not in ("127.0.0.1", "localhost"):
        return None
    return parsed.port or 8090


def _port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _find_binary():
    for path in _BINARY_CANDIDATES:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return shutil.which("whisper-server")


def _find_model():
    models = [m for m in glob.glob(os.path.join(WHISPER_DIR, "ggml-*.bin"))
              if os.path.getsize(m) > 0]
    if not models:
        return None
    return max(models, key=os.path.getsize)  # prefer the most capable model


def stop_whisper():
    """Stop the whisper-server we started (no-op if we didn't start one)."""
    global _proc
    if _proc is None or _proc.poll() is not None:
        _proc = None
        return
    _proc.terminate()
    try:
        _proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _proc.kill()
    _proc = None


def _handle_sigterm(signum, frame):
    stop_whisper()
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGTERM)


def maybe_start_whisper(settings: dict) -> None:
    """Start the local whisper-server if installed, needed, and not running."""
    global _proc
    if _proc is not None and _proc.poll() is None:
        return
    port = _local_port(settings)
    if port is None or _port_in_use(port):
        return
    binary = _find_binary()
    model = _find_model()
    if not binary or not model:
        return

    log_path = os.path.join(WHISPER_DIR, "server.log")
    os.makedirs(WHISPER_DIR, exist_ok=True)
    log = open(log_path, "a")
    log.write("=== whisper-server start: {} (model {}) ===\n".format(binary, model))
    log.flush()
    try:
        _proc = subprocess.Popen(
            [binary, "-m", model, "--host", "127.0.0.1", "--port", str(port),
             "--inference-path", "/v1/audio/transcriptions", "--convert", "-nt"],
            stdout=log, stderr=subprocess.STDOUT,
        )
    except OSError as e:
        print(f"[Whisper] failed to start whisper-server: {e}")
        return
    finally:
        log.close()  # the child holds its own handle

    print(f"[Whisper] started whisper-server on port {port} (model {os.path.basename(model)})")
    atexit.register(stop_whisper)
    signal.signal(signal.SIGTERM, _handle_sigterm)

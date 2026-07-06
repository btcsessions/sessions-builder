#!/usr/bin/env bash
# One-time setup for voice dictation: builds whisper.cpp's local transcription
# server and downloads the speech model. Run from the project folder:
#
#   bash setup_whisper.sh              # default model: large-v3-turbo (best accuracy)
#   bash setup_whisper.sh --model small.en   # lighter model for RAM-constrained Macs
#
# Safe to re-run — every step skips if already done. After it finishes,
# relaunch the planner: it starts/stops the Whisper server automatically.
set -e

cd "$(dirname "$0")"

MODEL="large-v3-turbo"
if [ "$1" = "--model" ] && [ -n "$2" ]; then
    MODEL="$2"
fi

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║   Voice Dictation — Whisper Setup    ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# --- Homebrew (needed for cmake + ffmpeg) ---
if ! command -v brew &>/dev/null; then
    echo "  ERROR: Homebrew is required. Install it first:"
    echo '    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    echo "  Then re-run: bash setup_whisper.sh"
    exit 1
fi
echo "  Homebrew: ok"

# --- Build tools + ffmpeg (ffmpeg lets the server decode browser audio) ---
for pkg in cmake ffmpeg; do
    if brew list "$pkg" &>/dev/null || command -v "$pkg" &>/dev/null; then
        echo "  $pkg: ok"
    else
        echo "  Installing $pkg..."
        brew install "$pkg"
    fi
done

mkdir -p data/whisper

# --- Build whisper-server from source ---
# (The Homebrew whisper-cpp package is compiled WITHOUT the server, so we
# build it ourselves once. Metal GPU acceleration is on by default.)
SRC="data/whisper/whisper.cpp"
SERVER_BIN="$SRC/build/bin/whisper-server"

if [ -x "$SERVER_BIN" ]; then
    echo "  whisper-server: already built"
else
    if [ ! -d "$SRC" ]; then
        echo ""
        echo "  Downloading whisper.cpp source..."
        git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$SRC"
    fi
    echo ""
    echo "  Building whisper-server (one time, takes a few minutes)..."
    cmake -S "$SRC" -B "$SRC/build" -DWHISPER_BUILD_SERVER=ON -DCMAKE_BUILD_TYPE=Release
    cmake --build "$SRC/build" -j --config Release --target whisper-server
    if [ ! -x "$SERVER_BIN" ]; then
        echo "  ERROR: build finished but $SERVER_BIN is missing."
        exit 1
    fi
    echo "  whisper-server: built"
fi

# --- Download the speech model ---
MODEL_FILE="data/whisper/ggml-$MODEL.bin"
if [ -s "$MODEL_FILE" ]; then
    echo "  Model ggml-$MODEL.bin: already downloaded"
else
    echo ""
    echo "  Downloading model ggml-$MODEL.bin (large-v3-turbo is ~1.6GB — resumes if interrupted)..."
    curl -L -C - --progress-bar -o "$MODEL_FILE" \
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$MODEL.bin"
    echo "  Model: downloaded"
fi

echo ""
echo "  ✓ Done. Restart the planner (dock icon or launch.command) and the"
echo "    mic buttons will just work — the app starts and stops the Whisper"
echo "    server for you. Audio never leaves this machine."
echo ""

#!/usr/bin/env bash
# Installs the Python AI packages (CPU builds). Does NOT download any model:
# Kokoro (~330 MB) and Whisper models download on first use into the
# hf-cache volume, which survives rebuilds.
set -euo pipefail

VENV="$HOME/.venvs/app"
cd /workspaces/facelesscreation

echo "==> Installing CPU-only torch into $VENV"
"$VENV/bin/pip" install --index-url https://download.pytorch.org/whl/cpu torch

echo "==> Installing Faster Whisper and Kokoro"
"$VENV/bin/pip" install -r .devcontainer/requirements-ai.txt

"$VENV/bin/python" - <<'PY'
import faster_whisper, kokoro, torch
print("faster-whisper", faster_whisper.__version__)
print("torch", torch.__version__, "(CUDA build)" if torch.version.cuda else "(CPU build)")
print("kokoro import OK")
PY

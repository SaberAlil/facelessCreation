#!/usr/bin/env bash
# postCreateCommand: runs once inside the dev container as user "vscode".
# Downloads Python/npm packages only. It never downloads AI models.
set -euo pipefail

WS="/workspaces/facelesscreation"
VENV="$HOME/.venvs/app"
cd "$WS"

echo "==> Checking volume ownership"
for d in "$HOME/.claude" "$HOME/.cache/huggingface" "$HOME/.cache/torch" "$HOME/.venvs"; do
  if [ "$(stat -c %U "$d")" != "$(id -un)" ]; then
    sudo chown -R "$(id -u):$(id -g)" "$d"
  fi
done

echo "==> Python virtual environment: $VENV"
if ! "$VENV/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 11)' 2>/dev/null; then
  rm -rf "$VENV"
  python3.11 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r .devcontainer/requirements-dev.txt
if [ -f backend/requirements.txt ]; then
  "$VENV/bin/pip" install --quiet -r backend/requirements.txt
fi

if [ "${INSTALL_AI_DEPS:-0}" = "1" ]; then
  bash .devcontainer/install-ai-deps.sh
else
  echo "    AI packages (Kokoro, Faster Whisper) not installed. Run: bash .devcontainer/install-ai-deps.sh"
fi

echo "==> Frontend dependencies"
if [ -f frontend/package.json ]; then
  (cd frontend && if [ -f package-lock.json ]; then npm ci; else npm install; fi)
else
  echo "    frontend/package.json not found yet - skipped"
fi

echo "==> Local data folders (git-ignored)"
mkdir -p data/generated data/tmp data/db

git config --global --add safe.directory "$WS"

echo
echo "Done. Next steps:"
echo "  bash .devcontainer/validate.sh      # environment checks"
echo "  claude                               # sign in to Claude Code"

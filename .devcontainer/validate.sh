#!/usr/bin/env bash
# ok/bad/wrn always return 0, so "A && ok || bad" is safe here.
# shellcheck disable=SC2015
# Environment validation. Run INSIDE the dev container:
#   bash .devcontainer/validate.sh
# Every line is PASS, FAIL, WARN or SKIP. Nothing is downloaded: the Ollama
# inference test only runs if qwen3:4b is already pulled.
# Results are also saved to data/validation/ (git-ignored).
set -uo pipefail

WS="/workspaces/facelesscreation"
[ -d "$WS" ] || WS="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$WS/data/validation"
mkdir -p "$OUT_DIR"
REPORT="$OUT_DIR/validate-$(date +%Y%m%d-%H%M%S).txt"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0; warn=0; skip=0
log()  { echo "$*" | tee -a "$REPORT"; }
ok()   { pass=$((pass+1)); log "PASS  $*"; }
bad()  { fail=$((fail+1)); log "FAIL  $*"; }
wrn()  { warn=$((warn+1)); log "WARN  $*"; }
skp()  { skip=$((skip+1)); log "SKIP  $*"; }

log "facelessCreation environment validation - $(date -Is)"
log "host: $(uname -srm)  user: $(id -un) ($(id -u))"
log "------------------------------------------------------------"

# 1. Toolchain -------------------------------------------------------------
pyv="$(python3 --version 2>&1)"
case "$pyv" in "Python 3.11."*) ok "python3: $pyv";; *) bad "python3: expected 3.11, got: $pyv";; esac

if [ -x "$HOME/.venvs/app/bin/python" ]; then
  ok "venv: $("$HOME/.venvs/app/bin/python" --version) at ~/.venvs/app"
  if "$HOME/.venvs/app/bin/python" -c "import fastapi, uvicorn" 2>/dev/null; then ok "venv: fastapi + uvicorn import"
  else bad "venv: fastapi/uvicorn missing (run setup.sh)"; fi
else
  bad "venv ~/.venvs/app missing (run .devcontainer/setup.sh)"
fi

nodev="$(node --version 2>&1)"
case "$nodev" in v22.*) ok "node: $nodev";; *) bad "node: expected v22, got: $nodev";; esac
command -v npm >/dev/null && ok "npm: $(npm --version)" || bad "npm missing"
command -v ng >/dev/null && ok "angular cli: $(ng version 2>/dev/null | grep -m1 -i 'Angular CLI' | tr -s ' ' || echo installed)" || bad "ng (Angular CLI) missing"

if git --version >/dev/null 2>&1 && git -C "$WS" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  ok "git: $(git --version), repo branch $(git -C "$WS" branch --show-current)"
else
  bad "git not working in $WS"
fi

if command -v claude >/dev/null; then
  ok "claude code: $(claude --version 2>/dev/null | head -1)"
  if [ -s "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.credentials.json" ]; then ok "claude code: credentials present in config volume"
  else wrn "claude code: not signed in yet (run 'claude')"; fi
else
  bad "claude code CLI missing"
fi

espeak-ng --version >/dev/null 2>&1 && ok "espeak-ng: $(espeak-ng --version | head -1 | cut -c1-60)" || bad "espeak-ng missing"
sqlite3 --version >/dev/null 2>&1 && ok "sqlite3: $(sqlite3 --version | cut -d' ' -f1)" || bad "sqlite3 missing"

# 2. GPU -------------------------------------------------------------------
gpu=0
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  name="$(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader | head -1)"
  case "$name" in *4050*) ok "nvidia-smi: $name";; *) wrn "nvidia-smi works but GPU is not an RTX 4050: $name";; esac
  gpu=1
else
  skp "nvidia-smi: no GPU visible in container (CPU fallback mode; see compose.local.yaml)"
fi

# 3. FFmpeg ----------------------------------------------------------------
ffmpeg -hide_banner -version >/dev/null 2>&1 && ok "ffmpeg: $(ffmpeg -hide_banner -version | head -1 | cut -c1-40)" || bad "ffmpeg missing"

encoders="$(ffmpeg -hide_banner -encoders 2>/dev/null)"  # no "| grep -q" under pipefail
if grep -q ' h264_nvenc ' <<<"$encoders"; then ok "ffmpeg build includes h264_nvenc"
else bad "ffmpeg build has no h264_nvenc"; fi

encode_test() { # $1 encoder, $2 extra args
  local f="$TMP/test-$1.mp4"
  # shellcheck disable=SC2086
  if ffmpeg -hide_banner -loglevel error -y \
       -f lavfi -i "testsrc2=size=1280x720:rate=30" -t 3 \
       -c:v "$1" $2 -pix_fmt yuv420p "$f" 2>"$TMP/$1.err"; then
    local codec dur
    codec="$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 "$f")"
    dur="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")"
    echo "codec=$codec duration=${dur}s size=$(stat -c %s "$f")B"
    return 0
  fi
  head -3 "$TMP/$1.err"
  return 1
}

if [ "$gpu" = 1 ]; then
  if res="$(encode_test h264_nvenc "-preset p4")"; then ok "NVENC encode (3 s, 720p): $res"
  else bad "NVENC encode failed: $res"; fi
else
  skp "NVENC encode: no GPU in container"
fi

if res="$(encode_test libx264 "-preset veryfast")"; then ok "libx264 CPU fallback encode (3 s, 720p): $res"
else bad "libx264 encode failed: $res"; fi

sel="$(bash "$WS/.devcontainer/select-encoder.sh")"
ok "select-encoder.sh chose: $sel"

# 4. Ollama ----------------------------------------------------------------
OH="${OLLAMA_HOST:-http://ollama:11434}"
if ver="$(curl -fsS --max-time 5 "$OH/api/version" 2>/dev/null)"; then
  ok "ollama reachable at $OH: $ver"
  tags="$(curl -fsS --max-time 5 "$OH/api/tags" 2>/dev/null)"
  if grep -q '"qwen3:4b"' <<<"$tags"; then
    if curl -fsS --max-time 300 "$OH/api/generate" \
         -d '{"model":"qwen3:4b","prompt":"Reply with the word ok.","stream":false,"think":false,"options":{"num_predict":8}}' >/dev/null; then
      ok "ollama: qwen3:4b generated a response"
      sleep 3
      ps="$(curl -fsS "$OH/api/ps" 2>/dev/null)"
      if grep -q '"models":\[\]' <<<"$ps"; then ok "ollama: model unloaded after inference (KEEP_ALIVE=0)"
      else wrn "ollama: model still loaded after inference: ${ps:0:120}"; fi
    else
      bad "ollama: qwen3:4b generation failed"
    fi
  else
    skp "ollama inference: qwen3:4b not pulled (pull needs approval: ~2.5 GB)"
  fi
else
  bad "ollama not reachable at $OH"
fi

# 5. Persistent caches -------------------------------------------------------
for d in "$HOME/.claude" "$HOME/.cache/huggingface" "$HOME/.cache/torch" "$HOME/.venvs"; do
  if ! mountpoint -q "$d" 2>/dev/null; then wrn "$d is not a mounted volume (will not survive rebuild)"; continue; fi
  owner="$(stat -c %U "$d")"
  if [ "$owner" = "$(id -un)" ] && touch "$d/.write-test" 2>/dev/null && rm -f "$d/.write-test"; then
    ok "volume $d: mounted, owned by $owner, writable"
  else
    bad "volume $d: owner=$owner, not writable by $(id -un)"
  fi
done

marker="$HOME/.cache/huggingface/.persistence-marker"
if ! mountpoint -q "$HOME/.cache/huggingface" 2>/dev/null; then
  skp "persistence marker: hf-cache volume not mounted (not inside the dev container?)"
elif [ -f "$marker" ]; then
  ok "persistence: marker from $(cat "$marker") is still present"
else
  date -Is > "$marker" 2>/dev/null && wrn "persistence: marker created now - rebuild the container and run again to confirm" \
    || bad "persistence: could not write marker"
fi

# 6. Secrets -----------------------------------------------------------------
if leaked="$(git -C "$WS" ls-files | grep -E '(^|/)\.env$|\.pem$|\.key$|credentials\.json$|\.safetensors$|\.gguf$|\.mp4$')"; then
  bad "tracked files that look like secrets/models/videos: $leaked"
else
  ok "no secrets, models or videos tracked by git"
fi

log "------------------------------------------------------------"
log "PASS=$pass FAIL=$fail WARN=$warn SKIP=$skip"
log "report: $REPORT"
[ "$fail" -eq 0 ]

#!/usr/bin/env bash
# Prints the H.264 encoder to use: h264_nvenc if a real NVENC encode works,
# otherwise libx264. Respects VIDEO_ENCODER if it is set to something other
# than "auto".
#
#   ENC=$(bash .devcontainer/select-encoder.sh)
#   ffmpeg -i in.mp4 -c:v "$ENC" out.mp4
set -uo pipefail

if [ -n "${VIDEO_ENCODER:-}" ] && [ "${VIDEO_ENCODER}" != "auto" ]; then
  echo "$VIDEO_ENCODER"
  exit 0
fi

# Capture first: "ffmpeg | grep -q" fails under pipefail (SIGPIPE).
encoders="$(ffmpeg -hide_banner -encoders 2>/dev/null)"
if grep -q ' h264_nvenc ' <<<"$encoders" \
   && ffmpeg -hide_banner -loglevel error \
        -f lavfi -i "color=black:size=256x256:rate=30" -frames:v 10 \
        -c:v h264_nvenc -f null - >/dev/null 2>&1; then
  echo "h264_nvenc"
else
  echo "libx264"
fi

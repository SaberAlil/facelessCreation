#!/usr/bin/env bash
# Read-only host inspection. Run on the HOST (your laptop), not in the container:
#   bash .devcontainer/host-check.sh            # inspect only
#   bash .devcontainer/host-check.sh --gpu-test # also run nvidia-smi in a throwaway
#                                               # container (pulls ubuntu:24.04, ~30 MB)
# It changes nothing on the system. Paste the output into HARDWARE.md.
set -uo pipefail

row() { printf '| %s | %s |\n' "$1" "$2"; }
val() { "$@" 2>/dev/null | head -1 || true; }

echo "## Detected on $(date -Is)"
echo
echo "| Item | Value |"
echo "|---|---|"
# shellcheck disable=SC1091
row "OS" "$(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}")"
row "Kernel" "$(uname -r)"
row "CPU" "$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs) ($(nproc) threads)"
row "RAM" "$(free -h | awk '/Mem:/{print $2}')"
row "Disk free (home)" "$(df -h "$HOME" | awk 'NR==2{print $4}')"

if command -v nvidia-smi >/dev/null 2>&1; then
  row "GPU" "$(val nvidia-smi --query-gpu=name --format=csv,noheader)"
  row "VRAM" "$(val nvidia-smi --query-gpu=memory.total --format=csv,noheader)"
  row "Power limit" "$(val nvidia-smi --query-gpu=power.limit --format=csv,noheader)"
  row "NVIDIA driver" "$(val nvidia-smi --query-gpu=driver_version --format=csv,noheader)"
  row "Max CUDA version supported by driver" "$(nvidia-smi 2>/dev/null | grep -o 'CUDA Version: [0-9.]*' | cut -d' ' -f3)"
  row "NVENC sessions now" "$(val nvidia-smi --query-gpu=encoder.stats.sessionCount --format=csv,noheader)"
else
  row "GPU" "nvidia-smi not found"
fi
row "CUDA toolkit on host (nvcc)" "$(command -v nvcc >/dev/null && nvcc --version | grep -o 'release [0-9.]*' || echo 'not installed (not needed)')"

row "Docker" "$(val docker --version)"
row "Docker Compose" "$(val docker compose version)"
row "Docker daemon reachable" "$(docker info >/dev/null 2>&1 && echo yes || echo 'no (daemon down or user not in docker group)')"
row "Docker runtimes" "$(docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -o '"[a-z-]*":' | tr -d '":' | xargs)"
row "NVIDIA Container Toolkit" "$(val nvidia-ctk --version || echo 'nvidia-ctk not found')"
row "VS Code" "$(command -v code >/dev/null && code --version | head -1 || echo 'code not on PATH')"
row "Dev Containers extension" "$(command -v code >/dev/null && code --list-extensions 2>/dev/null | grep -c ms-vscode-remote.remote-containers | sed 's/1/installed/;s/^0$/not installed/')"

if [ "${1:-}" = "--gpu-test" ]; then
  echo
  echo "### GPU container smoke test"
  echo '```'
  docker run --rm --gpus all -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video ubuntu:24.04 nvidia-smi -L 2>&1
  echo '```'
fi

# facelessCreation
A web application that allow users to create faceless videos

## Development environment

The app is developed inside a VS Code dev container with GPU support. It runs
fully locally: no paid AI APIs.

| What | Version / detail |
|---|---|
| Backend | Python 3.11, FastAPI, SQLite |
| Frontend | Node.js 22, Angular CLI |
| LLM | Ollama (separate container) with Qwen3 4B |
| Speech | Kokoro TTS (CPU), Faster Whisper (CPU INT8), eSpeak NG |
| Video | FFmpeg with NVIDIA NVENC, libx264 fallback |
| Optional | ComfyUI with Stable Diffusion |
| Tools | Claude Code, Git |

See [HARDWARE.md](HARDWARE.md) for the GPU/VRAM budget and
[PROGRESS.md](PROGRESS.md) for validation status.

### Requirements (host)

- Linux with Docker Engine and Docker Compose v2
- VS Code with the **Dev Containers** extension
- For GPU: NVIDIA driver and the NVIDIA Container Toolkit. Without them the
  container starts in CPU mode automatically (no NVENC, Ollama on CPU).

Check your machine (read-only):

```bash
bash .devcontainer/host-check.sh
```

### First start

1. `cp .env.example .env` (optional; never commit `.env`).
2. Open the repo in VS Code and run **Dev Containers: Reopen in Container** (F1).
   Before starting, `detect-gpu.sh` turns GPU passthrough on if the host supports it.
   To force CPU mode: start VS Code with `FACELESS_FORCE_CPU=1 code .`
3. Wait for the first build and `setup.sh` (several minutes).
4. In the container terminal:
   ```bash
   bash .devcontainer/validate.sh
   claude          # sign in to Claude Code once; the login survives rebuilds
   ```
5. Download the LLM when you're ready (~2.5 GB):
   ```bash
   curl http://ollama:11434/api/pull -d '{"model":"qwen3:4b"}'
   ```
6. Optional AI packages (CPU): `bash .devcontainer/install-ai-deps.sh`

### Day to day

| Task | How |
|---|---|
| Backend | Run **Backend: FastAPI (uvicorn, debug)** → http://localhost:8000 |
| Frontend | Run **Frontend: Angular dev server** → http://localhost:4200 |
| Pick the video encoder | `ENC=$(bash .devcontainer/select-encoder.sh)` → `h264_nvenc` or `libx264` |
| Optional ComfyUI | `docker compose -f .devcontainer/compose.yaml -f .devcontainer/compose.local.yaml --profile comfyui up -d comfyui` on the host → http://127.0.0.1:8188. Stop it before LLM work. |
| Rebuild after config changes | **Dev Containers: Rebuild Container** |

Models, caches, the Python venv and the Claude Code login live in Docker volumes
and survive rebuilds. Generated files go to `data/`, which Git ignores.

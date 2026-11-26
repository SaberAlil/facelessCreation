# Progress

Branch: `gpu-devcontainer` (from `main` @ `b101b56`). Not pushed.

**Status: configuration written and statically validated. Not yet validated on the
target laptop.** Application features must not start until `validate.sh` passes
inside the dev container on the laptop (see "Remaining manual steps").

Legend: ✅ done and tested · 🟡 done, needs the laptop to test · ⏳ manual step pending

## Checklist

| # | Step | Status |
|---|---|---|
| 1 | Inspect repo and README | ✅ `main` had `README.md` and an Angular `.gitignore` |
| 2 | Preserve files and Git history | ✅ new branch; README and `.gitignore` only appended to |
| 3 | Inspect host OS and hardware | ⏳ `host-check.sh` written; not run on the laptop |
| 4 | Docker and Compose installed | ⏳ checked by `host-check.sh` |
| 5 | NVIDIA Container Toolkit installed | ⏳ checked by `host-check.sh` and `detect-gpu.sh` |
| 6 | `.devcontainer/devcontainer.json` | ✅ |
| 7 | `.devcontainer/Dockerfile` | 🟡 lint-clean; image not built yet |
| 8 | `.devcontainer/compose.yaml` | ✅ `docker compose config` valid |
| 9 | `.devcontainer/setup.sh` | 🟡 lint-clean; runs on first container start |
| 10 | GPU passthrough | 🟡 `compose.gpu.yaml`, switched on by `detect-gpu.sh` |
| 11 | Persistent volumes (models, caches, assets) | 🟡 named volumes + `data/` bind mount |
| 12 | Ollama as separate service | 🟡 internal network only, no published port |
| 13 | Python virtual environment | 🟡 `~/.venvs/app` in a volume |
| 14 | Node.js 22 + Angular CLI | 🟡 |
| 15 | FFmpeg, eSpeak NG, system libraries | 🟡 |
| 16 | Forward ports 4200 and 8000 | ✅ in `devcontainer.json` |
| 17 | VS Code extensions and debugging | ✅ extensions + `.vscode/launch.json` |
| 18 | Claude Code in container | 🟡 installed in the image |
| 19 | Claude Code auth persists | 🟡 `claude-config` volume + `CLAUDE_CONFIG_DIR` |
| 20 | `.gitignore` | ✅ tested with `git check-ignore` |
| 21 | `.env.example` | ✅ no credentials |
| 22 | GPU access in container | ⏳ `validate.sh` |
| 23 | Real NVENC encode | ⏳ `validate.sh` |
| 24 | Ollama connectivity | ⏳ `validate.sh` |
| 25 | CPU fallback | ✅ libx264 path tested (see below) |
| 26 | Model cache permissions | ⏳ `validate.sh` |
| 27 | `HARDWARE.md` | ✅ (detected values pending) |
| 28 | `PROGRESS.md` | ✅ |
| 29 | README setup instructions | ✅ original text kept |
| 30 | Document results and manual steps | ✅ this file |

## Validation results (26 Nov 2026)

All tests below were **run in a cloud workspace** (Ubuntu 24.04, Docker 29.9,
no GPU, Docker Hub blocked), not on the laptop and not inside the dev container.

### Ran and passed

| Test | Result |
|---|---|
| `devcontainer.json`, `launch.json` parse as JSON | pass |
| `yamllint` on both Compose files | pass |
| `shellcheck` on all 7 scripts | pass (after fixes below) |
| `hadolint` on both Dockerfiles | pass (after fixes below) |
| `docker compose config`, CPU mode | valid; 0 GPU reservations; services `dev`, `ollama` |
| `docker compose config`, GPU mode (simulated with stub `nvidia-smi`) | valid; `dev` gets `gpu,compute,utility,video`; `ollama`, `comfyui` get `gpu,compute,utility` |
| `--profile comfyui` adds ComfyUI only when asked | pass |
| No privileged containers, no Docker socket mount | pass (checked in merged config) |
| Published ports | only ComfyUI, on `127.0.0.1:8188`; Ollama and dev publish none |
| Ollama settings in merged config | `KEEP_ALIVE=0`, `MAX_LOADED_MODELS=1`, `NUM_PARALLEL=1` |
| `detect-gpu.sh`, real (no GPU) | chose CPU mode: "nvidia-smi not found" |
| `detect-gpu.sh`, stubs: driver + `nvidia-ctk` | chose GPU mode |
| `detect-gpu.sh`, stubs: driver + `nvidia` Docker runtime only | chose GPU mode |
| `detect-gpu.sh`, stubs: driver but no toolkit | chose CPU mode: "NVIDIA Container Toolkit not found" |
| `FACELESS_FORCE_CPU=1` | chose CPU mode |
| `select-encoder.sh` (FFmpeg 6.1 with NVENC compiled in, no GPU) | real NVENC attempt failed → printed `libx264` |
| `VIDEO_ENCODER` override | respected |
| libx264 CPU fallback encode, 3 s 720p | pass: `codec=h264 duration=3.000000s size=1072345B` |
| `.gitignore`: `.env`, `compose.local.yaml`, `data/*.mp4`, `models/*.gguf` | ignored |
| `.gitignore`: `.env.example`, `.vscode/launch.json` | not ignored (committed) |
| Credential pattern scan of tracked files | nothing found |

### Issues found and fixed

1. **NVENC never selected on a GPU machine.** `ffmpeg -encoders | grep -q h264_nvenc`
   under `set -o pipefail` fails: `grep -q` exits on the first match and FFmpeg
   gets SIGPIPE. The test reported "no h264_nvenc" even though it was there, so
   `select-encoder.sh` would always have fallen back to libx264. Fixed by capturing
   the output first. The same pattern was removed from the Ollama checks in
   `validate.sh` and the Docker runtime check in `detect-gpu.sh`.
2. `validate.sh` crashed writing the persistence marker when the cache volume
   isn't mounted. It now skips that check outside the container.
3. Lint: `shellcheck` SC2015/SC1091 and `hadolint` DL3064 (false positive on
   `USERNAME`) / DL3066 handled.

### Not run (needs the laptop)

| Test | Why not run here |
|---|---|
| Build the dev image | Docker Hub is blocked from the cloud workspace |
| Open in VS Code Dev Containers | no VS Code / laptop access |
| `nvidia-smi` detects RTX 4050 in container | no GPU here |
| Real NVENC encode | no GPU here |
| Ollama reachable from dev container | Ollama image can't be pulled here |
| Qwen3 4B inference and unload after use | model not downloaded (needs approval) |
| Volume ownership and survival across rebuild | containers not started |
| Claude Code sign-in persists across rebuild | containers not started |

## Remaining manual steps (on the laptop)

1. `bash .devcontainer/host-check.sh` and paste the output into `HARDWARE.md`.
   If the NVIDIA Container Toolkit is missing, installing it changes the host:
   decide that yourself (NVIDIA's install guide), or keep CPU mode.
2. Open the repo in VS Code → **Dev Containers: Reopen in Container**.
3. In the container: `bash .devcontainer/validate.sh`.
4. Approve and pull the LLM (~2.5 GB), from the dev container:
   `curl http://ollama:11434/api/pull -d '{"model":"qwen3:4b"}'`
   then run `validate.sh` again for the inference and unload test.
5. Run `claude` and sign in.
6. **Dev Containers: Rebuild Container**, then `validate.sh` again: the
   persistence marker and the Claude sign-in should still be there.
7. Optional: `bash .devcontainer/install-ai-deps.sh` (Kokoro + Faster Whisper, CPU).
   Their models download on first use (Kokoro ~330 MB): that needs approval too.
8. Update this file with the results, then start on application features.

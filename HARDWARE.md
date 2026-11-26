# Hardware and compatibility

## Target machine (reported by the owner, not yet detected)

These values come from the project brief. They have **not** been read from the
machine yet: the dev container was written in a cloud workspace without access
to the laptop. Run `bash .devcontainer/host-check.sh` on the laptop and paste
its output under "Detected" below.

| Item | Value |
|---|---|
| Laptop | ASUS TUF Gaming F15 |
| CPU | Intel Core i7 |
| RAM | 16 GB |
| GPU | NVIDIA GeForce RTX 4050 Laptop GPU (Ada Lovelace) |
| VRAM | 6141 MiB |
| GPU power limit | 100 W |
| OS | Linux |
| NVIDIA driver | 595.99.02 |
| "CUDA Version" shown by nvidia-smi | 13.2 |

## Detected

_Not yet run. Paste the output of `bash .devcontainer/host-check.sh --gpu-test` here._

## What the numbers mean

- **"CUDA Version 13.2" is not an installed CUDA toolkit.** It is the newest CUDA
  version the driver can run. Nothing in this project needs a CUDA toolkit on the
  host, and none is installed inside the containers either.
- **No driver inside containers.** The NVIDIA Container Toolkit injects the host
  driver's libraries at start-up (`compute,utility` for Ollama/ComfyUI, plus
  `video` for the dev container so FFmpeg can load NVENC).
- **CUDA libraries for optional GPU work** come from Python wheels built for CUDA 12.x
  (PyTorch `cu128` for ComfyUI; CTranslate2 for Faster Whisper if you move it to GPU).
  CUDA 12.x programs run on newer drivers, so driver 595 is compatible.

## Video encoding

- The RTX 4050 Laptop GPU has an NVENC encoder that supports H.264, HEVC and AV1.
- The dev image uses Debian bookworm's FFmpeg, built with NVENC support that loads
  from the host driver at runtime, plus libx264 for the CPU fallback.
- `.devcontainer/select-encoder.sh` runs a real 10-frame NVENC encode and prints
  `h264_nvenc` if it works, otherwise `libx264`. `VIDEO_ENCODER` in `.env` overrides it.

## Memory budget (6 GB VRAM, 16 GB RAM)

Only **one large GPU workload at a time**.

| Workload | Where it runs | Notes |
|---|---|---|
| Ollama, Qwen3 4B (`qwen3:4b`, ~2.5 GB download) | GPU | Fits in 6 GB with a normal context. `OLLAMA_KEEP_ALIVE=0` unloads it right after each request; `OLLAMA_MAX_LOADED_MODELS=1` and `OLLAMA_NUM_PARALLEL=1` stop a second model or parallel requests from loading. |
| Kokoro TTS (82M) | CPU (default) | Small; CPU keeps VRAM free. |
| Faster Whisper | CPU, INT8 (default) | Start with `small`. Move to GPU (`float16`) only if CPU is too slow. |
| FFmpeg NVENC | GPU encoder block | Uses the separate encoder hardware and little VRAM; can run alongside other work. |
| ComfyUI + Stable Diffusion | GPU, optional | Starts with `--lowvram`. SD 1.5 fits in 6 GB; SDXL is possible but slow. **Stop it before Ollama inference.** Full AI video diffusion is out of scope. |

If you get out-of-memory errors: make sure ComfyUI is stopped, check `nvidia-smi`
for leftover processes, and use a shorter context for Qwen3.

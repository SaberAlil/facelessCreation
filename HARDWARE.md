# Hardware and compatibility

## Target machine (from the project brief)

Values from the brief; the "Detected" section below has what was measured.

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

## Detected (inside the dev container, 26 Nov 2026)

Read-only checks (`/etc/os-release`, `uname -a`, `lscpu`, `free -h`, `df -h`,
`nvidia-smi`, `ffmpeg -version/-encoders/-filters`) and `faceless doctor`.

| Item | Detected |
|---|---|
| CPU | 12th Gen Intel Core i7-12700H, 14 cores / 20 threads |
| RAM | 15.2 GiB total; **only ~6.9–7.6 GiB available** while the desktop session runs |
| Swap | 2 GiB |
| GPU | NVIDIA GeForce RTX 4050 Laptop GPU, 6141 MiB, ~74 MiB used at idle |
| GPU power limit | `nvidia-smi` reports `[N/A]` inside the container (100 W per brief) |
| Driver | 595.99.02 |
| `nvcc` | not installed (not needed) |
| Host kernel | Linux 6.8.0-149-generic (Ubuntu 22.04 HWE kernel) |
| Container OS | Debian 12 (bookworm) |
| Python / Node | 3.11.18 / 22.23.5 |
| FFmpeg | 5.1.9 (Debian): `h264_nvenc` (real test encode passes), `libx264`, `libass` (`ass`/`subtitles`), `drawtext`, `zoompan`, `loudnorm`, `sidechaincompress`, `gradients` |
| espeak-ng | 1.51 |
| Ollama | 0.40.18, `qwen3:4b` (Q4_K_M, 2.5 GB) pulled |
| Disk | 66 GB free of 444 GB (85 % used): keep an eye on it |
| Fonts | DejaVu (Sans, Sans Bold, Serif, Mono) only |

Unknowns: host distribution details beyond the kernel (run `host-check.sh` on the
host), real GPU power limit, thermals under sustained batch load.

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

The application enforces this: GPU-heavy stages (Ollama drafting, ComfyUI images, NVENC
renders) take a cross-process lock (`data/locks/gpu.lock`), check free VRAM against
`resources.vram_headroom_mib` (768 MiB default) first, and unload their model afterwards
(`keep_alive: 0` + `/api/ps` verification for Ollama, `POST /free` for ComfyUI).

## Measured benchmarks (26 Nov 2026, this laptop)

Machine time only; editorial review time is not included. Narration 65.8 s,
7 scenes, original procedural graphics, Whisper `small.en` captions, 1080x1920 30 fps.
Raw reports: `data/bench/benchmark-*.json`.

| Stage | Device | Time | Peak memory |
|---|---|---|---|
| Script draft, Ollama `qwen3:4b` (2 calls, incl. model load ~27 s) | GPU | 70–89 s | 3,220 MiB GPU memory in use (whole GPU); back to 74 MiB after unload |
| Narration, Kokoro 82M, 7 scenes | CPU | 20.8 s first video, 15.6–16.7 s once loaded | ~1.7–2.0 GB RSS |
| Visuals (7 procedural graphics) | CPU | 0.8 s | small |
| Captions, faster-whisper `small.en` INT8 | CPU | 5.4–7.0 s | +~0.5 GB RSS |
| Preview render 540x960 | NVENC | 16.7 s | 206 MiB GPU |
| Final render 1080x1920 | NVENC | 34.5–39.4 s | 283 MiB GPU |
| Final render 1080x1920 | libx264 (CPU fallback) | 43.2 s | 0 GPU |

| Run | Total machine time | Failures | Output |
|---|---|---|---|
| 1 video (NVENC) | 63.6 s | 0 | 14.7 MB MP4; 36.6 MB job folder |
| 7 videos in one process (NVENC) | 418.5 s (57–63.5 s each) | 0 | ~14.7 MB MP4 each; 292.7 MB of job folders |
| 1 video (libx264) | 73.2 s | 0 | 7.3 MB MP4 (CRF 20 vs NVENC CQ 21) |

Notes:
- Kokoro and Whisper stay loaded in a long-running process (batch, dashboard). Run
  from separate CLI commands, each stage pays its model load again (Kokoro ~5 s).
- Peak RSS for a process running both models was 3.3 GB: with ~7 GB available, run one
  video at a time (the default).
- The draft time covers the LLM only; checking facts and editing is human time.

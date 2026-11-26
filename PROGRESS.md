# Progress

Branch: `gpu-devcontainer`. Nothing committed or pushed by the agent.

# Part 2: Faceless Studio application (started 26 Nov 2026)

Legend: [x] done and tested here · [~] implemented, tested only with mocks/fixtures · [ ] not done

Spec: `CLAUDE_CODE_FACELESS_STUDIO_RTX4050.md` (hardware addendum + original spec), supplied
in chat on 26 Nov 2026. All results below were produced on the target laptop inside the
dev container (RTX 4050 visible, NVENC working, Ollama with `qwen3:4b`).

> **Important: approvals in the test runs were made by the coding agent** (reviewer
> `claude-code (pipeline test)`, notes say TEST APPROVAL). They exercise the gates; they
> are not editorial approval. The sample research claims are deliberately unverified and
> the day-1 video must not be published before a human fact-check.

## Milestones

### Step 1 / M1: environment, bootstrap, CLI
- [x] Read-only environment inspection → `HARDWARE.md` "Detected"
- [x] Project layout: `backend/` (package `faceless_studio`), `frontend/`, `config/`, `content/`, `docs/`
- [x] Config: pydantic defaults = `config/default.yaml` (test enforces they match), `config/local.yaml`, `FACELESS__…` env
- [x] `doctor`: FFmpeg filters (libass etc.), **real NVENC test encode**, fonts, models, RAM/VRAM/disk, Ollama, Pexels, ComfyUI; install hints
- [x] SQLite state (jobs, transitions, stage runs, approvals, calendar, library), job ids, per-job dirs, exit codes
- [x] `new`, `list`, `status`, `resume`
- [x] Unit tests

### Step 2–3: providers and resource management
- [x] Cross-process GPU lock (`flock`; released if the holder dies; re-entrant), separate render lock
- [x] VRAM/RAM headroom checks (768 MiB / 3 GiB defaults), per-stage duration, peak RSS, sampled GPU memory, device, fallbacks recorded for every stage
- [x] Ollama: `keep_alive: 0`, unload **verified via `/api/ps`** (live: VRAM back to 74 MiB)
- [x] OOM handling: one retry with reduced settings (Ollama num_ctx 4096→2048 then CPU; ComfyUI 448x672/14 steps; Whisper small.en→base.en), no unbounded retries; simulated-OOM tests
- [x] Model ids and licenses verified (`LICENSES.md`)

### M2: calendar, research, scripts
- [x] CSV/XLSX import with header-row detection, column mapping, duplicate day/date/topic and empty-topic rejection, source row + original topic kept, select by day or date
- [x] Manual research workflow (`research init/add-source/add-claim/verify/show`), bounded public-URL `fetch` (no private hosts, no redirects followed)
- [x] `ManualScriptProvider` (.md/.txt/.json with claims + platform copy)
- [x] `OllamaScriptProvider`: prose draft (length feedback, ≤3 attempts) → JSON storyboard (schema validation, ≤3 attempts); strips leaked `<think>`; research passed as data; "INSUFFICIENT RESEARCH" path
- [x] Claim checks: unsourced/unknown-source/unverified claims flagged; **all LLM claims stay flagged** until a human clears them
- [x] `draft`, `review-script`, `approve-script` (hash of script + research; edits withdraw approval)
- [ ] The real 365-day `content/faceless_content_365_day_calendar.xlsx` was not provided; importer tested with generated XLSX files and the sample CSV

### M3: voice
- [x] Kokoro 82M (`af_heart`, Apache-2.0) on CPU, per-scene synthesis, cache by text/voice/speed/model, controlled pauses, two-pass loudness normalisation, real timing via ffprobe
- [x] User narration import; espeak-ng demo provider (marked demo, cannot pass final approval)

### M4: visual assets
- [x] Local licensed library with provenance (license required, commercial-use flag, creator, attribution, source URL, hash dedupe)
- [x] Original procedural graphics, 5 templates × 6 palettes, no repeats across neighbouring scenes
- [x] Scene matching, manual replacement, music selection, rights review (`confirm`/`reject`), asset-set approval
- [~] Pexels provider (official API, portrait preference, size cap, host allow-list) — tested with a mocked API only; no key available
- [~] ComfyUI provider (local API, license guard, GPU lock, VRAM check, `/free` after use, OOM retry, provenance) — mocked API only; needs ComfyUI + a checkpoint you choose

### M5: subtitles and rendering
- [x] faster-whisper `small.en` CPU INT8 word timestamps aligned to the **script text**; divergence report; approximate fallback (needs review); SRT import with validation; ASS styling in safe zones
- [x] FFmpeg render: frame-exact scene timing from narration, aspect-preserving crop / blurred pad, Ken Burns on stills, push-in on video, music ducking, loudness -14 LUFS / -1 dBTP, 1080x1920 30 fps yuv420p H.264/AAC faststart, 540x960 preview, dry run
- [x] NVENC chosen by a real probe encode, runtime failure → libx264 (tested by injecting a broken NVENC option)
- [x] Validation: streams, codec, size, fps, pixel format, A/V duration vs narration, faststart, full decode, loudness/true peak
- [x] Safe subprocess use: argument arrays, timeouts, escaped filter paths, `textfile=` for text, atomic writes

### M6: review, export, batch
- [x] `review` checklist, `approve-final` (refused while required checks fail; records hashes), `export` package
- [x] `batch` by dates or day range, `--dry-run`, `--limit`, `--resume`, `--concurrency` (default 1; renders always serial), stops at gates, $0 cost notice
- [x] Tracking CSV that keeps manually entered columns
- [x] `README.md` operational guide, `docs/walkthrough.md` (single video + weekly batch)
- [x] Cache by content hash; downstream invalidation; `cache stats/clean` (LRU, never touches job inputs/exports)

### M7: dashboard
- [x] FastAPI on loopback: Host check, `X-Faceless` header for mutations, CORS limited to the dev origin, file allow-list, single worker (serial GPU work), typed approvals
- [x] Angular 22 dashboard: calendar (import, create jobs), jobs, job page (stage runs with memory/device, run stages, script & research editing with claim flags, asset previews + rights, caption editor, preview/final players, checklist, approvals, export), system/doctor
- [~] UI tested with component tests and live HTTP checks through the dev proxy; **no browser click-through test** (no browser installed in the container)

## Test results (26 Nov 2026)

| Suite | Result |
|---|---|
| `cd backend && pytest -q` | **79 passed** (unit + FFmpeg integration with a labelled tone fixture) |
| `pytest -m models` (real Kokoro + faster-whisper) | 1 passed |
| `ruff check` | clean |
| `cd frontend && npx ng test --watch=false` | 5 passed |
| `npx ng build` | ok, 92 KB transfer size |

## Real runs on this laptop

| What | Result |
|---|---|
| Smoke video (`data/jobs/20261126-smoke-test-why-prices-en-b027`) | 15.9 s, Kokoro + Whisper + NVENC, all technical checks pass; final approval correctly **refused** (too short) |
| Full video, day 1 (`data/jobs/d001-20261126-why-prices-end-in-99-e92b/export/`) | 65.8 s, 7 scenes, 44 captions (91 % script match; "Manoj" heard as "Manage", captions keep the script spelling), -14.1 LUFS, -2.3 dBTP, NVENC, faststart, decode clean, **EXPORTED** (test approvals) |
| CPU fallback, full length | libx264 render 43.2 s, validation passes |
| Restart after failure | Simulated render crash → FAILED, earlier work kept, `resume` completes (test) |
| Ollama draft | `qwen3:4b` on GPU, 70–89 s, peak 3,220 MiB, unload verified; with unverified research it reports insufficient research; with verified research it wrote 152 words in one attempt |
| Batch | days 1–7: day 1 exported, days 2–7 created and stopped at DRAFT; re-run creates no duplicates |
| Benchmarks | 1 video 63.6 s; 7 videos 418.5 s, 0 failures (details in `HARDWARE.md`) |

## Known limitations

- **Qwen3 4B drafts need real editing.** In tests it invented a detail ("$2.95", not in the research) and garbled an example. Claims are flagged, but the fact-check is yours.
- Only original procedural graphics have been used in real renders. Real licensed footage, Pexels and ComfyUI images are implemented but not exercised end-to-end here.
- Kokoro mispronounces some names; check the audio, rephrase, or record your own narration (`voice --narration`).
- With user-recorded narration, scene boundaries are estimated by text share (captions are still Whisper-timed).
- Only ~7 GB RAM is free while the desktop runs; one process with both models peaks at ~3.3 GB. `--concurrency > 1` is not benchmarked.
- Peak RSS values are process high-water marks (upper bounds in long-running processes).
- GPL components (espeak-ng via misaki, phonemizer-fork; FFmpeg as a separate program) are fine for private use; review before distributing the software.
- misaki installs spaCy `en_core_web_sm` by itself on first use.
- `.env.example` was not edited (project rule: no `.env*` files). Add `PEXELS_API_KEY=` there yourself if you want Pexels.
- Platform monetisation eligibility, output quality and views are not guaranteed.

## Needs you

1. Fact-check `content/samples/day-001-research.json` (or replace it), then re-approve and re-export day 1 yourself, or delete that job.
2. Put the real calendar at `content/faceless_content_365_day_calendar.xlsx` and run `faceless calendar import … --dry-run` to check the detected columns.
3. Optional: Pexels key in `.env`; licensed footage/music via `faceless library add`.
4. Optional: ComfyUI: choose a commercially usable SD 1.5 checkpoint (download needs your approval), set `comfyui.*` in `config/local.yaml`, then run `faceless assets JOB --ai-images`.
5. Optional: approve a Chromium download (~150 MB) if you want browser end-to-end tests of the dashboard.
6. Dev container leftovers from Part 1: Claude Code sign-in and the rebuild-persistence check.

# Part 1: Dev container

**Update 26 Nov 2026 09:04: `validate.sh` passed on the laptop inside the dev container
(PASS=22 FAIL=0 WARN=2 SKIP=1, report `data/validation/validate-20261126-090408.txt`):
RTX 4050 visible, real NVENC encode OK, Ollama reachable. `qwen3:4b` was pulled
afterwards. Remaining WARNs: Claude Code sign-in, rebuild-persistence check.**

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

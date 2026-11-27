# facelessCreation
A web application that allow users to create faceless videos

## Faceless Studio: quick start

A local pipeline that turns a topic from your content calendar into an original, narrated
1080x1920 documentary short (45–90 s) for YouTube Shorts, TikTok and Instagram Reels.
Research from your calendar's sources, script by Qwen3 (Ollama), Kokoro narration (9
voices to choose from), **real photos from Wikimedia Commons and Openverse** (AI images from
Stable Diffusion or your own uploads when no photo fits), a cut every ~3 seconds,
Shorts-style word-by-word captions in the language you choose (English by default), FFmpeg
rendering.
No paid APIs. **You approve three things with one click each**: the script, the pictures,
the video. Nothing is approved or published automatically.

### Dashboard (easiest)

```bash
cd backend  && uvicorn faceless_studio.api:app --host 127.0.0.1 --port 8000   # terminal 1
cd frontend && npx ng serve --port 4200                                       # terminal 2
```

Open http://localhost:4200 → **Home** (or **Calendar**) → **Make video**. The video page
guides you through four steps: **Script → Pictures → Video → Publish**. Each approval
starts the next steps automatically; a progress bar shows what is happening. Set your
name and the default subtitle language once in **Settings**.

### Terminal

```bash
faceless make --day 1 [--subtitles it]   # research → script; stops for your check
faceless approve-script JOB --acknowledge-flags
faceless make --day 1                    # voice → pictures; stops for your check
faceless review-assets JOB --confirm-all --approve
faceless make --day 1                    # subtitles → video; stops for your check
faceless approve-final JOB
faceless make --day 1                    # files to publish → data/jobs/JOB/export/
```

Change a picture: `faceless assets JOB --scene s03 --photo | --ai | --upload FILE`.
Change the subtitle language: `faceless set JOB --subtitles it` (en, it, es, fr, de, pt, none).

Optional AI images need the image generator running on the laptop:
`docker compose -f .devcontainer/compose.yaml -f .devcontainer/compose.local.yaml --profile comfyui up -d comfyui`

**Calendar sources:** the "Research / sources" column holds one link per line (Wikipedia
pages and research papers work best). `backend/scripts/fill_calendar_sources.py` fills it
from `content/calendar_source_plan.tsv` (see `content/calendar_sources_report.csv`).

`JOB` is the id printed by `faceless new`; a unique prefix works. `faceless status JOB`
shows the state, what is current, and the exact next command; `faceless resume JOB` runs
the automatic stages up to the next human review gate.

Full step-by-step guide, including a seven-day batch and every review action:
[docs/walkthrough.md](docs/walkthrough.md).

### Commands

| Command | What it does |
|---|---|
| `doctor [--no-network] [--json]` | Tools, filters, real NVENC probe, fonts, models, RAM/VRAM/disk, Ollama/Pexels/ComfyUI |
| `config` | Effective configuration (defaults + `config/local.yaml` + `FACELESS__…` env vars) |
| `calendar import FILE [--sheet S] [--topic-col C] [--date-col C] [--day-col C] [--replace] [--dry-run]` | CSV/XLSX import; rejects duplicate days/dates/topics and empty topics |
| `calendar list [--from-day N] [--to-day N]` | Calendar with job state per day |
| `new --day N \| --date YYYY-MM-DD \| --topic TEXT` | Create a job (one job per calendar day) |
| `list [--state S]`, `status JOB [--json]`, `resume JOB` | Inspect / continue jobs |
| `research init\|add-source\|add-claim\|verify\|fetch\|show JOB …` | Record sources and claims; `fetch` reads a public page's title/excerpt (bounded, no private hosts) |
| `make --day N \| --days A-B \| --from D --to D \| --topic TEXT [--subtitles LANG]` | Everything automatic, stopping only at approvals; run again after each approval |
| `set JOB --subtitles LANG` | Subtitle language for one video (en, it, es, fr, de, pt, none) |
| `research refresh JOB` | Rebuild a job's research from the (re-imported) calendar row or Wikipedia |
| `draft JOB --script FILE \| --text TEXT \| --llm [--research FILE]` | Manual script (`.md`/`.txt`/`.json`) or a local Qwen3 draft from the research |
| `review-script JOB`, `approve-script JOB [--acknowledge-flags] [--yes]` | Show flags; approve (records hashes) |
| `voice JOB [--provider kokoro\|espeak] [--narration FILE]` | Narration + real per-scene timing (espeak = demo only) |
| `library add FILE --license L --commercial-use … `, `library add-manifest`, `list`, `remove` | Your licensed media with provenance |
| `assets JOB [--pexels] [--ai-images] [--rebuild]` | Visual per scene: library match → Pexels → ComfyUI → original graphic |
| `assets JOB --scene S --use LIB_ID \| --procedural [--overlay T]`, `assets JOB --music LIB_ID\|none` | Manual replacement / music |
| `review-assets JOB [--confirm IDS\|--confirm-all] [--reject IDS] [--approve]` | Rights review and approval |
| `subtitles JOB [--model base.en] [--approximate] [--import FILE.srt] [--mark-reviewed]` | Whisper timing on the script text; corrections; review |
| `render JOB [--preview] [--dry-run] [--encoder h264_nvenc\|libx264]`, `preview JOB` | Render + validation (ffprobe, decode, loudness, faststart) |
| `review JOB`, `approve-final JOB`, `export JOB` | Final checklist, approval, export package |
| `batch --from D --to D \| --days 1-7 [--limit N] [--dry-run] [--llm] [--research-dir DIR] [--no-resume] [--concurrency 1]` | Prepare several days; stops at review gates |
| `tracking export [FILE]` | CSV for links/analytics you enter by hand (keeps your edits) |
| `cache stats`, `cache clean [--max-gb N] [--dry-run]`, `gpu` | Disk usage, LRU cleanup, GPU lock/memory |

Exit codes: 0 ok, 1 error, 3 not found, 4 blocked by a review gate, 5 validation failed,
6 provider unavailable, 7 tool failed, 8 resources (RAM/VRAM/GPU lock).

### Dashboard (optional)

```bash
uvicorn faceless_studio.api:app --host 127.0.0.1 --port 8000   # or the VS Code launch config
cd frontend && npx ng serve --host 127.0.0.1 --port 4200         # http://localhost:4200
```

**Making a video in the dashboard:** Calendar → **Make video** on a day. It researches the
topic and writes the script, then the job page says what to approve. Approve in that tab
(type APPROVE), press **Run automatically to the next approval**, repeat. The three stops
are: *Script & research* → *Assets* → *Preview, review & export*.

Calendar, jobs, script/research editing, asset rights review with previews, caption
editing, preview/final players, checklists, approvals (type APPROVE) and export. The API
listens on loopback only and rejects requests without the `X-Faceless` header or with a
non-local Host header. Long stages run one at a time in a single server-side worker.

### Where things live

| Path | Content |
|---|---|
| `backend/` | Python package `faceless_studio` (CLI, pipeline, API) and tests |
| `frontend/` | Angular dashboard |
| `config/default.yaml` | All settings; override in `config/local.yaml` (git-ignored) |
| `content/` | Calendars, research and scripts you write (`content/samples/` has examples) |
| `data/jobs/JOB/` | One folder per job: `script.json`, `research.json`, `voice/`, `assets/`, `captions/`, `render/`, `export/`, `logs/` |
| `data/library/`, `data/cache/`, `data/db/` | Licensed media, TTS/stock caches, SQLite state |

### Tests

```bash
cd backend && pytest -q                 # 97 unit + FFmpeg integration tests (no models needed)
cd backend && pytest -q -m models       # real Kokoro + faster-whisper
cd frontend && npx ng test --watch=false
python backend/scripts/benchmark.py --videos 7   # machine-time benchmark in data/bench
```

Hardware measurements: [HARDWARE.md](HARDWARE.md). Licenses: [LICENSES.md](LICENSES.md).
Status and limitations: [PROGRESS.md](PROGRESS.md).

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

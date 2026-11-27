# Dependency, model and media licenses

Checked on 26 Nov 2026 from installed package metadata, Hugging Face model cards and
`ollama show`. Re-check before commercial use; licenses can change between versions.
This is engineering documentation, not legal advice.

## Models (downloaded locally, never redistributed by this project)

| Model | Used for | License | Commercial use | Notes |
|---|---|---|---|---|
| `hexgrad/Kokoro-82M` (rev `f3ff357179`), voices `af_heart` (default), `af_bella`, `af_nicole`, `am_michael`, `am_fenrir`, `am_puck`, `bf_emma`, `bm_george`, `bm_fable` | narration | Apache-2.0 | yes | Weights and voice packs are in the same Apache-2.0 repo. Voices other than `af_heart` (~0.5 MB each) download on first use from the same revision. |
| `Systran/faster-whisper-small.en` (rev `d1d751a5f8`) | caption timing | MIT | yes | Conversion of OpenAI Whisper (MIT). `base.en` fallback is also MIT. |
| `qwen3:4b` via Ollama (Q4_K_M) | optional script drafts | Apache-2.0 | yes | Drafts are flagged for human fact-checking; the model is not a source. |
| spaCy `en_core_web_sm` 3.8.0 | Kokoro text processing (misaki) | MIT | yes | **Installed automatically by misaki on first Kokoro run** (12.8 MB pip download). |
| `stable-diffusion-v1-5/stable-diffusion-v1-5` → `v1-5-pruned-emaonly.safetensors` (4.27 GB, sha256 `6ce01616…fa2fa`, repo rev `451f4fe161`) | AI images via ComfyUI | CreativeML OpenRAIL-M | yes, with use restrictions | Downloaded 26 Nov 2026 with approval into `models/comfyui/checkpoints/`. The license forbids e.g. deceptive, defamatory or harmful uses and impersonation; outputs are AI-generated and are labelled as such in the export. |

## Photos in videos

| Source | Accepted licences | Credits |
|---|---|---|
| Wikimedia Commons (search API) | Public domain, CC0, CC BY (any version). **Not** CC BY-SA, NC, ND or GFDL: share-alike could require licensing the whole video the same way. | Photographer + licence recorded per scene; CC BY credits are added to the YouTube/TikTok/Instagram texts automatically. |
| Openverse (api.openverse.org, anonymous) | Same rule: CC0, public domain mark, CC BY only (requested from the API and checked again). Photos come from Flickr, StockSnap, Commons and others. | Creator + licence + source page recorded; CC BY credits exported. Openverse's licence data comes from the source sites: check the source page for anything you publish widely. |
| Your uploads | You confirm you own them or may use them commercially. | — |
| AI images (Stable Diffusion 1.5) | CreativeML OpenRAIL-M | Marked as AI-generated in the export. |

## Research data

| Source | License | How it is used |
|---|---|---|
| English Wikipedia (API, only when the calendar's research cell is empty) | CC BY-SA 4.0 text | Short excerpts are passed to the local LLM as background; they are not reproduced in videos. Claims built on them stay flagged until you check them. |
| Pages you list in the calendar | the publisher's terms | Fetched once (public pages only, bounded size) to give the LLM relevant paragraphs. |

## Programs invoked as separate processes

| Program | License | Notes |
|---|---|---|
| FFmpeg 5.1 (Debian build, `--enable-gpl`, libx264, libass) | GPL-2.0+ | Used as an external tool; not linked into this project. |
| NVENC (NVIDIA driver) | NVIDIA driver license | Loaded by FFmpeg from the host driver at runtime. |
| espeak-ng 1.51 | GPL-3.0+ | Demo TTS only (`--provider espeak`), output is marked demo and cannot be approved. |
| Ollama | MIT | Separate container. |
| ComfyUI (optional) | GPL-3.0 | Separate container, local API only. |

## Python packages (main ones)

| Package | Version | License |
|---|---|---|
| kokoro | 0.9.4 | Apache-2.0 |
| misaki | 0.9.4 | Apache-2.0 |
| espeakng-loader | 0.2.4 | bundles espeak-ng (GPL-3.0) as misaki's fallback phonemizer |
| phonemizer-fork | 3.3.2 | GPL-3.0+ |
| num2words | 0.5.14 | LGPL |
| torch (CPU) | 2.15.0 | BSD-3-Clause (+ bundled third-party licenses) |
| faster-whisper | 1.2.1 | MIT |
| ctranslate2 | 4.9.1 | MIT |
| spacy | 3.8.18 | MIT |
| transformers | 5.24.1 | Apache-2.0 |
| huggingface-hub | 2.4.0 | Apache-2.0 |
| onnxruntime | 1.32.0 | MIT |
| numpy | 2.4.6 | BSD-3-Clause and others |
| soundfile | 0.14.0 | BSD-3-Clause |
| fastapi | 0.165.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| typer / rich | 0.29.3 / 15.0.0 | MIT |
| pydantic | 2.14.4 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| openpyxl | 3.1.5 | MIT |
| PyYAML | 6.0.4 | MIT |

The GPL/LGPL components are used privately on this machine. If you ever **distribute**
this software (not just its videos), review those obligations first. Videos you
render are not derivative works of these tools.

## Frontend

Angular 22 (MIT), RxJS (Apache-2.0), TypeScript (Apache-2.0), Vitest (MIT). Full list:
`cd frontend && npx license-checker --summary` (not installed by default).

## Media in rendered videos

| Source | License recorded in the job's `sources.json` |
|---|---|
| Original procedural graphics | Original work generated by Faceless Studio (owned by the channel) |
| Font: DejaVu Sans Bold (`fonts-dejavu-core` 2.37) | Bitstream Vera license + public domain changes: free, commercial use allowed |
| Library media you import | Whatever you record with `faceless library add --license …` (required) |
| Pexels (optional) | Pexels License (free, attribution appreciated not required; no selling unaltered copies) |
| Background music (optional) | Only tracks you import with a commercial, cross-platform license |

No third-party stock footage or music was used in the test videos produced so far.

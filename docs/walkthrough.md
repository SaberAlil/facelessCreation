# Walkthrough: one video, then a weekly batch

All commands run inside the dev container from the repository root. Replace `JOB` with
the id that `faceless new` prints (any unique prefix works). Every command that is
blocked tells you why and prints the next command.

## 0. Check the machine

```bash
faceless doctor
```

Fix anything marked `fail`. `warn` on a model only means it downloads on first use.

## 1. Calendar and job

```bash
faceless calendar import content/faceless_content_365_day_calendar.xlsx --dry-run  # shows sheets + column mapping
faceless calendar import content/faceless_content_365_day_calendar.xlsx
faceless calendar list --to-day 7
faceless new --day 1          # or: --date 2027-01-01, or --topic "free topic"
```

The importer finds the header row and the day/date/topic columns, keeps the original
topic text and spreadsheet row, and refuses duplicate days, dates or topics. Use
`--sheet`, `--topic-col`, `--date-col`, `--day-col` if detection picks the wrong ones.
No calendar yet? Try `content/samples/calendar.csv`.

## 2. Research (you, not the model)

```bash
faceless research add-source JOB --id S1 --title "Paper or article title" \
    --url https://… --publisher "Journal / site" --published 2005-06
faceless research add-claim JOB --id R1 --text "What the source supports" --sources S1
faceless research verify JOB R1          # only after you read it in the source
faceless research show JOB               # lists missing URLs/dates/unverified claims
```

Or write `data/jobs/JOB/research.json` directly (see
`content/samples/day-001-research.json`), or pass `--research FILE` to `draft`.
`faceless research fetch JOB URL --id S2` pulls a public page's title and a short
excerpt as a candidate; verify it yourself.

## 3. Script

Write it yourself:

```bash
faceless draft JOB --script content/scripts/day-001.md
```

Format (see `content/samples/day-001-script.md`): `# Title`, optional `Hook:`,
`Claim C1: text [R1]` lines, platform lines (`YouTube title:`, `TikTok:`, …,
`Hashtags:`), then one paragraph per scene with an optional metadata line
`> visual: … | terms: a, b | overlay: … | claims: C1`.

Or let the local model draft from the research (GPU, about 1–1.5 minutes):

```bash
faceless draft JOB --llm
```

It writes prose first (with length feedback, at most 3 attempts), then structures it.
It answers "insufficient research" instead of padding when the research is thin. Every
claim it writes is flagged.

## 4. Review and approve the script (gate 1)

```bash
faceless review-script JOB           # also writes data/jobs/JOB/review/script_review.md
```

Check every claim against its source. Fix the script (edit `data/jobs/JOB/script.json`
or re-run `draft`), clear `needs_review` on claims you verified, then:

```bash
faceless approve-script JOB                      # asks you to type APPROVE
faceless approve-script JOB --acknowledge-flags  # if flagged claims remain and you checked them
```

Approval records the hash of the script and research. Any later edit withdraws it.

## 5. Narration

```bash
faceless voice JOB                    # Kokoro af_heart on CPU, ~20 s for a 70 s script
faceless voice JOB --narration my-recording.wav   # or your own voice
```

Scene timing comes from the real audio. Narration is normalised to -16 LUFS; the
final mix is normalised to -14 LUFS / -1 dBTP.

## 6. Visuals and rights review (gate 2)

Add media you own or have licensed (once; they are reused across videos):

```bash
faceless library add ~/footage/calculator.mp4 --license "own work" --commercial-use \
    --creator "Me" --tags calculator,budget,money
faceless library add photo.jpg --license "CC0-1.0" --commercial-use \
    --source-url https://… --creator "Name"
```

Then:

```bash
faceless assets JOB                   # library match by tags → original graphics
faceless assets JOB --pexels          # optional, needs PEXELS_API_KEY in .env
faceless review-assets JOB            # table of license, creator, rights; previews in data/jobs/JOB/assets
faceless assets JOB --scene s03 --use v1a2b3c4d5e      # replace one scene
faceless assets JOB --scene s03 --procedural --overlay "Round up"
faceless review-assets JOB --confirm s01,s02 --reject s03
faceless review-assets JOB --confirm-all --approve     # after checking every license
```

## 7. Captions

```bash
faceless subtitles JOB
```

Caption text is the approved script (correct spelling and numbers); timing comes from
faster-whisper on the real narration. Mismatches are listed (for example a name Kokoro
pronounces oddly). To correct: edit `data/jobs/JOB/captions/captions.srt` and run
`faceless subtitles JOB --import data/jobs/JOB/captions/captions.srt`.
If Whisper can't run, `--approximate` spreads words over each scene's measured audio;
then check it and run `--mark-reviewed`.

## 8. Render and final approval (gate 3)

```bash
faceless render JOB --preview         # 540x960, quick look
faceless render JOB                   # 1080x1920 30 fps H.264/AAC, faststart
faceless review JOB                   # checklist + file paths
```

Watch the whole `render/final.mp4`. Then:

```bash
faceless approve-final JOB
faceless export JOB
```

`approve-final` is refused while a required check fails (stale or unapproved stages,
demo voice, unreviewed approximate captions, failed validation, length outside 45–90 s).

## 9. Export and publish (manual)

`data/jobs/JOB/export/` contains the MP4, cover PNG, SRT, `youtube.txt`, `tiktok.txt`,
`instagram.txt` (title/captions/hashtags/credits plus a disclosure reminder about the
synthetic voice), `sources.json`, `metadata.json` (stage times, encoder, validation) and
`review.json` (who approved what, with hashes). Upload by hand and set each platform's
AI-content disclosure where its rules require it.

Track results by hand:

```bash
faceless tracking export data/tracking.csv     # re-running keeps the columns you filled in
```

## Weekly batch (seven videos)

```bash
faceless batch --from 2027-01-01 --to 2027-01-07 --dry-run
faceless batch --from 2027-01-01 --to 2027-01-07 --llm --research-dir content/research
```

The batch creates one job per calendar day (never twice), loads
`content/research/day-NNN.json` if present, drafts with the local model when there is
research, and runs automatic stages until each job reaches a review gate. It never
approves or publishes. Then, for each job:

1. `faceless review-script JOB` → fix → `faceless approve-script JOB`
2. `faceless resume JOB` (narration + visuals) → `faceless review-assets JOB … --approve`
3. `faceless resume JOB` (captions + render) → watch → `faceless approve-final JOB`
4. `faceless resume JOB` (export)

Re-run the same `batch` command at any point: finished stages are skipped and failed
stages are retried. Measured machine time is about one minute per video on this laptop
(see HARDWARE.md); editorial time is extra and depends on you.

## When something fails

- `faceless status JOB` shows the failed stage, its error, and the next command.
  Earlier work is kept; fix the cause and run the stage again (or `faceless resume JOB`).
- `data/jobs/JOB/logs/` and `data/jobs/JOB/render/ffmpeg.log` have the tool output.
- `faceless gpu` shows who holds the GPU lock. Stop ComfyUI before drafting or rendering.
- Out of disk: `faceless cache stats`, then `faceless cache clean --max-gb 5`.

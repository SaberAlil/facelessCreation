"""Measure machine time for N full videos on this machine (no editorial time).

Runs in an isolated data dir (default data/bench) so benchmark jobs never mix with real
ones. Review gates are passed with reviewer "benchmark" — these videos are NOT approved
content. The TTS cache is cleared before each video so every narration is synthesized.

    python backend/scripts/benchmark.py --videos 1
    python backend/scripts/benchmark.py --videos 7
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", type=int, default=1)
    ap.add_argument("--script", type=Path, default=REPO / "content/samples/day-001-script.md")
    ap.add_argument("--data-dir", type=Path, default=REPO / "data/bench")
    ap.add_argument("--encoder", default=None, choices=[None, "h264_nvenc", "libx264"])
    args = ap.parse_args()

    os.environ["FACELESS__PATHS__DATA_DIR"] = str(args.data_dir)
    from faceless_studio.config import load_settings
    from faceless_studio.jobs import Studio
    from faceless_studio.pipeline import Pipeline
    from faceless_studio.resources import meminfo_mib, query_gpu
    from faceless_studio.schemas import RightsStatus

    s = load_settings(root=REPO)
    pl = Pipeline(Studio(s))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    results, failures = [], []
    t_all = time.monotonic()
    for i in range(1, args.videos + 1):
        shutil.rmtree(s.cache_dir / "tts", ignore_errors=True)
        t0 = time.monotonic()
        jid = pl.studio.create_job(f"Benchmark {stamp} video {i}").id
        try:
            pl.draft(jid, script_file=args.script)
            pl.approve_script(jid, "benchmark", acknowledge_flags=True,
                              notes="benchmark run, not editorial approval")
            pl.voice(jid)
            pl.assets(jid)
            pl.set_rights(jid, None, RightsStatus.CONFIRMED)
            pl.approve_assets(jid, "benchmark")
            pl.subtitles(jid)
            pl.render(jid, encoder=args.encoder)
        except Exception as e:  # record and continue: failures are part of the result
            failures.append({"video": i, "job": jid, "error": f"{type(e).__name__}: {e}"})
        runs = pl.studio.stage_runs(jid)
        final = pl.paths(jid).final
        results.append({
            "video": i, "job": jid, "wall_s": round(time.monotonic() - t0, 1),
            "stages": {r["stage"]: {"s": round(r["duration_s"] or 0, 1), "device": r["device"],
                                    "rss_mib": r["peak_rss_mib"],
                                    "child_rss_mib": r["peak_children_rss_mib"],
                                    "vram_used_mib": r["peak_vram_used_mib"],
                                    "status": r["status"], "fallbacks": r["fallbacks"]}
                       for r in runs if r["stage"] != "assets" or r["duration_s"]},
            "video_s": pl.timing(jid).duration if pl.timing(jid) else None,
            "mp4_bytes": final.stat().st_size if final.is_file() else 0,
        })
        print(json.dumps(results[-1]), flush=True)
    gpu = query_gpu()
    report = {
        "when": stamp, "videos": args.videos, "total_wall_s": round(time.monotonic() - t_all, 1),
        "failures": failures, "results": results,
        "job_dirs_bytes": sum(f.stat().st_size for f in (args.data_dir / "jobs").rglob("*")
                              if f.is_file()),
        "machine": {"gpu": gpu.name if gpu else None, "ram_total_mib":
                    meminfo_mib().get("MemTotal")},
    }
    out = args.data_dir / f"benchmark-{stamp}-{args.videos}.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"report: {out}")
    print(f"total {report['total_wall_s']}s for {args.videos} video(s); failures: {len(failures)}")


if __name__ == "__main__":
    main()

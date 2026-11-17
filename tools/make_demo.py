#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Render the before/after media for demo/index.html.

    python tools/make_demo.py            # synthetic cautery-smoke clip
    scripts/fetch_sample.sh && python tools/make_demo.py   # + real CC BY laparoscopy clip

Writes demo/media/*.mp4 (H.264, browser-playable) and demo/media/clips.js (metadata and per-frame
statistics, loaded as a script so the viewer also works from file://).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import synth  # noqa: E402

MEDIA = ROOT / "demo" / "media"


def sh(*cmd):
    subprocess.run([str(c) for c in cmd], check=True)


def encode(frames_dir: Path, pattern: str, fps: float, out: Path, width: int) -> None:
    sh("ffmpeg", "-y", "-loglevel", "error", "-framerate", fps, "-i", frames_dir / pattern,
       "-vf", f"scale={width}:-2:flags=lanczos", "-c:v", "libx264", "-preset", "slow", "-crf", "23",
       "-pix_fmt", "yuv420p", "-movflags", "+faststart", out)


def process(cli: Path, src: Path, out_dir: Path, report: Path) -> dict:
    sh(cli, "--input", src, "--output", out_dir, "--report", report, "--warmup", "0")
    return json.loads(report.read_text())


def clip_entry(key, title, subtitle, credit, fps, rep):
    fr = rep["frames"]
    return {
        "key": key, "title": title, "subtitle": subtitle, "credit": credit, "fps": fps,
        "before": f"media/{key}_before.mp4", "after": f"media/{key}_after.mp4",
        "width": fr[0]["width"], "height": fr[0]["height"],
        "summary": rep["summary"], "threads": rep["threads"],
        "series": {
            "total_ms": [round(f["total_ms"], 2) for f in fr],
            "haze": [round(f["haze_level"], 3) for f in fr],
            "glare": [round(f["glare_fraction"] * 100, 3) for f in fr],
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", type=Path, default=ROOT / "build" / "sve_cli")
    ap.add_argument("--frames", type=int, default=150)
    ap.add_argument("--width", type=int, default=960, help="encoded width of demo videos")
    args = ap.parse_args()

    MEDIA.mkdir(parents=True, exist_ok=True)
    clips = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # 1) Synthetic cautery puff: smoke rises and clears, with specular glare throughout.
        synth.generate(tmp / "syn", [synth.SeqSpec("demo", smoke=0.7, glare=True, smoke_ramp=True,
                                                   frames=args.frames, seed=11)], 1280, 720)
        rep = process(args.cli, tmp / "syn" / "demo" / "degraded", tmp / "syn_out", tmp / "syn.json")
        encode(tmp / "syn" / "demo" / "degraded", "%03d.png", 30, MEDIA / "synthetic_before.mp4", args.width)
        encode(tmp / "syn_out", "%03d.png", 30, MEDIA / "synthetic_after.mp4", args.width)
        clips.append(clip_entry(
            "synthetic", "Cautery smoke + glare", "Synthetic, 1280×720",
            "Rendered by tools/synth.py (MIT). Ground truth available, used for verification.", 30, rep))

        # 2) Real laparoscopy footage, if fetched (scripts/fetch_sample.sh).
        real = ROOT / "data" / "real" / "laparoscopy.mp4"
        if real.exists():
            import cv2
            fps = cv2.VideoCapture(str(real)).get(cv2.CAP_PROP_FPS) or 25
            src = tmp / "real_in"
            src.mkdir()
            sh("ffmpeg", "-loglevel", "error", "-i", real, src / "%05d.png")
            rep = process(args.cli, src, tmp / "real_out", tmp / "real.json")
            encode(src, "%05d.png", fps, MEDIA / "real_before.mp4", min(args.width, 640))
            encode(tmp / "real_out", "%05d.png", fps, MEDIA / "real_after.mp4", min(args.width, 640))
            clips.append(clip_entry(
                "real", "Laparoscopic cholecystectomy", "Real footage, 640×480",
                "Boer, Boerma & de Vries Reilingh, J Med Case Reports 5:588 (2011), CC BY 2.0, "
                "via Wikimedia Commons. Re-encoded and processed.", round(fps, 3), rep))

    (MEDIA / "clips.js").write_text("window.SVE_CLIPS = " + json.dumps(clips, indent=1) + ";\n")
    print(f"wrote {len(clips)} clip(s) to {MEDIA}")


if __name__ == "__main__":
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is required")
    main()

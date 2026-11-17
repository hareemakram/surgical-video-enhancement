#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic laparoscopic test footage with exact ground truth.

Real surgical video has no "clean" reference: nobody can film the same scene with and without
smoke. To measure enhancement quality objectively, this generator renders a tissue scene and then
applies the two degradations the pipeline targets, using the standard physical models:

* surgical smoke:  I = J * t + A * (1 - t)      (atmospheric scattering model, Koschmieder)
* specular glare:  I = J + S, clipped to 255     (wet tissue reflecting the scope light)

Every degraded frame is written next to its clean frame (and a binary glare mask), so tests can
compute full-reference metrics (PSNR, SSIM, CIEDE2000) against ground truth.

Output layout (PNG, lossless)::

    <out>/<sequence>/clean/000.png
    <out>/<sequence>/degraded/000.png
    <out>/<sequence>/glare_mask/000.png
    <out>/<sequence>/meta.json

The renderer is deterministic for a given seed (REQ-VER-001).
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class SeqSpec:
    name: str
    smoke: float          # peak smoke density, 0 = none, ~0.7 = heavy
    glare: bool           # add specular highlights
    motion: bool = True   # camera pans across the scene
    smoke_ramp: bool = False  # smoke density rises and falls (cautery puff) instead of constant
    frames: int = 30
    seed: int = 0


STANDARD_SEQUENCES = [
    SeqSpec("clean", smoke=0.0, glare=False, seed=1),
    SeqSpec("smoke_light", smoke=0.35, glare=False, seed=2),
    SeqSpec("smoke_heavy", smoke=0.65, glare=False, seed=3),
    SeqSpec("glare", smoke=0.0, glare=True, seed=4),
    SeqSpec("smoke_glare", smoke=0.5, glare=True, seed=5),
    SeqSpec("static_smoke", smoke=0.5, glare=False, motion=False, seed=6),
]


def _noise(rng: np.random.Generator, h: int, w: int, octaves=(64, 24, 8), weights=(0.6, 0.3, 0.1)) -> np.ndarray:
    """Band-limited fractal noise in [0, 1]."""
    acc = np.zeros((h, w), np.float32)
    for size, wt in zip(octaves, weights):
        n = rng.standard_normal((h, w)).astype(np.float32)
        n = cv2.GaussianBlur(n, (0, 0), size / 2.0)
        n /= n.std() + 1e-6
        acc += wt * n
    acc -= acc.min()
    acc /= acc.max() + 1e-6
    return acc


class Scene:
    """A large tissue canvas; frames are crops of it (camera motion) seen through a circular scope."""

    def __init__(self, width: int, height: int, seed: int):
        self.w, self.h = width, height
        rng = np.random.default_rng(seed)
        self.rng = rng
        cw, ch = int(width * 1.6), int(height * 1.6)
        self.cw, self.ch = cw, ch

        base = _noise(rng, ch, cw)
        fat = np.clip((_noise(rng, ch, cw, (120, 40), (0.8, 0.2)) - 0.62) * 4.0, 0, 1)
        # Tissue colour palette (BGR, linear 0..1): pink/red mucosa with yellowish fat patches.
        b = 0.22 + 0.18 * base
        g = 0.20 + 0.22 * base
        r = 0.55 + 0.35 * base
        tissue = np.dstack([b, g, r])
        fat_col = np.array([0.35, 0.70, 0.85], np.float32)
        tissue = tissue * (1 - fat[..., None] * 0.8) + fat_col * (fat[..., None] * 0.8)

        # Blood vessels: random smooth dark-red curves.
        vessels = np.zeros((ch, cw), np.float32)
        for _ in range(40):
            pts = [rng.uniform([0, 0], [cw, ch])]
            ang = rng.uniform(0, 2 * np.pi)
            for _ in range(rng.integers(6, 14)):
                ang += rng.normal(0, 0.5)
                pts.append(pts[-1] + 40 * np.array([np.cos(ang), np.sin(ang)]))
            th = int(rng.integers(1, 5))
            cv2.polylines(vessels, [np.array(pts, np.int32)], False, 1.0, th, cv2.LINE_AA)
        vessels = cv2.GaussianBlur(vessels, (0, 0), 1.2)
        vcol = np.array([0.10, 0.08, 0.45], np.float32)
        tissue = tissue * (1 - 0.7 * vessels[..., None]) + vcol * (0.7 * vessels[..., None])

        # Surface relief shading.
        height_map = cv2.GaussianBlur(base, (0, 0), 6)
        gy, gx = np.gradient(height_map)
        shade = np.clip(1.0 + 25.0 * (gx - gy), 0.75, 1.15)
        self.tissue = np.clip(tissue * shade[..., None], 0, 1).astype(np.float32)

        # Specular spots live on the tissue surface (they move with it).
        n_spots = 60
        self.spots = np.column_stack([
            rng.uniform(0, cw, n_spots), rng.uniform(0, ch, n_spots),
            rng.uniform(2.0, 9.0, n_spots), rng.uniform(1.0, 3.0, n_spots), rng.uniform(0, np.pi, n_spots),
        ])

        # Smoke density field, larger than the frame so it can drift independently of the tissue.
        self.smoke_field = _noise(rng, ch, cw, (90, 30), (0.75, 0.25))

        # Scope optics: circular field of view and light fall-off towards the rim.
        yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
        cx, cy = width / 2, height / 2
        rad = 0.53 * height if width > height else 0.48 * min(width, height)
        rr = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / rad
        self.fov = (rr <= 1.0).astype(np.float32)
        self.fov = cv2.GaussianBlur(self.fov, (0, 0), 1.0)
        self.light = (1.05 - 0.45 * rr ** 2).clip(0.3, 1.1)

    def offset(self, i: int, motion: bool) -> tuple[int, int]:
        if not motion:
            return (self.cw - self.w) // 2, (self.ch - self.h) // 2
        t = i / 30.0
        ox = (self.cw - self.w) / 2 + (self.cw - self.w) * 0.35 * np.sin(0.9 * t)
        oy = (self.ch - self.h) / 2 + (self.ch - self.h) * 0.35 * np.sin(0.6 * t + 1.0)
        return int(ox), int(oy)

    def render(self, i: int, spec: SeqSpec):
        ox, oy = self.offset(i, spec.motion)
        crop = self.tissue[oy:oy + self.h, ox:ox + self.w]
        clean = crop * self.light[..., None]

        glare = np.zeros((self.h, self.w), np.float32)
        if spec.glare:
            for x, y, sx, sy, ang in self.spots:
                px, py = x - ox, y - oy
                if -20 < px < self.w + 20 and -20 < py < self.h + 20:
                    # Highlights shimmer slightly as the tissue breathes.
                    s = 1.0 + 0.15 * np.sin(0.7 * i + x)
                    cv2.ellipse(glare, ((float(px), float(py)), (float(sx * 2 * s), float(sy * 2 * s)),
                                        float(np.degrees(ang))), 1.0, -1, cv2.LINE_AA)
            glare = cv2.GaussianBlur(glare, (0, 0), 1.5) * 1.6
        glare *= self.fov

        degraded = clean + glare[..., None]
        if spec.smoke > 0:
            if spec.smoke_ramp:
                density = spec.smoke * np.clip(np.sin(np.pi * i / max(1, spec.frames - 1)) * 1.3, 0, 1)
            else:
                density = spec.smoke
            # Smoke drifts upward/right independently of the camera.
            sx = (ox + int(i * 2.0)) % (self.cw - self.w)
            sy = (oy + int(i * 1.2)) % (self.ch - self.h)
            field = self.smoke_field[sy:sy + self.h, sx:sx + self.w]
            t = 1.0 - density * (0.55 + 0.45 * field)
            airlight = np.array([0.88, 0.88, 0.90], np.float32) * self.light[..., None] ** 0.3
            degraded = degraded * t[..., None] + airlight * (1.0 - t[..., None])

        noise = self.rng.normal(0, 1.2 / 255, degraded.shape).astype(np.float32)
        degraded = degraded + noise

        fov3 = self.fov[..., None]
        to8 = lambda a: np.clip(a * fov3 * 255.0 + 0.5, 0, 255).astype(np.uint8)
        mask = ((glare > 0.25) * 255).astype(np.uint8)
        return to8(clean), to8(degraded), mask


def generate(out: Path, specs, width: int, height: int) -> None:
    for spec in specs:
        scene = Scene(width, height, spec.seed)
        root = out / spec.name
        for sub in ("clean", "degraded", "glare_mask"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        for i in range(spec.frames):
            clean, degraded, mask = scene.render(i, spec)
            cv2.imwrite(str(root / "clean" / f"{i:03d}.png"), clean)
            cv2.imwrite(str(root / "degraded" / f"{i:03d}.png"), degraded)
            cv2.imwrite(str(root / "glare_mask" / f"{i:03d}.png"), mask)
        meta = asdict(spec) | {"width": width, "height": height}
        (root / "meta.json").write_text(json.dumps(meta, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("data/synthetic"))
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--frames", type=int, default=None, help="override frames per sequence")
    ap.add_argument("--demo", action="store_true", help="render one long cautery-smoke clip for the demo")
    args = ap.parse_args()

    if args.demo:
        specs = [SeqSpec("demo_cautery", smoke=0.7, glare=True, smoke_ramp=True,
                         frames=args.frames or 180, seed=11)]
    else:
        specs = STANDARD_SEQUENCES
        if args.frames:
            specs = [SeqSpec(**(asdict(s) | {"frames": args.frames})) for s in specs]
    generate(args.out, specs, args.width, args.height)
    print(f"wrote {len(specs)} sequence(s) to {args.out}")


if __name__ == "__main__":
    main()

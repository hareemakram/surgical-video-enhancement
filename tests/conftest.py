# SPDX-License-Identifier: MIT
"""Shared fixtures: locate the C++ binary, render synthetic ground-truth footage, run the CLI."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import synth  # noqa: E402

# Frames per synthetic sequence. Enough for stable statistics, small enough for CI.
FRAMES = int(os.environ.get("SVE_TEST_FRAMES", "20"))


def pytest_collection_modifyitems(items):
    """Copy @pytest.mark.req IDs into the JUnit report so tools/trace_report.py can map them."""
    for item in items:
        ids = [i for m in item.iter_markers("req") for i in m.args]
        if ids:
            item.user_properties.append(("requirements", ",".join(sorted(set(ids)))))


@pytest.fixture(scope="session")
def cli() -> Path:
    exe = Path(os.environ.get("SVE_CLI", ROOT / "build" / "sve_cli"))
    if not exe.exists():
        pytest.exit(f"sve_cli not found at {exe}; build with cmake first (see README)", returncode=3)
    return exe


@pytest.fixture(scope="session")
def data_720p(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("synthetic_720p")
    specs = [synth.SeqSpec(**(vars(s) | {"frames": FRAMES})) for s in synth.STANDARD_SEQUENCES]
    synth.generate(out, specs, 1280, 720)
    return out


@pytest.fixture(scope="session")
def data_1080p(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("synthetic_1080p")
    synth.generate(out, [synth.SeqSpec("smoke_glare", smoke=0.5, glare=True, frames=10, seed=5)], 1920, 1080)
    return out


class Runner:
    def __init__(self, exe: Path, tmp: Path):
        self.exe, self.tmp, self.n = exe, tmp, 0

    def __call__(self, inp: Path, *args: str, output: bool = True, check: bool = True):
        """Run sve_cli on `inp`; return (report dict, output dir or None, CompletedProcess)."""
        self.n += 1
        out_dir = self.tmp / f"run{self.n}" if output else None
        report = self.tmp / f"run{self.n}.json"
        cmd = [str(self.exe), "--input", str(inp), "--report", str(report), *args]
        if out_dir is not None:
            cmd += ["--output", str(out_dir)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if check:
            assert proc.returncode == 0, proc.stderr
        rep = json.loads(report.read_text()) if report.exists() else None
        return rep, out_dir, proc


@pytest.fixture
def run(cli, tmp_path) -> Runner:
    return Runner(cli, tmp_path)


def load_frames(d: Path) -> list[np.ndarray]:
    return [cv2.imread(str(p), cv2.IMREAD_UNCHANGED) for p in sorted(d.glob("*.png"))]

# SPDX-License-Identifier: MIT
"""Interfaces: frame I/O formats, frame count/size preservation, report schema."""
import json
import subprocess

import cv2
import numpy as np
import pytest

from conftest import load_frames


@pytest.mark.req("REQ-IF-001")
def test_frame_count_and_size_preserved(run, data_720p):
    src = load_frames(data_720p / "smoke_light" / "degraded")
    _, out_dir, _ = run(data_720p / "smoke_light" / "degraded", "--warmup", "0")
    out = load_frames(out_dir)
    assert len(out) == len(src)
    assert all(o.shape == s.shape and o.dtype == s.dtype for o, s in zip(out, src))


@pytest.mark.req("REQ-IF-001")
def test_video_file_roundtrip(cli, data_720p, tmp_path):
    frames = load_frames(data_720p / "smoke_light" / "degraded")
    src = tmp_path / "in.avi"
    vw = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"MJPG"), 25, (1280, 720))
    for f in frames:
        vw.write(f)
    vw.release()
    dst = tmp_path / "out.avi"
    p = subprocess.run([str(cli), "--input", str(src), "--output", str(dst)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    cap = cv2.VideoCapture(str(dst))
    n = 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        assert f.shape == (720, 1280, 3)
        n += 1
    assert n == len(frames)


@pytest.mark.req("REQ-IF-002")
def test_grayscale_input_supported(run, data_720p, tmp_path):
    d = tmp_path / "gray"
    d.mkdir()
    g = cv2.cvtColor(load_frames(data_720p / "smoke_heavy" / "degraded")[0], cv2.COLOR_BGR2GRAY)
    cv2.imwrite(str(d / "000.png"), g)
    rep, out_dir, _ = run(d, "--warmup", "0")
    out = load_frames(out_dir)[0]
    assert out.shape == g.shape and out.ndim == 2
    assert not rep["frames"][0]["bypassed"]


@pytest.mark.req("REQ-IF-003")
def test_report_schema(run, data_720p):
    rep, _, _ = run(data_720p / "clean" / "degraded", "--warmup", "2", output=False)
    for k in ("opencv_version", "threads", "summary", "frames"):
        assert k in rep
    for k in ("frames", "mean_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms"):
        assert isinstance(rep["summary"][k], (int, float))
    frame_keys = {"name", "width", "height", "total_ms", "fov_ms", "glare_ms", "dehaze_ms", "contrast_ms",
                  "apply_ms", "glare_fraction", "haze_level", "bypassed", "bypass_reason"}
    assert all(frame_keys <= set(f) for f in rep["frames"])


@pytest.mark.req("REQ-IF-003")
def test_invalid_arguments_rejected(cli):
    p = subprocess.run([str(cli), "--bogus"], capture_output=True, text=True)
    assert p.returncode == 2
    p = subprocess.run([str(cli), "--input", "/nonexistent.mp4"], capture_output=True, text=True)
    assert p.returncode == 1

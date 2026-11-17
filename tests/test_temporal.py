# SPDX-License-Identifier: MIT
"""Temporal stability: enhancement must not introduce flicker."""
import cv2
import numpy as np
import pytest

from conftest import load_frames
import metrics as M


def _mean_luma(frames, mask):
    return np.array([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)[mask].mean() for f in frames])


@pytest.mark.req("REQ-PRF-003")
def test_no_flicker_on_static_scene(run, data_720p, record_property):
    deg = load_frames(data_720p / "static_smoke" / "degraded")
    _, out_dir, _ = run(data_720p / "static_smoke" / "degraded", "--warmup", "0")
    out = load_frames(out_dir)
    mask = M.fov_mask(deg[0])
    # Skip the first frames while the airlight estimate settles from its initial value.
    li, lo = _mean_luma(deg, mask)[3:], _mean_luma(out, mask)[3:]
    jitter_in = np.abs(np.diff(li)).mean()
    jitter_out = np.abs(np.diff(lo)).mean()
    record_property("frame_to_frame_luma_change_in", round(float(jitter_in), 3))
    record_property("frame_to_frame_luma_change_out", round(float(jitter_out), 3))
    assert jitter_out <= max(1.0, 2.0 * jitter_in), "output brightness must not flicker"


@pytest.mark.req("REQ-PRF-003")
def test_no_flicker_on_moving_scene(run, data_720p, record_property):
    """With camera motion, the per-pixel output change must track the input change."""
    deg = load_frames(data_720p / "smoke_light" / "degraded")
    _, out_dir, _ = run(data_720p / "smoke_light" / "degraded", "--warmup", "0")
    out = load_frames(out_dir)
    mask = M.fov_mask(deg[0])
    li, lo = _mean_luma(deg, mask), _mean_luma(out, mask)
    record_property("luma_std_in", round(float(li.std()), 3))
    record_property("luma_std_out", round(float(lo.std()), 3))
    assert lo.std() <= 2.0 * li.std() + 1.0

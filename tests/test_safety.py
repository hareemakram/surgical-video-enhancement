# SPDX-License-Identifier: MIT
"""Safety-related behaviour: fail-safe pass-through, FOV integrity, colour fidelity, determinism."""
import cv2
import numpy as np
import pytest

from conftest import load_frames
import metrics as M


@pytest.mark.req("REQ-SAF-001")
@pytest.mark.parametrize("kind", ["black", "white", "tiny", "uint16", "bgra"])
def test_fail_safe_passthrough(run, tmp_path, kind, record_property):
    d = tmp_path / "in"
    d.mkdir()
    img = {
        "black": np.zeros((720, 1280, 3), np.uint8),
        "white": np.full((720, 1280, 3), 255, np.uint8),
        "tiny": np.full((8, 8, 3), 128, np.uint8),
        "uint16": np.full((64, 64, 3), 30000, np.uint16),
        "bgra": np.full((64, 64, 4), 100, np.uint8),
    }[kind]
    cv2.imwrite(str(d / "000.png"), img)
    rep, out_dir, proc = run(d, "--warmup", "0")
    out = load_frames(out_dir)
    assert proc.returncode == 0
    assert len(out) == 1 and out[0].shape == img.shape and out[0].dtype == img.dtype
    f = rep["frames"][0]
    record_property("bypassed", f["bypassed"])
    record_property("bypass_reason", f["bypass_reason"])
    if kind in ("tiny", "uint16", "bgra"):
        # Unsupported input: must be passed through bit-exact and flagged.
        assert f["bypassed"]
        assert np.array_equal(out[0], img)
    else:
        # Degenerate but valid content: processed without artefacts.
        assert not f["bypassed"]
        assert np.abs(out[0].astype(int) - img.astype(int)).max() <= 1


@pytest.mark.req("REQ-SAF-002")
def test_outside_fov_is_bit_exact(run, data_720p):
    _, out_dir, _ = run(data_720p / "smoke_glare" / "degraded", "--warmup", "0")
    for d, o in zip(load_frames(data_720p / "smoke_glare" / "degraded"), load_frames(out_dir)):
        outside = cv2.cvtColor(d, cv2.COLOR_BGR2GRAY) < 6
        outside = cv2.erode(outside.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
        assert outside.mean() > 0.2, "test frames must have a black border"
        assert np.array_equal(d[outside], o[outside])


@pytest.mark.req("REQ-SAF-003")
@pytest.mark.parametrize("seq", ["clean", "glare", "smoke_heavy"])
def test_tissue_hue_preserved(run, data_720p, seq, record_property):
    """Tissue colour is diagnostic (perfusion, ischaemia, bile). The full pipeline must not shift
    hue: compare against the ground-truth clean frame."""
    _, out_dir, _ = run(data_720p / seq / "degraded", "--warmup", "0")
    clean = load_frames(data_720p / seq / "clean")
    out = load_frames(out_dir)
    dh = np.mean([M.hue_shift_deg(c, o, M.fov_mask(c)) for c, o in zip(clean, out)])
    record_property("mean_hue_shift_deg", round(float(dh), 2))
    assert dh <= 5.0


@pytest.mark.req("REQ-SAF-004")
def test_deterministic_output(run, data_720p):
    _, a, _ = run(data_720p / "smoke_glare" / "degraded", "--warmup", "0")
    _, b, _ = run(data_720p / "smoke_glare" / "degraded", "--warmup", "0")
    for x, y in zip(load_frames(a), load_frames(b)):
        assert np.array_equal(x, y)


@pytest.mark.req("REQ-FUN-005")
def test_all_stages_disabled_is_identity(run, data_720p):
    _, out_dir, _ = run(data_720p / "smoke_glare" / "degraded", "--no-glare", "--no-dehaze", "--no-contrast", "--warmup", "0")
    for d, o in zip(load_frames(data_720p / "smoke_glare" / "degraded"), load_frames(out_dir)):
        assert np.array_equal(d, o)


@pytest.mark.req("REQ-FUN-005")
@pytest.mark.parametrize("flag", ["--no-glare", "--no-dehaze", "--no-contrast"])
def test_each_stage_can_be_disabled(run, data_720p, flag):
    full, _, _ = run(data_720p / "smoke_glare" / "degraded", "--warmup", "0", output=False)
    part, _, _ = run(data_720p / "smoke_glare" / "degraded", flag, "--warmup", "0", output=False)
    key = {"--no-glare": "glare_ms", "--no-dehaze": "dehaze_ms", "--no-contrast": "contrast_ms"}[flag]
    assert all(f[key] == 0 for f in part["frames"])
    assert all(f[key] > 0 for f in full["frames"])

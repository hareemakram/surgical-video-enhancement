# SPDX-License-Identifier: MIT
"""Image quality verification against synthetic ground truth (see tools/synth.py)."""
import numpy as np
import pytest

from conftest import load_frames
import metrics as M

SMOKE_SEQS = ["smoke_light", "smoke_heavy", "smoke_glare"]


def _pairs(data, seq, out_dir):
    clean = load_frames(data / seq / "clean")
    deg = load_frames(data / seq / "degraded")
    out = load_frames(out_dir)
    assert len(clean) == len(deg) == len(out)
    return clean, deg, out


@pytest.mark.req("REQ-FUN-001")
@pytest.mark.parametrize("seq", SMOKE_SEQS)
def test_dehaze_restores_colour_and_structure(run, data_720p, seq, record_property):
    """Stage-level: glare + dehaze (contrast off, so the output is comparable to ground truth)."""
    _, out_dir, _ = run(data_720p / seq / "degraded", "--no-contrast", "--warmup", "0")
    clean, deg, out = _pairs(data_720p, seq, out_dir)
    de_in = np.mean([M.delta_e(c, d, M.fov_mask(c)) for c, d in zip(clean, deg)])
    de_out = np.mean([M.delta_e(c, o, M.fov_mask(c)) for c, o in zip(clean, out)])
    ss_in = np.mean([M.ssim(c, d) for c, d in zip(clean, deg)])
    ss_out = np.mean([M.ssim(c, o) for c, o in zip(clean, out)])
    ps_in = np.mean([M.psnr(c, d) for c, d in zip(clean, deg)])
    ps_out = np.mean([M.psnr(c, o) for c, o in zip(clean, out)])
    for k, v in dict(de_in=de_in, de_out=de_out, ssim_in=ss_in, ssim_out=ss_out, psnr_in=ps_in, psnr_out=ps_out).items():
        record_property(k, round(float(v), 3))
    assert de_out <= 0.5 * de_in, "colour error vs ground truth must at least halve"
    assert ps_out >= ps_in + 3.0, "PSNR must improve by >= 3 dB"
    assert ss_out >= ss_in, "structural similarity must not degrade"


@pytest.mark.req("REQ-FUN-002")
def test_glare_highlights_removed(run, data_720p, record_property):
    _, out_dir, _ = run(data_720p / "glare" / "degraded", "--no-contrast", "--no-dehaze", "--warmup", "0")
    clean, deg, out = _pairs(data_720p, "glare", out_dir)
    masks = [m > 0 for m in load_frames(data_720p / "glare" / "glare_mask")]
    spec_in = np.mean([M.specular_fraction(d, m) for d, m in zip(deg, masks)])
    spec_out = np.mean([M.specular_fraction(o, m) for o, m in zip(out, masks)])
    ps_in = np.mean([M.psnr(c, d, m) for c, d, m in zip(clean, deg, masks)])
    ps_out = np.mean([M.psnr(c, o, m) for c, o, m in zip(clean, out, masks)])
    record_property("specular_fraction_in", round(float(spec_in), 4))
    record_property("specular_fraction_out", round(float(spec_out), 4))
    record_property("psnr_in_mask_in", round(float(ps_in), 2))
    record_property("psnr_in_mask_out", round(float(ps_out), 2))
    assert spec_in > 0.3, "test data must contain glare"
    assert spec_out <= 0.1 * spec_in, "at least 90 % of glare pixels must be suppressed"
    assert ps_out >= ps_in + 6.0, "highlight regions must move toward the true tissue colour"


@pytest.mark.req("REQ-FUN-002")
def test_glare_detector_false_positive_rate(run, data_720p, record_property):
    """Bright, smoky but glare-free tissue must not be mistaken for highlights."""
    for seq in ("clean", "smoke_light", "smoke_heavy"):
        rep, _, _ = run(data_720p / seq / "degraded", "--warmup", "0", output=False)
        fp = max(f["glare_fraction"] for f in rep["frames"])
        record_property(f"max_glare_fraction_{seq}", round(fp, 5))
        assert fp <= 0.005, f"{seq}: {fp:.2%} of FOV flagged as glare"


@pytest.mark.req("REQ-FUN-003")
@pytest.mark.parametrize("seq", ["smoke_light", "smoke_heavy"])
def test_full_pipeline_increases_visible_detail(run, data_720p, seq, record_property):
    _, out_dir, _ = run(data_720p / seq / "degraded", "--warmup", "0")
    _, deg, out = _pairs(data_720p, seq, out_dir)
    ge_in = np.mean([M.gradient_energy(d, M.fov_mask(d)) for d in deg])
    ge_out = np.mean([M.gradient_energy(o, M.fov_mask(d)) for d, o in zip(deg, out)])
    rc_in = np.mean([M.rms_contrast(d, M.fov_mask(d)) for d in deg])
    rc_out = np.mean([M.rms_contrast(o, M.fov_mask(d)) for d, o in zip(deg, out)])
    record_property("gradient_gain", round(float(ge_out / ge_in), 3))
    record_property("rms_contrast_gain", round(float(rc_out / rc_in), 3))
    assert ge_out >= 1.3 * ge_in
    assert rc_out >= 1.15 * rc_in


@pytest.mark.req("REQ-FUN-004")
def test_clear_frames_not_altered_by_dehaze(run, data_720p, record_property):
    rep, out_dir, _ = run(data_720p / "clean" / "degraded", "--no-contrast", "--warmup", "0")
    clean, deg, out = _pairs(data_720p, "clean", out_dir)
    de = np.mean([M.delta_e(d, o, M.fov_mask(d)) for d, o in zip(deg, out)])
    haze = max(f["haze_level"] for f in rep["frames"])
    record_property("delta_e_vs_input", round(float(de), 3))
    record_property("max_haze_level", round(haze, 4))
    assert de <= 2.0, "clear footage must stay within ~2 JND of the input"
    assert haze <= 0.05

# SPDX-License-Identifier: MIT
"""Latency verification. Timing is measured inside sve_cli around Pipeline::process() only
(decode/encode excluded), after warm-up frames, with OpenCV's default thread pool."""
import pytest

BUDGET_MS = 30.0


def _check(rep, record_property, label):
    s = rep["summary"]
    for k in ("mean_ms", "p95_ms", "p99_ms", "max_ms"):
        record_property(f"{label}_{k}", round(s[k], 2))
    assert s["frames"] >= 50, "not enough timing samples"
    assert not any(f["bypassed"] for f in rep["frames"])
    return s


@pytest.mark.req("REQ-PRF-001")
def test_latency_720p_within_budget(run, data_720p, record_property):
    rep, _, _ = run(data_720p / "smoke_glare" / "degraded", "--repeat", "5", "--warmup", "5", output=False)
    s = _check(rep, record_property, "720p")
    assert s["p95_ms"] <= BUDGET_MS, f"p95 {s['p95_ms']:.1f} ms exceeds {BUDGET_MS} ms"
    assert s["mean_ms"] <= BUDGET_MS


@pytest.mark.req("REQ-PRF-002")
def test_latency_1080p_within_budget(run, data_1080p, record_property):
    rep, _, _ = run(data_1080p / "smoke_glare" / "degraded", "--repeat", "8", "--warmup", "5", output=False)
    s = _check(rep, record_property, "1080p")
    assert s["p95_ms"] <= BUDGET_MS, f"p95 {s['p95_ms']:.1f} ms exceeds {BUDGET_MS} ms"


@pytest.mark.req("REQ-PRF-001")
def test_latency_independent_of_content(run, data_720p, record_property):
    """Worst-case content (heavy smoke + glare) must not be much slower than a clear frame:
    the algorithms have data-independent run time, so the budget holds for any scene."""
    clear, _, _ = run(data_720p / "clean" / "degraded", "--repeat", "3", "--warmup", "5", output=False)
    worst, _, _ = run(data_720p / "smoke_glare" / "degraded", "--repeat", "3", "--warmup", "5", output=False)
    ratio = worst["summary"]["p50_ms"] / clear["summary"]["p50_ms"]
    record_property("worst_to_clear_ratio", round(ratio, 2))
    assert ratio < 2.5

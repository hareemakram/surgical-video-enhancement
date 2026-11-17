# Software Verification Plan

| | |
|---|---|
| Document | SVP-SVE-001 |
| Verifies | [SRS-SVE-001](requirements.md) |
| Results | [traceability.md](traceability.md) (latest local run), CI artifacts (each commit) |

## 1. Strategy

Every requirement is verified by at least one automated test in `tests/`. Each test is tagged with
the requirement IDs it covers (`@pytest.mark.req("REQ-…")`) and records its measured values with
`record_property`. `tools/trace_report.py` joins the requirement table with the JUnit results into
the traceability matrix. A requirement is **PASS** only if it has at least one test and all of its
tests pass. CI fails if any requirement is FAIL or NOT VERIFIED.

The tests treat the shipped `sve_cli` binary as a black box. They do not import C++ internals, so
what is tested is what is shipped.

## 2. Test data: synthetic ground truth

Real surgical video cannot be filmed with and without smoke, so it has no reference image to
measure against. `tools/synth.py` therefore renders laparoscopy-like scenes (tissue texture,
vessels, fat, scope vignetting, circular FOV, camera motion, sensor noise) and applies the
degradations with their physical models:

* smoke: `I = J·t + A·(1−t)` with a drifting, spatially varying density field;
* glare: additive saturated specular spots that move with the tissue.

Each degraded frame has its clean frame and a highlight mask, so full-reference metrics (ΔE00,
PSNR, SSIM) can be computed. Sequences: `clean`, `smoke_light`, `smoke_heavy`, `glare`,
`smoke_glare`, `static_smoke` (no motion, for flicker), 1280×720 and 1920×1080. Generation is
seeded and deterministic.

Real, openly licensed laparoscopy footage (see `ATTRIBUTION.md`) is used for the demo and for
visual review. It is not used for pass/fail criteria.

## 3. Metrics

| Metric | Use |
|---|---|
| CIEDE2000 (ΔE00) | Colour fidelity against ground truth or input |
| PSNR, SSIM | Pixel and structural fidelity against ground truth |
| Gradient energy, RMS contrast | Visible detail (no-reference) |
| CIELAB hue-angle difference | Hue preservation |
| Specular fraction | Share of near-white saturated pixels in highlight regions |
| Latency mean/p95/p99/max | Measured inside `sve_cli` around `Pipeline::process` only |

## 4. Environment

* CI: GitHub Actions `ubuntu-24.04` (4 vCPU), OpenCV from the Ubuntu archive, Python 3.12.
* Build: CMake, Release, `-Wall -Wextra -Wpedantic -Werror` on the library.
* Latency tests use warm-up frames and at least 50 samples. Shared CI runners add timing noise,
  which is one reason the budget is checked on the 95th percentile rather than the maximum.

## 5. Outputs (CI artifacts)

| File | Content |
|---|---|
| `reports/junit.xml` | Machine-readable results with requirement IDs and measurements |
| `reports/test-report.html` | Human-readable pytest report |
| `reports/traceability.md` | Requirement → test → verdict → evidence (also posted to the job summary) |
| `reports/benchmark.json` | Per-frame latency report from `sve_cli` |

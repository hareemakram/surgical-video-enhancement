# Software Requirements Specification

| | |
|---|---|
| Document | SRS-SVE-001 |
| Product | Surgical Video Enhancement (SVE) pipeline, v1.0 |
| Status | Released for portfolio demonstration |
| Related | [design.md](design.md) · [verification-plan.md](verification-plan.md) · [traceability.md](traceability.md) |

> **Intended use and limits.** SVE is an engineering demonstration of how a real-time image
> enhancement component for laparoscopic/endoscopic video can be specified, built and verified.
> It is **not a medical device**, has not been clinically validated, and must not be used for
> diagnosis or to guide surgery. The structure of this document follows the spirit of
> IEC 62304 (software requirements, traceability, SOUP), in a deliberately lightweight form.

## 1. Context

During minimally invasive surgery the surgeon sees the patient only through the endoscope video.
Two common problems reduce visibility:

* **Surgical smoke.** Electrocautery and ultrasonic devices produce plume that scatters the scope
  light, lowering contrast and washing colour toward grey-white.
* **Specular glare.** Wet tissue reflects the co-axial scope light as small saturated white spots,
  which hide tissue and distract the eye.

SVE is a frame-by-frame filter that sits between the camera and the display and reduces both
problems within a fixed latency budget, without changing the clinically meaningful colour of tissue.

## 2. Definitions

| Term | Meaning |
|---|---|
| FOV | Field of view: the (usually circular) lit image of the scope inside the black frame. |
| Frame latency | Wall time of `Pipeline::process()` for one frame, excluding decode/encode and display. |
| Reference platform | 4 vCPU x86-64 Linux, OpenCV 4.x, as provided by GitHub-hosted `ubuntu-24.04` runners. |
| Ground truth | For synthetic footage, the same frame rendered without smoke and glare (see `tools/synth.py`). |
| ΔE00 | CIEDE2000 colour difference. About 1 is just noticeable; above 10 is obvious. |

## 3. Requirements

Verification method for all requirements is **Test** (automated, `tests/`), run on every push by CI.
Numeric thresholds are evaluated on the synthetic ground-truth sequences unless stated otherwise.

### 3.1 Functional

| ID | Requirement | Acceptance criterion | Rationale |
|---|---|---|---|
| REQ-FUN-001 | The system shall reduce surgical smoke haze. | On smoke sequences (light, heavy, smoke + glare), with contrast stage off: mean ΔE00 to ground truth reduced by ≥ 50 %, PSNR improved by ≥ 3 dB, SSIM not reduced. | Primary purpose. Measured against ground truth so "looks sharper" cannot hide a wrong result. |
| REQ-FUN-002 | The system shall suppress specular glare highlights. | ≥ 90 % of ground-truth highlight pixels no longer specular; PSNR in highlight regions improved by ≥ 6 dB; ≤ 0.5 % of the FOV flagged as glare on glare-free footage. | Glare hides tissue. False positives would erase real bright structures. |
| REQ-FUN-003 | The system shall increase local contrast of hazy footage. | Full pipeline on smoke sequences: mean gradient magnitude +30 %, RMS contrast +15 %. | Restores visible tissue texture and vessel edges. |
| REQ-FUN-004 | The system shall leave clear (smoke-free) footage substantially unchanged by smoke removal. | Clear sequence, contrast stage off: mean ΔE00 to input ≤ 2.0; estimated haze level ≤ 0.05. | Enhancement must not alter a good image. |
| REQ-FUN-005 | Each processing stage shall be individually disableable. | Each stage reports zero time when disabled; with all stages disabled the output is bit-identical to the input. | Lets the user (or a supervisor component) fall back to the raw image. |

### 3.2 Performance

| ID | Requirement | Acceptance criterion | Rationale |
|---|---|---|---|
| REQ-PRF-001 | Frame latency at 1280×720 shall stay within 30 ms. | On the reference platform: mean and 95th percentile ≤ 30 ms over ≥ 50 frames after warm-up; worst-case content no more than 2.5× slower than clear content. | 30 ms keeps up with 30 fps video and keeps added glass-to-glass delay small for hand-eye coordination. |
| REQ-PRF-002 | Frame latency at 1920×1080 shall stay within 30 ms. | On the reference platform: 95th percentile ≤ 30 ms over ≥ 50 frames after warm-up. | Full-HD is the common resolution of current laparoscopy towers. |
| REQ-PRF-003 | The output shall be temporally stable (no flicker). | Static scene: mean frame-to-frame luminance change ≤ max(1.0, 2× input). Moving scene: std of frame mean luminance ≤ 2× input + 1. | Frame-wise estimates (airlight, CLAHE) can pump brightness, which is tiring and distracting. |

### 3.3 Safety-related

| ID | Requirement | Acceptance criterion | Rationale |
|---|---|---|---|
| REQ-SAF-001 | On invalid or unsupported input, or an internal error, the system shall pass the frame through unchanged and flag it, and shall never drop or blank a frame. | Black, white, tiny (8×8), 16-bit and 4-channel frames: one output frame per input, same size and type; unsupported inputs bit-identical and flagged `bypassed`; valid degenerate frames (all black/white) unchanged within ±1. | Losing the image during surgery is the most severe failure mode. |
| REQ-SAF-002 | Pixels outside the scope FOV shall not be modified. | Black border pixels (≥ 3 px from any lit pixel) bit-identical between input and output. | Prevents noise amplification and false structures in the border; keeps on-screen overlays intact. |
| REQ-SAF-003 | The system shall preserve tissue hue. | Full pipeline: mean CIELAB hue-angle difference to ground truth ≤ 5° on clear, glare and heavy smoke sequences. | Tissue colour carries clinical information (perfusion, ischaemia, bile). |
| REQ-SAF-004 | Processing shall be deterministic. | Two runs on the same input give bit-identical output. | Reproducibility of verification results and field issues. |

### 3.4 Interfaces

| ID | Requirement | Acceptance criterion | Rationale |
|---|---|---|---|
| REQ-IF-001 | The system shall read and write image sequences and video files, preserving frame count, size and type. | PNG directory and AVI/MJPG round trip: same frame count and dimensions. | Integration with recorders and test tooling. |
| REQ-IF-002 | The system shall accept 8-bit single-channel (monochrome) frames. | Grey input produces grey output of the same size, not bypassed. | Some scopes and NIR modes deliver monochrome video. |
| REQ-IF-003 | The CLI shall emit a machine-readable per-frame report and reject invalid invocations. | JSON report contains summary latency statistics and per-frame stage timings, glare fraction, haze level and bypass status; unknown argument exits 2, unreadable input exits 1. | Verification evidence and field diagnostics. |

## 4. Risk notes (informative)

A short hazard view in the style of ISO 14971, linking each hazard to the requirements that
control it. Severity and probability scoring is out of scope for this demonstration.

| Hazard | Possible cause | Control (requirement) |
|---|---|---|
| Loss of image during surgery | Exception, invalid frame, unsupported format | Fail-safe pass-through (REQ-SAF-001), stage bypass (REQ-FUN-005) |
| Delayed image impairs hand-eye coordination | Slow processing on large frames | Latency budget (REQ-PRF-001, REQ-PRF-002), content-independent run time |
| Misleading tissue colour | Over-aggressive dehazing, colour cast | Hue preservation (REQ-SAF-003), clear-frame passthrough (REQ-FUN-004) |
| Real structure erased as "glare" | False-positive highlight detection | False-positive limit (REQ-FUN-002), over-exposure guard in design |
| Distracting flicker | Per-frame parameter estimates | Temporal stability (REQ-PRF-003) |
| Artefacts in black border / overlays altered | Processing outside FOV | REQ-SAF-002 |

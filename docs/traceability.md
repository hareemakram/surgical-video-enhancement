# Traceability matrix and verification results

This file is a snapshot of a local run, committed for reference. CI regenerates it for every commit (job summary and `verification-report` artifact).

Generated 2026-11-17 13:29 UTC from `junit.xml`.

**15 of 15 requirements verified PASS**, 0 FAIL, 0 NOT VERIFIED. 32 of 32 tests passed.

| Requirement | Summary | Tests | Verdict | Evidence (measured) |
|---|---|---|---|---|
| REQ-FUN-001 | The system shall reduce surgical smoke haze. | `test_image_quality::test_dehaze_restores_colour_and_structure[smoke_light]` PASS<br>`test_image_quality::test_dehaze_restores_colour_and_structure[smoke_heavy]` PASS<br>`test_image_quality::test_dehaze_restores_colour_and_structure[smoke_glare]` PASS | **PASS** | de_in=11.815<br>de_out=3.668<br>ssim_in=0.945<br>ssim_out=0.967<br>psnr_in=20.692<br>psnr_out=28.71<br>de_in=22.095<br>de_out=5.769 … |
| REQ-FUN-002 | The system shall suppress specular glare highlights. | `test_image_quality::test_glare_highlights_removed` PASS<br>`test_image_quality::test_glare_detector_false_positive_rate` PASS | **PASS** | specular_fraction_in=0.7916<br>specular_fraction_out=0.0<br>psnr_in_mask_in=5.71<br>psnr_in_mask_out=32.95<br>max_glare_fraction_clean=0<br>max_glare_fraction_smoke_light=0<br>max_glare_fraction_smoke_heavy=0.00171 |
| REQ-FUN-003 | The system shall increase local contrast of hazy footage. | `test_image_quality::test_full_pipeline_increases_visible_detail[smoke_light]` PASS<br>`test_image_quality::test_full_pipeline_increases_visible_detail[smoke_heavy]` PASS | **PASS** | gradient_gain=2.127<br>rms_contrast_gain=1.249<br>gradient_gain=2.961<br>rms_contrast_gain=1.524 |
| REQ-FUN-004 | The system shall leave clear (smoke-free) footage substantially unchanged by smoke removal. | `test_image_quality::test_clear_frames_not_altered_by_dehaze` PASS | **PASS** | delta_e_vs_input=0.952<br>max_haze_level=0.0229 |
| REQ-FUN-005 | Each processing stage shall be individually disableable. | `test_safety::test_all_stages_disabled_is_identity` PASS<br>`test_safety::test_each_stage_can_be_disabled[--no-glare]` PASS<br>`test_safety::test_each_stage_can_be_disabled[--no-dehaze]` PASS<br>`test_safety::test_each_stage_can_be_disabled[--no-contrast]` PASS | **PASS** |  |
| REQ-PRF-001 | Frame latency at 1280×720 shall stay within 30 ms. | `test_performance::test_latency_720p_within_budget` PASS<br>`test_performance::test_latency_independent_of_content` PASS | **PASS** | 720p_mean_ms=8.15<br>720p_p95_ms=10.21<br>720p_p99_ms=10.91<br>720p_max_ms=12.28<br>worst_to_clear_ratio=1.5 |
| REQ-PRF-002 | Frame latency at 1920×1080 shall stay within 30 ms. | `test_performance::test_latency_1080p_within_budget` PASS | **PASS** | 1080p_mean_ms=19.06<br>1080p_p95_ms=23.52<br>1080p_p99_ms=26.65<br>1080p_max_ms=27.71 |
| REQ-PRF-003 | The output shall be temporally stable (no flicker). | `test_temporal::test_no_flicker_on_static_scene` PASS<br>`test_temporal::test_no_flicker_on_moving_scene` PASS | **PASS** | frame_to_frame_luma_change_in=0.004<br>frame_to_frame_luma_change_out=0.039<br>luma_std_in=0.09<br>luma_std_out=0.172 |
| REQ-SAF-001 | On invalid or unsupported input, or an internal error, the system shall pass the frame through unchanged and flag it, and shall never drop or blank a frame. | `test_safety::test_fail_safe_passthrough[black]` PASS<br>`test_safety::test_fail_safe_passthrough[white]` PASS<br>`test_safety::test_fail_safe_passthrough[tiny]` PASS<br>`test_safety::test_fail_safe_passthrough[uint16]` PASS<br>`test_safety::test_fail_safe_passthrough[bgra]` PASS | **PASS** | bypassed=False<br>bypass_reason=<br>bypassed=False<br>bypass_reason=<br>bypassed=True<br>bypass_reason=frame too small<br>bypassed=True<br>bypass_reason=unsupported pixel format … |
| REQ-SAF-002 | Pixels outside the scope FOV shall not be modified. | `test_safety::test_outside_fov_is_bit_exact` PASS | **PASS** |  |
| REQ-SAF-003 | The system shall preserve tissue hue. | `test_safety::test_tissue_hue_preserved[clean]` PASS<br>`test_safety::test_tissue_hue_preserved[glare]` PASS<br>`test_safety::test_tissue_hue_preserved[smoke_heavy]` PASS | **PASS** | mean_hue_shift_deg=1.28<br>mean_hue_shift_deg=1.28<br>mean_hue_shift_deg=2.57 |
| REQ-SAF-004 | Processing shall be deterministic. | `test_safety::test_deterministic_output` PASS | **PASS** |  |
| REQ-IF-001 | The system shall read and write image sequences and video files, preserving frame count, size and type. | `test_interface::test_frame_count_and_size_preserved` PASS<br>`test_interface::test_video_file_roundtrip` PASS | **PASS** |  |
| REQ-IF-002 | The system shall accept 8-bit single-channel (monochrome) frames. | `test_interface::test_grayscale_input_supported` PASS | **PASS** |  |
| REQ-IF-003 | The CLI shall emit a machine-readable per-frame report and reject invalid invocations. | `test_interface::test_report_schema` PASS<br>`test_interface::test_invalid_arguments_rejected` PASS | **PASS** |  |

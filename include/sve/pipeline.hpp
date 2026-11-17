// SPDX-License-Identifier: MIT
// Real-time enhancement pipeline for endoscopic / laparoscopic video.
//
// The pipeline is a fixed sequence of stages, each of which can be disabled:
//
//   input (BGR8) -> FOV mask -> glare suppression -> smoke dehaze -> local contrast -> output (BGR8)
//
// Analysis (glare in-fill, transmission map) runs at reduced resolution; the result is applied to
// the full-resolution frame in one fused, multi-threaded per-pixel pass.
//
// Design notes and requirement IDs (REQ-xxx) are in docs/.
#pragma once

#include <opencv2/core.hpp>

#include <array>
#include <cstdint>
#include <string>

namespace sve {

/// Tunable parameters. Defaults are the verified configuration (see docs/design.md).
struct Config {
    // Stage enable flags (REQ-FUN-005: every stage can be bypassed).
    bool enable_glare = true;
    bool enable_dehaze = true;
    bool enable_contrast = true;

    /// Resolution (relative to input) at which glare in-fill and the smoke transmission map are
    /// estimated. Results are upsampled and applied at full resolution (REQ-PRF-001).
    double analysis_scale = 0.25;

    // Field-of-view detection (REQ-SAF-002).
    int fov_threshold = 12;  ///< Gray level below which a pixel counts as outside the scope image.

    // Glare / specular highlight suppression (REQ-FUN-002).
    float glare_value_min = 0.90f;       ///< Minimum brightness (max channel, 0..1) of a highlight.
    float glare_saturation_max = 0.30f;  ///< Maximum colour saturation of a highlight.
    int glare_dilate_px = 3;             ///< Grow the mask to cover the highlight halo.
    double glare_fill_sigma = 6.0;       ///< Gaussian sigma (px) used to fill from surrounding tissue.
    /// If more than this fraction of the FOV qualifies as "glare", the frame is over-exposed or
    /// shows a white object (gauze, instrument); real highlights are small, so the stage is skipped.
    double glare_max_fraction = 0.10;

    // Smoke / haze removal, dark channel prior (REQ-FUN-001).
    int dehaze_patch = 15;
    /// Tissue prior: haze-free tissue (red, little blue/green) has a normalized dark channel up to
    /// about this value. Only dark-channel energy above it is attributed to smoke, so clear
    /// frames pass through the dehaze stage unchanged (REQ-FUN-004).
    float dehaze_dark_floor = 0.25f;           ///< Dark-channel patch size at full resolution (px).
    float dehaze_omega = 0.85f;      ///< Fraction of haze removed (1 = all; <1 keeps depth cue).
    float dehaze_t_min = 0.25f;      ///< Lower bound on transmission, limits noise amplification.
    float airlight_ema = 0.15f;      ///< Temporal smoothing of airlight, 1 = no smoothing (REQ-PRF-003).
    int guided_radius = 12;          ///< Guided filter radius at full resolution (px).
    double guided_eps = 1e-3;        ///< Guided filter regularisation.

    // Local contrast: CLAHE on luminance, applied as a per-pixel gain so hue is kept (REQ-FUN-003).
    double clahe_clip = 1.3;
    int clahe_tiles = 8;
    float contrast_gain_min = 0.6f;  ///< Clamp on the per-pixel luminance gain.
    float contrast_gain_max = 2.0f;
};

/// Per-frame result metadata.
struct FrameStats {
    double total_ms = 0.0;
    double fov_ms = 0.0;       ///< Downscale to analysis resolution + FOV maps.
    double glare_ms = 0.0;
    double dehaze_ms = 0.0;
    double contrast_ms = 0.0;
    double apply_ms = 0.0;     ///< Fused full-resolution pass (FOV, glare blend, haze removal).
    double glare_fraction = 0.0;  ///< Fraction of FOV pixels classified as specular highlight.
    double haze_level = 0.0;      ///< Mean (1 - transmission) inside FOV, 0 = clear, 1 = opaque.
    bool bypassed = false;        ///< True if the frame was passed through unmodified (REQ-SAF-001).
    std::string bypass_reason;
};

class Pipeline {
public:
    explicit Pipeline(Config cfg = {});

    /// Enhance one frame. Accepts CV_8UC3 (BGR) or CV_8UC1. Output has the same size and type.
    /// Never throws: on any internal error, or unsupported input, the input frame is returned
    /// unchanged and stats.bypassed is set (fail-safe pass-through, REQ-SAF-001).
    cv::Mat process(const cv::Mat& frame, FrameStats* stats = nullptr);

    /// Reset temporal state (call between unrelated clips).
    void reset();

    const Config& config() const { return cfg_; }

private:
    cv::Mat process_impl(const cv::Mat& bgr, FrameStats& st);

    Config cfg_;
    bool have_airlight_ = false;
    std::array<float, 3> airlight_{1.f, 1.f, 1.f};
};

// ---- Individual stages (exposed for unit testing and documentation) ----

/// Field-of-view maps at analysis resolution. A full-resolution pixel is inside the FOV if its
/// analysis-resolution cell is `interior`, or if the cell is in `mask` and the pixel itself is lit
/// (grey level > lit_threshold). This keeps dark tissue inside the scope image while never letting
/// the mask grow into the black border (REQ-SAF-002).
struct FovMaps {
    cv::Mat mask;      ///< CV_8U, 255 = scope image (after closing)
    cv::Mat interior;  ///< CV_8U, 255 = safely inside the scope image
    int lit_threshold = 6;
};

/// Compute FOV maps from a downscaled grey image.
FovMaps fov_maps(const cv::Mat& gray_small, int threshold);

/// Full-resolution FOV mask (CV_8U, 255 = inside), using the same rule as the pipeline.
cv::Mat detect_fov(const cv::Mat& bgr8, int threshold, double analysis_scale = 0.25);

/// Classify specular highlights on the full-resolution 8-bit frame. Returns CV_8U mask (255 = highlight).
cv::Mat detect_glare(const cv::Mat& bgr8, const Config& cfg);

/// Result of glare in-filling: replacement colour and blend weight, at the resolution of the input.
struct GlareFill {
    cv::Mat fill;   ///< CV_32FC3, tissue colour estimated from the highlight's surroundings.
    cv::Mat alpha;  ///< CV_32F in [0,1], 1 = replace pixel by fill (feathered at the edge).
};

/// Normalized-convolution in-fill of highlight pixels from surrounding tissue.
/// `mask` is CV_8U (255 = highlight) at the same size as `bgr32f`; `px_scale` = image scale
/// relative to full resolution (used to convert pixel parameters).
GlareFill glare_fill(const cv::Mat& bgr32f, const cv::Mat& mask, const Config& cfg, double px_scale);

/// Edge-preserving guided filter (He et al., 2010). I and p are CV_32F single channel.
cv::Mat guided_filter(const cv::Mat& I, const cv::Mat& p, int r, double eps);

}  // namespace sve

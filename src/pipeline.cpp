// SPDX-License-Identifier: MIT
#include "sve/pipeline.hpp"

#include <opencv2/imgproc.hpp>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <exception>

namespace sve {
namespace {

using Clock = std::chrono::steady_clock;

double ms_since(Clock::time_point t0) {
    return std::chrono::duration<double, std::milli>(Clock::now() - t0).count();
}

int odd_at_least(double v, int lo) {
    int k = std::max(lo, static_cast<int>(std::lround(v)));
    return (k % 2 == 0) ? k + 1 : k;
}

cv::Mat min_channel(const cv::Mat& bgr32f) {
    std::vector<cv::Mat> ch;
    cv::split(bgr32f, ch);
    cv::Mat m;
    cv::min(ch[0], ch[1], m);
    cv::min(m, ch[2], m);
    return m;
}

/// Airlight = brightness of the haziest 0.1 % of pixels (largest dark-channel values) in the FOV.
std::array<float, 3> estimate_airlight(const cv::Mat& small, const cv::Mat& dark, const cv::Mat& fov) {
    cv::Mat dark8;
    dark.convertTo(dark8, CV_8U, 255.0);
    const int hist_size = 256;
    const float range[] = {0.f, 256.f};
    const float* ranges[] = {range};
    cv::Mat hist;
    cv::calcHist(&dark8, 1, nullptr, fov, hist, 1, &hist_size, ranges);
    const double n = cv::sum(hist)[0];
    const double target = std::max(1.0, n * 0.001);
    double acc = 0.0;
    int thr = 255;
    for (; thr > 0; --thr) {
        acc += hist.at<float>(thr);
        if (acc >= target) break;
    }
    cv::Mat sel = (dark8 >= thr) & fov;
    cv::Scalar m = cv::mean(small, sel);
    // Surgical smoke scatters the (white-balanced) scope light, so the airlight is close to
    // neutral grey. Forcing it neutral stops bright tissue in a clear frame being mistaken for
    // airlight, which would otherwise make red tissue look "hazy" and shift its colour.
    const float a = std::clamp(static_cast<float>(std::max({m[0], m[1], m[2]})), 0.5f, 1.0f);
    return {a, a, a};
}

/// Precomputed mapping from full-resolution coordinates to analysis-resolution coordinates, so
/// low-resolution maps can be sampled inside the fused pass instead of being upsampled into
/// full-size temporaries (saves several full-frame passes per frame, REQ-PRF-001/002).
struct Axis {
    std::vector<int> i0, i1, nn;
    std::vector<float> w;
    Axis(int full, int small) : i0(full), i1(full), nn(full), w(full) {
        const float r = static_cast<float>(small) / full;
        for (int x = 0; x < full; ++x) {
            float src = std::clamp((x + 0.5f) * r - 0.5f, 0.f, static_cast<float>(small - 1));
            i0[x] = static_cast<int>(src);
            i1[x] = std::min(i0[x] + 1, small - 1);
            w[x] = src - i0[x];
            nn[x] = std::min(static_cast<int>((x + 0.5f) * r), small - 1);
        }
    }
};

inline float bilerp(const float* r0, const float* r1, int x0, int x1, float wx, float wy) {
    const float a = r0[x0] + wx * (r0[x1] - r0[x0]);
    const float b = r1[x0] + wx * (r1[x1] - r1[x0]);
    return a + wy * (b - a);
}

}  // namespace

Pipeline::Pipeline(Config cfg) : cfg_(cfg) {}

void Pipeline::reset() {
    have_airlight_ = false;
    airlight_ = {1.f, 1.f, 1.f};
}

cv::Mat Pipeline::process(const cv::Mat& frame, FrameStats* stats) {
    FrameStats st;
    const auto t0 = Clock::now();
    cv::Mat out;
    try {
        if (frame.empty()) {
            st.bypassed = true;
            st.bypass_reason = "empty frame";
        } else if (frame.depth() != CV_8U || (frame.channels() != 1 && frame.channels() != 3)) {
            st.bypassed = true;
            st.bypass_reason = "unsupported pixel format";
        } else if (frame.cols < 16 || frame.rows < 16) {
            st.bypassed = true;
            st.bypass_reason = "frame too small";
        }
        if (st.bypassed) {
            out = frame.clone();
        } else {
            cv::Mat bgr;
            if (frame.channels() == 1)
                cv::cvtColor(frame, bgr, cv::COLOR_GRAY2BGR);
            else
                bgr = frame;
            out = process_impl(bgr, st);
            if (frame.channels() == 1) cv::cvtColor(out, out, cv::COLOR_BGR2GRAY);
        }
    } catch (const std::exception& e) {
        // Fail-safe: the surgeon must never lose the image because enhancement failed.
        st = FrameStats{};
        st.bypassed = true;
        st.bypass_reason = std::string("exception: ") + e.what();
        out = frame.clone();
    }
    st.total_ms = ms_since(t0);
    if (stats) *stats = st;
    return out;
}

cv::Mat Pipeline::process_impl(const cv::Mat& bgr8, FrameStats& st) {
    // ---- Analysis image and field of view ----------------------------------------------
    auto t = Clock::now();
    const double s = std::clamp(cfg_.analysis_scale, 0.1, 1.0);
    cv::Mat small8, gray_s, small;
    cv::resize(bgr8, small8, cv::Size(), s, s, cv::INTER_AREA);
    cv::cvtColor(small8, gray_s, cv::COLOR_BGR2GRAY);
    const FovMaps fov = fov_maps(gray_s, cfg_.fov_threshold);
    const cv::Mat& fov_s = fov.mask;
    const double fov_px = std::max(1.0, cv::countNonZero(fov_s) / (s * s));
    small8.convertTo(small, CV_32FC3, 1.0 / 255.0);
    st.fov_ms = ms_since(t);

    // Low-resolution maps produced by the analysis stages, sampled by the fused pass.
    cv::Mat gmask;      // full-res CV_8U detected highlights
    cv::Mat alpha_s;    // CV_32F glare blend weight (analysis res), empty = no glare
    cv::Mat fill_s[3];  // CV_32F glare replacement colour per channel, 0..255
    cv::Mat inv_t_s;    // CV_32F 1/transmission, empty = no dehaze
    std::array<float, 3> A{0.f, 0.f, 0.f};

    // ---- Stage 1: specular glare suppression --------------------------------------------
    if (cfg_.enable_glare) {
        t = Clock::now();
        gmask = detect_glare(bgr8, cfg_);
        const int n = cv::countNonZero(gmask);
        st.glare_fraction = n / fov_px;
        if (n > 0 && st.glare_fraction <= cfg_.glare_max_fraction) {
            cv::Mat gmask_s;
            cv::resize(gmask, gmask_s, small.size(), 0, 0, cv::INTER_AREA);  // any coverage > 0
            GlareFill g = glare_fill(small, gmask_s, cfg_, s);
            g.alpha.setTo(0.f, ~fov_s);
            // Clean the analysis image too, so highlights cannot bias the airlight estimate.
            cv::Mat inv_alpha = 1.0 - g.alpha;
            cv::blendLinear(g.fill, small, g.alpha, inv_alpha, small);
            alpha_s = g.alpha;
            cv::Mat f255 = g.fill * 255.0;
            cv::split(f255, fill_s);
        } else {
            gmask.release();
        }
        st.glare_ms = ms_since(t);
    }

    // ---- Stage 2: smoke removal (dark channel prior + guided filter) ---------------------
    if (cfg_.enable_dehaze) {
        t = Clock::now();
        const cv::Mat outside = ~fov_s;
        const int patch = odd_at_least(cfg_.dehaze_patch * s, 3);
        const cv::Mat kernel = cv::getStructuringElement(cv::MORPH_RECT, cv::Size(patch, patch));

        // Pixels outside the FOV are black; set them to 1 so they do not pull the dark channel
        // down along the scope border (which would leave an un-dehazed ring).
        cv::Mat minc = min_channel(small);
        minc.setTo(1.f, outside);
        cv::Mat dark;
        cv::erode(minc, dark, kernel);
        dark.setTo(0.f, outside);

        const std::array<float, 3> A_now = estimate_airlight(small, dark, fov_s);
        if (!have_airlight_) {
            airlight_ = A_now;
            have_airlight_ = true;
        } else {
            // Exponential moving average avoids frame-to-frame brightness flicker (REQ-PRF-003).
            for (int c = 0; c < 3; ++c) airlight_[c] += cfg_.airlight_ema * (A_now[c] - airlight_[c]);
        }
        A = airlight_;

        std::vector<cv::Mat> ch;
        cv::split(small, ch);
        cv::Mat minn = ch[0] / A[0];
        cv::min(minn, ch[1] / A[1], minn);
        cv::min(minn, ch[2] / A[2], minn);
        minn.setTo(1.f, outside);
        cv::Mat dark_n;
        cv::erode(minn, dark_n, kernel);
        // t = 1 - omega * max(0, dark - floor) / (1 - floor)   (dark channel prior + tissue floor)
        const float fl = std::clamp(cfg_.dehaze_dark_floor, 0.f, 0.9f);
        cv::Mat excess = cv::max(dark_n - fl, 0.0);
        cv::Mat trans = 1.0 - (cfg_.dehaze_omega / (1.f - fl)) * excess;

        const int r = std::max(2, static_cast<int>(std::lround(cfg_.guided_radius * s)));
        cv::Mat guide;
        cv::cvtColor(small, guide, cv::COLOR_BGR2GRAY);
        trans = guided_filter(guide, trans, r, cfg_.guided_eps);
        trans = cv::max(cv::min(trans, 1.0), cfg_.dehaze_t_min);
        st.haze_level = 1.0 - cv::mean(trans, fov_s)[0];
        inv_t_s = 1.0 / trans;
        st.dehaze_ms = ms_since(t);
    }

    // ---- Fused full-resolution pass: FOV test, glare blend, scattering-model inversion ----
    t = Clock::now();
    cv::Mat out8(bgr8.size(), CV_8UC3);
    cv::Mat luma(bgr8.size(), CV_8U);
    cv::Mat inside(bgr8.size(), CV_8U);
    const Axis ax(bgr8.cols, small.cols), ay(bgr8.rows, small.rows);
    const float A255[3] = {A[0] * 255.f, A[1] * 255.f, A[2] * 255.f};
    const bool do_glare = !alpha_s.empty(), do_haze = !inv_t_s.empty();
    const int lit = fov.lit_threshold;
    cv::parallel_for_(cv::Range(0, bgr8.rows), [&](const cv::Range& rr) {
        for (int y = rr.start; y < rr.end; ++y) {
            const int y0 = ay.i0[y], y1 = ay.i1[y], yn = ay.nn[y];
            const float wy = ay.w[y];
            const uchar* src = bgr8.ptr<uchar>(y);
            const uchar* fm = fov.mask.ptr<uchar>(yn);
            const uchar* fi = fov.interior.ptr<uchar>(yn);
            const uchar* gm = do_glare ? gmask.ptr<uchar>(y) : nullptr;
            uchar* dst = out8.ptr<uchar>(y);
            uchar* lu = luma.ptr<uchar>(y);
            uchar* in = inside.ptr<uchar>(y);
            for (int x = 0; x < bgr8.cols; ++x) {
                const int i = 3 * x;
                const int xn = ax.nn[x];
                const int grey = (29 * src[i] + 150 * src[i + 1] + 77 * src[i + 2]) >> 8;
                if (!(fi[xn] || (fm[xn] && grey > lit))) {
                    // Outside the field of view: copied bit-exact (REQ-SAF-002).
                    dst[i] = src[i]; dst[i + 1] = src[i + 1]; dst[i + 2] = src[i + 2];
                    lu[x] = 0;
                    in[x] = 0;
                    continue;
                }
                in[x] = 255;
                float c[3] = {static_cast<float>(src[i]), static_cast<float>(src[i + 1]),
                              static_cast<float>(src[i + 2])};
                const int x0 = ax.i0[x], x1 = ax.i1[x];
                const float wx = ax.w[x];
                if (do_glare) {
                    float a = gm[x] ? 1.f
                                    : bilerp(alpha_s.ptr<float>(y0), alpha_s.ptr<float>(y1), x0, x1, wx, wy);
                    if (a > 0.f) {
                        for (int k = 0; k < 3; ++k) {
                            const float f = bilerp(fill_s[k].ptr<float>(y0), fill_s[k].ptr<float>(y1), x0, x1, wx, wy);
                            c[k] += a * (f - c[k]);
                        }
                    }
                }
                if (do_haze) {
                    const float it = bilerp(inv_t_s.ptr<float>(y0), inv_t_s.ptr<float>(y1), x0, x1, wx, wy);
                    for (int k = 0; k < 3; ++k) c[k] = (c[k] - A255[k]) * it + A255[k];
                }
                for (int k = 0; k < 3; ++k) dst[i + k] = cv::saturate_cast<uchar>(c[k]);
                lu[x] = static_cast<uchar>((29 * dst[i] + 150 * dst[i + 1] + 77 * dst[i + 2]) >> 8);
            }
        }
    });
    st.apply_ms = ms_since(t);

    // ---- Stage 3: local contrast (CLAHE on luminance, applied as a hue-preserving gain) ---
    if (cfg_.enable_contrast) {
        t = Clock::now();
        cv::Mat luma2;
        auto clahe = cv::createCLAHE(cfg_.clahe_clip, cv::Size(cfg_.clahe_tiles, cfg_.clahe_tiles));
        clahe->apply(luma, luma2);
        const float gmin = cfg_.contrast_gain_min, gmax = cfg_.contrast_gain_max;
        cv::parallel_for_(cv::Range(0, bgr8.rows), [&](const cv::Range& rr) {
            for (int y = rr.start; y < rr.end; ++y) {
                const uchar* in = inside.ptr<uchar>(y);
                const uchar* l1 = luma.ptr<uchar>(y);
                const uchar* l2 = luma2.ptr<uchar>(y);
                uchar* d = out8.ptr<uchar>(y);
                for (int x = 0; x < bgr8.cols; ++x) {
                    if (!in[x]) continue;
                    uchar* p = d + 3 * x;
                    const int mx = std::max({p[0], p[1], p[2]});
                    float g = (l2[x] + 0.5f) / (l1[x] + 0.5f);
                    // Scaling all channels by one gain keeps chromaticity (hue) unchanged; capping
                    // it so no channel clips keeps that true for bright pixels (REQ-SAF-003).
                    g = std::clamp(g, gmin, gmax);
                    if (mx > 0) g = std::min(g, 255.f / mx);
                    for (int k = 0; k < 3; ++k) p[k] = cv::saturate_cast<uchar>(p[k] * g);
                }
            }
        });
        st.contrast_ms = ms_since(t);
    }
    return out8;
}

}  // namespace sve

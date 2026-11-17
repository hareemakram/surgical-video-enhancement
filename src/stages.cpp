// SPDX-License-Identifier: MIT
#include "sve/pipeline.hpp"

#include <opencv2/core/utility.hpp>
#include <opencv2/imgproc.hpp>

#include <algorithm>
#include <cmath>

namespace sve {

FovMaps fov_maps(const cv::Mat& gray_small, int threshold) {
    FovMaps m;
    m.lit_threshold = threshold / 2;
    cv::Mat bin;
    cv::threshold(gray_small, bin, threshold, 255, cv::THRESH_BINARY);
    // Pad with black so morphology treats the image edge as "outside" (the scope circle is often
    // cut by the top/bottom of a 16:9 frame); otherwise closing would leak along the edge.
    const int pad = 6;
    cv::copyMakeBorder(bin, bin, pad, pad, pad, pad, cv::BORDER_CONSTANT, cv::Scalar(0));
    cv::Mat near_lit;  // lit cells grown by 2
    cv::dilate(bin, near_lit, cv::Mat(), cv::Point(-1, -1), 2, cv::BORDER_CONSTANT, cv::Scalar(0));
    // Close holes caused by dark tissue (cavities, vessels, shadow) inside the scope image.
    const cv::Mat k = cv::getStructuringElement(cv::MORPH_ELLIPSE, cv::Size(9, 9));
    cv::Mat closed, interior;
    cv::morphologyEx(bin, closed, cv::MORPH_CLOSE, k, cv::Point(-1, -1), 1, cv::BORDER_CONSTANT, cv::Scalar(0));
    cv::erode(closed, interior, cv::Mat(), cv::Point(-1, -1), 2, cv::BORDER_CONSTANT, cv::Scalar(0));
    // Closing also fills the concave cusps where a scope circle meets the image edge; limiting
    // the interior to the neighbourhood of genuinely lit cells removes them again.
    interior &= near_lit;
    const cv::Rect roi(pad, pad, gray_small.cols, gray_small.rows);
    m.mask = closed(roi).clone();
    m.interior = interior(roi).clone();
    return m;
}

cv::Mat detect_fov(const cv::Mat& bgr8, int threshold, double analysis_scale) {
    cv::Mat small, gray_s, gray;
    cv::resize(bgr8, small, cv::Size(), analysis_scale, analysis_scale, cv::INTER_AREA);
    cv::cvtColor(small, gray_s, cv::COLOR_BGR2GRAY);
    const FovMaps m = fov_maps(gray_s, threshold);
    cv::cvtColor(bgr8, gray, cv::COLOR_BGR2GRAY);
    cv::Mat mask, interior;
    cv::resize(m.mask, mask, bgr8.size(), 0, 0, cv::INTER_NEAREST);
    cv::resize(m.interior, interior, bgr8.size(), 0, 0, cv::INTER_NEAREST);
    return interior | (mask & (gray > m.lit_threshold));
}

cv::Mat detect_glare(const cv::Mat& bgr8, const Config& cfg) {
    // Specular reflections are bright AND desaturated (white); bright tissue keeps its hue.
    // Pixels outside the FOV are black, so they can never qualify.
    cv::Mat mask(bgr8.size(), CV_8U);
    const int vmin = static_cast<int>(std::lround(cfg.glare_value_min * 255.f));
    const float smax = cfg.glare_saturation_max;
    cv::parallel_for_(cv::Range(0, bgr8.rows), [&](const cv::Range& rr) {
        for (int y = rr.start; y < rr.end; ++y) {
            const uchar* p = bgr8.ptr<uchar>(y);
            uchar* m = mask.ptr<uchar>(y);
            for (int x = 0; x < bgr8.cols; ++x, p += 3) {
                const int mx = std::max({p[0], p[1], p[2]});
                const int mn = std::min({p[0], p[1], p[2]});
                m[x] = (mx >= vmin && (mx - mn) <= smax * mx) ? 255 : 0;
            }
        }
    });
    return mask;
}

GlareFill glare_fill(const cv::Mat& img, const cv::Mat& mask, const Config& cfg, double px_scale) {
    GlareFill g;
    cv::Mat bin = mask > 0;
    if (cfg.glare_dilate_px > 0) {
        const int r = std::max(1, static_cast<int>(std::lround(cfg.glare_dilate_px * px_scale)));
        cv::dilate(bin, bin, cv::getStructuringElement(cv::MORPH_ELLIPSE, cv::Size(2 * r + 1, 2 * r + 1)));
    }
    cv::Mat inside;
    bin.convertTo(inside, CV_32F, 1.0 / 255.0);
    cv::Mat w = 1.0 - inside;  // weight of valid (non-highlight) pixels

    // Normalized convolution:  fill = G*(I * w) / G*(w).  Equivalent to in-painting from the
    // surrounding tissue, but O(N) with a run time independent of the mask (REQ-PRF-001).
    // The three weighted colour channels and the weight are blurred together as one 4-channel image.
    const double s = std::max(1.0, cfg.glare_fill_sigma * px_scale);
    const int ks = static_cast<int>(std::ceil(s * 3)) * 2 + 1;
    std::vector<cv::Mat> ch;
    cv::split(img, ch);
    for (auto& c : ch) c = c.mul(w);
    ch.push_back(w);
    cv::Mat nd;
    cv::merge(ch, nd);
    cv::GaussianBlur(nd, nd, cv::Size(ks, ks), s);
    cv::split(nd, ch);
    cv::Mat den = cv::max(ch[3], 1e-4);
    for (int c = 0; c < 3; ++c) cv::divide(ch[c], den, ch[c]);
    ch.pop_back();
    cv::merge(ch, g.fill);

    // Feathered blend weight, but always 1 inside the mask itself.
    cv::GaussianBlur(inside, g.alpha, cv::Size(5, 5), 1.2);
    g.alpha = cv::max(g.alpha, inside);
    // Where no valid tissue is within reach the estimate is meaningless: keep the original pixel.
    g.alpha.setTo(0.f, den < 0.02);
    return g;
}

cv::Mat guided_filter(const cv::Mat& I, const cv::Mat& p, int r, double eps) {
    const cv::Size k(2 * r + 1, 2 * r + 1);
    cv::Mat mean_I, mean_p, mean_Ip, mean_II;
    cv::boxFilter(I, mean_I, CV_32F, k);
    cv::boxFilter(p, mean_p, CV_32F, k);
    cv::boxFilter(I.mul(p), mean_Ip, CV_32F, k);
    cv::boxFilter(I.mul(I), mean_II, CV_32F, k);
    cv::Mat cov_Ip = mean_Ip - mean_I.mul(mean_p);
    cv::Mat var_I = mean_II - mean_I.mul(mean_I);
    cv::Mat a = cov_Ip / (var_I + eps);
    cv::Mat b = mean_p - a.mul(mean_I);
    cv::boxFilter(a, a, CV_32F, k);
    cv::boxFilter(b, b, CV_32F, k);
    return a.mul(I) + b;
}

}  // namespace sve

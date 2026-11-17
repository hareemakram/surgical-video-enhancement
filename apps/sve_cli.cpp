// SPDX-License-Identifier: MIT
// Command-line front end: enhance a video file, an image, or a directory of images, and write a
// JSON report with per-frame latency and stage statistics (consumed by the Python test suite).
#include "sve/pipeline.hpp"

#include <opencv2/core/utility.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>
#include <opencv2/videoio.hpp>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <sstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace {

struct Args {
    std::string input, output, report;
    bool side_by_side = false;
    int warmup = 3;
    int repeat = 1;
    int threads = -1;
    double scale = 1.0;
    sve::Config cfg;
};

void usage() {
    std::cerr <<
        "Usage: sve_cli --input <video|image|dir> [--output <video|image|dir>] [--report r.json]\n"
        "               [--no-glare] [--no-dehaze] [--no-contrast] [--side-by-side]\n"
        "               [--warmup N] [--repeat N] [--threads N] [--scale F]\n"
        "               [--omega F] [--dark-floor F] [--t-min F] [--clahe-clip F] [--analysis-scale F]\n";
}

bool parse(int argc, char** argv, Args& a) {
    for (int i = 1; i < argc; ++i) {
        std::string k = argv[i];
        auto next = [&]() -> std::string {
            if (i + 1 >= argc) throw std::runtime_error("missing value for " + k);
            return argv[++i];
        };
        if (k == "--input") a.input = next();
        else if (k == "--output") a.output = next();
        else if (k == "--report") a.report = next();
        else if (k == "--no-glare") a.cfg.enable_glare = false;
        else if (k == "--no-dehaze") a.cfg.enable_dehaze = false;
        else if (k == "--no-contrast") a.cfg.enable_contrast = false;
        else if (k == "--side-by-side") a.side_by_side = true;
        else if (k == "--warmup") a.warmup = std::stoi(next());
        else if (k == "--repeat") a.repeat = std::max(1, std::stoi(next()));
        else if (k == "--threads") a.threads = std::stoi(next());
        else if (k == "--scale") a.scale = std::stod(next());
        else if (k == "--omega") a.cfg.dehaze_omega = std::stof(next());
        else if (k == "--dark-floor") a.cfg.dehaze_dark_floor = std::stof(next());
        else if (k == "--t-min") a.cfg.dehaze_t_min = std::stof(next());
        else if (k == "--clahe-clip") a.cfg.clahe_clip = std::stod(next());
        else if (k == "--analysis-scale") a.cfg.analysis_scale = std::stod(next());
        else if (k == "-h" || k == "--help") return false;
        else throw std::runtime_error("unknown argument " + k);
    }
    return !a.input.empty();
}

bool is_image(const fs::path& p) {
    std::string e = p.extension().string();
    std::transform(e.begin(), e.end(), e.begin(), ::tolower);
    return e == ".png" || e == ".jpg" || e == ".jpeg" || e == ".bmp" || e == ".tif" || e == ".tiff";
}

/// Uniform frame source over a video, a single image, or a sorted directory of images.
class Source {
public:
    explicit Source(const std::string& path) {
        fs::path p(path);
        if (fs::is_directory(p)) {
            for (auto& e : fs::directory_iterator(p))
                if (e.is_regular_file() && is_image(e.path())) files_.push_back(e.path());
            std::sort(files_.begin(), files_.end());
        } else if (is_image(p)) {
            files_.push_back(p);
        } else {
            cap_.open(path);
            if (!cap_.isOpened()) throw std::runtime_error("cannot open input " + path);
            fps_ = cap_.get(cv::CAP_PROP_FPS);
        }
    }
    bool read(cv::Mat& f, std::string& name) {
        if (cap_.isOpened()) {
            char buf[32];
            std::snprintf(buf, sizeof(buf), "frame_%06zu", idx_++);
            name = buf;
            return cap_.read(f) && !f.empty();
        }
        if (idx_ >= files_.size()) return false;
        name = files_[idx_].filename().string();
        f = cv::imread(files_[idx_++].string(), cv::IMREAD_UNCHANGED);
        return true;  // empty images are passed on; the pipeline must bypass them safely
    }
    double fps() const { return fps_ > 0 ? fps_ : 25.0; }

private:
    cv::VideoCapture cap_;
    std::vector<fs::path> files_;
    size_t idx_ = 0;
    double fps_ = 0;
};

class Sink {
public:
    Sink(const std::string& path, double fps) : path_(path), fps_(fps) {
        if (path.empty()) return;
        fs::path p(path);
        dir_mode_ = !p.has_extension();
        if (dir_mode_) fs::create_directories(p);
        else if (p.has_parent_path()) fs::create_directories(p.parent_path());
        single_image_ = is_image(p);
    }
    void write(const cv::Mat& f, const std::string& name) {
        if (path_.empty() || f.empty()) return;
        if (dir_mode_) {
            fs::path n(name);
            if (!is_image(n)) n += ".png";
            cv::imwrite((fs::path(path_) / n).string(), f);
        } else if (single_image_) {
            cv::imwrite(path_, f);
        } else {
            if (!vw_.isOpened()) {
                const std::string ext = fs::path(path_).extension().string();
                const int fourcc = (ext == ".avi") ? cv::VideoWriter::fourcc('M', 'J', 'P', 'G')
                                                   : cv::VideoWriter::fourcc('m', 'p', '4', 'v');
                vw_.open(path_, fourcc, fps_, f.size(), f.channels() == 3);
                if (!vw_.isOpened()) throw std::runtime_error("cannot open output " + path_);
            }
            vw_.write(f);
        }
    }

private:
    std::string path_;
    double fps_;
    bool dir_mode_ = false, single_image_ = false;
    cv::VideoWriter vw_;
};

double percentile(std::vector<double> v, double q) {
    if (v.empty()) return 0.0;
    std::sort(v.begin(), v.end());
    const double pos = q * (v.size() - 1);
    const size_t lo = static_cast<size_t>(pos);
    const size_t hi = std::min(lo + 1, v.size() - 1);
    return v[lo] + (pos - lo) * (v[hi] - v[lo]);
}

std::string json_escape(const std::string& s) {
    std::string o;
    for (char c : s) {
        if (c == '"' || c == '\\') o += '\\';
        o += c;
    }
    return o;
}

}  // namespace

int main(int argc, char** argv) {
    Args a;
    try {
        if (!parse(argc, argv, a)) {
            usage();
            return 2;
        }
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << "\n";
        usage();
        return 2;
    }
    if (a.threads >= 0) cv::setNumThreads(a.threads);

    try {
        Source src(a.input);
        Sink sink(a.output, src.fps());
        sve::Pipeline pipe(a.cfg);

        struct Rec {
            std::string name;
            int w, h;
            sve::FrameStats st;
        };
        std::vector<Rec> recs;
        cv::Mat frame;
        std::string name;
        int idx = 0;
        while (src.read(frame, name)) {
            if (a.scale != 1.0 && !frame.empty())
                cv::resize(frame, frame, cv::Size(), a.scale, a.scale, cv::INTER_AREA);
            cv::Mat out;
            sve::FrameStats st;
            // --repeat reprocesses the same frame to obtain more timing samples on short inputs;
            // only the last repetition's statistics are kept for each repetition record.
            for (int r = 0; r < a.repeat; ++r) {
                out = pipe.process(frame, &st);
                if (idx >= a.warmup) recs.push_back({name, frame.cols, frame.rows, st});
                ++idx;
            }
            if (a.side_by_side && !frame.empty() && !out.empty() && frame.type() == out.type()) {
                cv::Mat sbs;
                cv::hconcat(frame, out, sbs);
                sink.write(sbs, name);
            } else {
                sink.write(out, name);
            }
        }

        std::vector<double> tot;
        for (auto& r : recs)
            if (!r.st.bypassed) tot.push_back(r.st.total_ms);
        const double mean = tot.empty() ? 0 : std::accumulate(tot.begin(), tot.end(), 0.0) / tot.size();
        const double p50 = percentile(tot, 0.50), p95 = percentile(tot, 0.95), p99 = percentile(tot, 0.99);
        const double mx = tot.empty() ? 0 : *std::max_element(tot.begin(), tot.end());

        std::cerr << std::fixed << std::setprecision(2) << "frames=" << recs.size()
                  << " mean=" << mean << "ms p50=" << p50 << "ms p95=" << p95 << "ms max=" << mx
                  << "ms threads=" << cv::getNumThreads() << "\n";

        if (!a.report.empty()) {
            std::ofstream o(a.report);
            o << std::setprecision(6) << "{\n";
            o << "  \"input\": \"" << json_escape(a.input) << "\",\n";
            o << "  \"opencv_version\": \"" << CV_VERSION << "\",\n";
            o << "  \"threads\": " << cv::getNumThreads() << ",\n";
            o << "  \"warmup_frames_excluded\": " << a.warmup << ",\n";
            o << "  \"summary\": {\"frames\": " << tot.size() << ", \"mean_ms\": " << mean
              << ", \"p50_ms\": " << p50 << ", \"p95_ms\": " << p95 << ", \"p99_ms\": " << p99
              << ", \"max_ms\": " << mx << "},\n";
            o << "  \"frames\": [\n";
            for (size_t i = 0; i < recs.size(); ++i) {
                const auto& r = recs[i];
                o << "    {\"name\": \"" << json_escape(r.name) << "\", \"width\": " << r.w
                  << ", \"height\": " << r.h << ", \"total_ms\": " << r.st.total_ms
                  << ", \"fov_ms\": " << r.st.fov_ms << ", \"glare_ms\": " << r.st.glare_ms
                  << ", \"dehaze_ms\": " << r.st.dehaze_ms << ", \"contrast_ms\": " << r.st.contrast_ms << ", \"apply_ms\": " << r.st.apply_ms
                  << ", \"glare_fraction\": " << r.st.glare_fraction
                  << ", \"haze_level\": " << r.st.haze_level
                  << ", \"bypassed\": " << (r.st.bypassed ? "true" : "false")
                  << ", \"bypass_reason\": \"" << json_escape(r.st.bypass_reason) << "\"}"
                  << (i + 1 < recs.size() ? "," : "") << "\n";
            }
            o << "  ]\n}\n";
        }
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << "\n";
        return 1;
    }
    return 0;
}

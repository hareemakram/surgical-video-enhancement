# SPDX-License-Identifier: MIT
"""Image quality metrics used by the verification tests.

Full-reference metrics compare against the synthetic ground truth; no-reference metrics describe
properties of a single image. All functions take uint8 BGR images (OpenCV convention).
"""
from __future__ import annotations

import cv2
import numpy as np
from skimage.color import deltaE_ciede2000, rgb2lab
from skimage.metrics import peak_signal_noise_ratio, structural_similarity


def fov_mask(img: np.ndarray, thr: int = 30) -> np.ndarray:
    """Pixels clearly inside the scope image (eroded to stay away from the rim)."""
    m = (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) > thr).astype(np.uint8)
    return cv2.erode(m, np.ones((9, 9), np.uint8)).astype(bool)


def lab(img: np.ndarray) -> np.ndarray:
    return rgb2lab(img[..., ::-1].astype(np.float64) / 255.0)


def psnr(ref: np.ndarray, img: np.ndarray, mask: np.ndarray | None = None) -> float:
    if mask is None:
        return float(peak_signal_noise_ratio(ref, img, data_range=255))
    d = (ref.astype(np.float64) - img.astype(np.float64))[mask]
    mse = float(np.mean(d ** 2))
    return float("inf") if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


def ssim(ref: np.ndarray, img: np.ndarray) -> float:
    return float(structural_similarity(ref, img, channel_axis=2, data_range=255))


def delta_e(ref: np.ndarray, img: np.ndarray, mask: np.ndarray) -> float:
    """Mean CIEDE2000 colour difference inside `mask` (1 ~ just noticeable, >10 = obvious)."""
    return float(deltaE_ciede2000(lab(ref), lab(img))[mask].mean())


def rms_contrast(img: np.ndarray, mask: np.ndarray) -> float:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float64) / 255.0
    return float(g[mask].std())


def gradient_energy(img: np.ndarray, mask: np.ndarray) -> float:
    """Mean Sobel gradient magnitude: a no-reference proxy for visible detail."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1)
    return float(np.sqrt(gx ** 2 + gy ** 2)[mask].mean())


def hue_shift_deg(ref: np.ndarray, img: np.ndarray, mask: np.ndarray, min_chroma: float = 10.0) -> float:
    """Mean absolute CIELAB hue-angle difference over sufficiently coloured pixels, in degrees."""
    a, b = lab(ref), lab(img)
    ca = np.hypot(a[..., 1], a[..., 2])
    m = mask & (ca > min_chroma)
    ha = np.arctan2(a[..., 2], a[..., 1])
    hb = np.arctan2(b[..., 2], b[..., 1])
    d = np.angle(np.exp(1j * (hb - ha)))
    return float(np.degrees(np.abs(d[m])).mean())


def specular_fraction(img: np.ndarray, mask: np.ndarray) -> float:
    """Fraction of `mask` pixels that look like specular glare (near-white, bright)."""
    f = img.astype(np.int16)
    mx, mn = f.max(2), f.min(2)
    spec = (mx >= 235) & ((mx - mn) <= 0.3 * mx)
    return float(spec[mask].mean()) if mask.any() else 0.0

"""Mask operations — pure numpy/cv2, no torch, no ultralytics, no SAM package.

Tensor<->numpy conversions happen in ``builder.py`` so these functions are
trivially unit-testable and reusable.
"""

import cv2
import numpy as np

_MORPH = {
    "dilate": cv2.MORPH_DILATE,
    "erode": cv2.MORPH_ERODE,
    "open": cv2.MORPH_OPEN,
    "close": cv2.MORPH_CLOSE,
}


def bboxes_to_mask(bboxes, height: int, width: int) -> np.ndarray:
    """[H,W] float mask covering the given bounding boxes (union).

    Accepts a single dict or a list of dicts in ComfyUI ``BOUNDING_BOX`` format
    (``{"x", "y", "width", "height"}``, pixel coordinates), tolerating
    normalized (0..1) inputs when all values are <= 1.
    """
    if isinstance(bboxes, dict):
        bboxes = [bboxes]
    mask = np.zeros((height, width), dtype=np.float32)
    for box in bboxes or []:
        try:
            x, y = float(box["x"]), float(box["y"])
            w, h = float(box["width"]), float(box["height"])
        except (TypeError, KeyError, ValueError):
            continue
        if 0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1:
            x, y, w, h = x * width, y * height, w * width, h * height
        x1, y1 = int(max(0, x)), int(max(0, y))
        x2, y2 = int(min(width, x + w)), int(min(height, y + h))
        if x2 > x1 and y2 > y1:
            mask[y1:y2, x1:x2] = 1.0
    return mask


def face_region_mask(image_bgr: np.ndarray, faces, crop_factor: float, falloff: float) -> np.ndarray:
    """Soft elliptical masks over detected face bboxes (built-in fallback).

    ``faces``: engine Face objects with a ``bbox`` [x1, y1, x2, y2].
    """
    height, width = image_bgr.shape[:2]
    mask = np.zeros((height, width), dtype=np.float32)
    for face in faces:
        x1, y1, x2, y2 = [float(v) for v in face.bbox[:4]]
        bw, bh = x2 - x1, y2 - y1
        if bw <= 1 or bh <= 1:
            continue
        grow_w, grow_h = bw * (crop_factor - 1.0), bh * (crop_factor - 1.0)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        ax, ay = (bw + grow_w) / 2, (bh + grow_h) / 2
        cv2.ellipse(
            mask,
            (int(cx), int(cy)),
            (int(max(1, ax)), int(max(1, ay))),
            angle=0, startAngle=0, endAngle=360,
            color=1.0, thickness=-1,
        )
    if falloff > 0:
        k = int(falloff) * 2 + 1
        mask = cv2.GaussianBlur(mask, (k, k), 0)
    return np.clip(mask, 0.0, 1.0)


def grow_mask(mask: np.ndarray, amount: int) -> np.ndarray:
    if amount == 0:
        return mask
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (abs(amount) * 2 + 1, abs(amount) * 2 + 1))
    if amount > 0:
        return cv2.dilate(mask, kernel, iterations=1)
    return cv2.erode(mask, kernel, iterations=1)


def apply_morphology(mask: np.ndarray, operation: str, distance: int) -> np.ndarray:
    if operation == "none" or distance == 0:
        return mask
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (distance * 2 + 1, distance * 2 + 1))
    return cv2.morphologyEx(mask, _MORPH.get(operation, cv2.MORPH_OPEN), kernel, iterations=1)


def feather_mask(mask: np.ndarray, blur_radius: int, sigma_factor: float) -> np.ndarray:
    if blur_radius <= 0:
        return mask
    k = int(blur_radius) * 2 + 1
    return cv2.GaussianBlur(mask, (k, k), max(0.01, float(sigma_factor)))


def composite(base: np.ndarray, overlay: np.ndarray, mask: np.ndarray) -> np.ndarray:
    m = np.clip(mask, 0.0, 1.0)[..., None]
    return (overlay.astype(np.float32) * m + base.astype(np.float32) * (1.0 - m)).astype(np.uint8)

"""Face-restore upRes logic (pure, unit-testable).

The idea: face-restoration models have a native working resolution
(512 / 1024 / 2048, derived from the model name, or the exact fixed input
size read from an ONNX graph). The pipeline has two scaling points:

  S1: face crop -> native size   (align step; up- or down-scaled)
  S2: restored face -> back to the face's own resolution (paste-back)

With the upRes switch ON:
  * a model that can run at the face's actual size (dynamic input, e.g.
    GFPGAN .pth) and a face well within its native size is restored AT that
    size - no pixel upscaling/interpolation happens at all;
  * otherwise the crop is brought to the model's native size (interpolation
    or, with "Use Upscale model", a comfy upscale model), restored, and the
    result is brought back to the face's own resolution.
With the switch OFF the behavior is the classic one: always align at the
model's native size with the helper's default interpolation.
"""

import cv2

UPRES_USE_MODEL = "Use Upscale model"

INTERPOLATIONS = {
    "Nearest": cv2.INTER_NEAREST,
    "Bilinear": cv2.INTER_LINEAR,
    "Bicubic": cv2.INTER_CUBIC,
    "Lanczos": cv2.INTER_LANCZOS4,
    UPRES_USE_MODEL: None,  # handled by the comfy-native upscale-model path
}

UPRES_CHOICES = ["Lanczos", "Bicubic", "Bilinear", "Nearest", UPRES_USE_MODEL]


def interp_flag(name):
    """cv2 flag for an interpolation choice (Lanczos for anything unknown)."""
    return INTERPOLATIONS.get(name, cv2.INTER_LANCZOS4)


def native_size_from_name(model_name):
    """512 / 1024 / 2048 from the model's file name (GPEN-BFR-1024 etc.)."""
    size = 512
    if "1024" in model_name.lower():
        size = 1024
    elif "2048" in model_name.lower():
        size = 2048
    return size


def restore_model_native(model_name, ort_session=None):
    """(native_size, dynamic) for a face-restoration model.

    dynamic=True means the model accepts arbitrary (multiple-of-8) input
    sizes, so small faces can be restored without any pixel upscaling.
    ONNX graphs are introspected from the session's declared input shape;
    .pth archs are known: GFPGAN is fully convolutional (dynamic),
    CodeFormer / RestoreFormer / GPEN exports are fixed-size.
    """
    native = native_size_from_name(model_name)
    lower = model_name.lower()
    if ort_session is not None:
        try:
            shape = ort_session.get_inputs()[0].shape
            h, w = shape[2], shape[3]
            if isinstance(h, int) and isinstance(w, int) and h > 0 and h == w:
                return h, False
            return native, True
        except Exception:
            return native, False
    if ".onnx" in lower:
        # ONNX without a session to introspect: assume the name-derived size
        return native, False
    if "gfpgan" in lower:
        return native, True
    return native, False


def decide_restore_size(native, dynamic, face_px, upres_on):
    """Effective align/restore size for a face of ``face_px`` original pixels."""
    if not upres_on:
        return native
    if dynamic and face_px and face_px < native * 0.9:
        return int(max(64, min(native, round(face_px / 8.0) * 8)))
    return native


def needs_model_upscale(upres_mode, native, face_px):
    """S1: should the small crop be super-resolved by an upscale model?"""
    return (upres_mode == UPRES_USE_MODEL and face_px is not None
            and face_px < native * 0.9)

"""HDR Colour Bridge — the RenoDX-inspired tonal stage around the DLSS-NR DLL.

The DLSS-NR helpers (ours: neuroframe_engine; OreX's: dlss5nr_bridge) wrap
``nvngx_dlssnr.dll`` the way the ReShade addon ``renodx-dlss5.addon64``
(clshortfuse's RenoDX DLSS 5 work, MIT) does: the neural model's answer is
composed back into the frame through a paper-white-normalised colour bridge.
The "Classic" mode here reimplements that first-generation bridge math —
paper-white gain with a diffuse-white shoulder — as a pure-numpy post stage,
so the RenoDX-style controls are available in ComfyUI regardless of which
bridge DLL runs underneath:

- ``diffuse_white_nits``  — diffuse white of the display the grade targets
  (RenoDX default 237 nits; the reference point everything is normalised to),
- ``scene_paper_white_scale`` — the gain applied to scene linear before the
  shoulder (RenoDX "Scene Paper-White Scale", default 2.537),
- ``hdr_transfer_strength`` — how strongly the linear bridge transfer is
  applied (0 = pass-through, 1 = full classic transfer, >1 = extra gain),
- ``color_strength`` — chroma preservation around luma (1 = unchanged),
- ``black_lever`` (Anchored mode) — black-floor restore lever.

The "Anchored" mode implements the second-generation idea: instead of a
fixed white shoulder, the white point is anchored to the frame's own
measured highlight exposure (self-calibrating per frame — the single-image
equivalent of RenoDX's exposure-scan anchoring), and the black lever then
restores the floor the gain lifted.

All functions are pure numpy on float32 [0,1] RGB frames so they are
unit-testable without a GPU or the DLLs.
"""

import numpy as np

# RenoDX Classic reference values (defaults per the addon's UI)
DIFFUSE_WHITE_NITS_DEFAULT = 237.0
PAPER_WHITE_SCALE_DEFAULT = 2.537
SRGB_LINEAR_ALPHA = 0.2126  # Rec.709 luma weight (R channel), used via dot


def _srgb_to_linear(x):
    """Standard sRGB EOTF, piecewise."""
    a = 0.055
    return np.where(x <= 0.04045, x / 12.92, ((x + a) / (1 + a)) ** 2.4)


def _linear_to_srgb(x):
    """Standard sRGB inverse EOTF, piecewise, clamped to [0,1]."""
    a = 0.055
    x = np.clip(x, 0.0, None)
    out = np.where(x <= 0.0031308, x * 12.92, (1 + a) * np.power(np.maximum(x, 1e-12), 1 / 2.4) - a)
    return np.clip(out, 0.0, 1.0)


def _luma(rgb):
    """Rec.709 luma of a linear-light frame (..., 3)."""
    w = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    return rgb @ w


def _apply_color_strength(rgb, strength):
    """Scale chroma around luma (1.0 = untouched)."""
    if abs(strength - 1.0) < 1e-4:
        return rgb
    y = _luma(rgb)[..., None]
    return y + (rgb - y) * float(strength)


def classic_bridge(frame, diffuse_white_nits=DIFFUSE_WHITE_NITS_DEFAULT,
                   paper_white_scale=PAPER_WHITE_SCALE_DEFAULT,
                   transfer_strength=1.0, color_strength=1.0):
    """Classic (paper-white gain) HDR Colour Bridge.

    frame: float32 [0,1] RGB (display-referred sRGB-ish), HWC or CHW-flattened HWC.
    Returns the graded frame in the same layout/range.
    """
    frame = np.asarray(frame, dtype=np.float32)
    t = float(np.clip(transfer_strength, 0.0, 2.0))
    if t <= 1e-4:
        return frame

    lin = _srgb_to_linear(np.clip(frame, 0.0, 1.0))
    gain = float(paper_white_scale) * (1.0 + max(t - 1.0, 0.0))  # >1 strength adds gain
    x = lin * gain

    # extended-Reinhard shoulder kneeing at diffuse white (normalised to the
    # 237-nit default): x == w maps to 1.0 exactly, above rolls off softly.
    w = max(float(diffuse_white_nits) / DIFFUSE_WHITE_NITS_DEFAULT, 1e-4)
    mapped = x * (1.0 + x / (w * w)) / (1.0 + x)
    out = mapped / w

    out = _apply_color_strength(out, color_strength)
    graded = _linear_to_srgb(out)

    # transfer strength below 1 blends the grade back toward the input
    blend = min(t, 1.0)
    return np.clip(frame * (1.0 - blend) + graded * blend, 0.0, 1.0).astype(np.float32)


def anchored_bridge(frame, diffuse_white_nits=DIFFUSE_WHITE_NITS_DEFAULT,
                    paper_white_scale=PAPER_WHITE_SCALE_DEFAULT,
                    transfer_strength=1.0, color_strength=1.0,
                    black_lever=0.5, highlight_percentile=95.0,
                    highlight_bias=0.25):
    """Anchored mode: white point anchored to the frame's own highlights.

    Measures the frame's highlight exposure (the given luminance percentile in
    linear light) and knees at ``max(diffuse white, measured highlight)`` —
    the single-image analogue of RenoDX's anchored white point. The black
    lever then restores the floor (0 = shadows follow the gain untouched,
    1 = shadows pulled back toward the original).
    """
    frame = np.asarray(frame, dtype=np.float32)
    t = float(np.clip(transfer_strength, 0.0, 2.0))
    if t <= 1e-4:
        return frame

    lin = _srgb_to_linear(np.clip(frame, 0.0, 1.0))
    gain = float(paper_white_scale) * (1.0 + max(t - 1.0, 0.0))
    x = lin * gain

    luma = np.clip(_luma(x), 0.0, None)
    measured = float(np.percentile(luma, highlight_percentile))
    w = max(float(diffuse_white_nits) / DIFFUSE_WHITE_NITS_DEFAULT,
            measured * (1.0 + float(highlight_bias)), 1e-4)

    mapped = x * (1.0 + x / (w * w)) / (1.0 + x)
    out = mapped / w

    # black lever: restore the shadow floor the gain lifted
    lever = float(np.clip(black_lever, 0.0, 1.0))
    if lever > 1e-4:
        shadow = np.exp(-np.maximum(lin, 0.0) * 8.0)  # ~1 in shadows, ~0 in highlights
        out = out * (1.0 - lever * 0.5 * shadow) + lin * (lever * 0.5 * shadow)

    out = _apply_color_strength(out, color_strength)
    graded = _linear_to_srgb(out)
    blend = min(t, 1.0)
    return np.clip(frame * (1.0 - blend) + graded * blend, 0.0, 1.0).astype(np.float32)


def apply_bridge(frame, mode="classic", **kwargs):
    """Dispatch: mode 'classic' | 'anchored' (anything else = pass-through)."""
    if mode == "classic":
        keys = ("diffuse_white_nits", "paper_white_scale", "transfer_strength", "color_strength")
        return classic_bridge(frame, **{k: v for k, v in kwargs.items() if k in keys})
    if mode == "anchored":
        return anchored_bridge(frame, **kwargs)
    return frame

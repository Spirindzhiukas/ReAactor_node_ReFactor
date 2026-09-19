"""HDR Colour Bridge — the RenoDX-inspired tonal stage around the DLSS-NR DLL.

The DLSS-NR helpers (ours: neuroframe_engine; OreX's: dlss5nr_bridge) wrap
``nvngx_dlssnr.dll`` the way the ReShade addon ``renodx-dlss5.addon64``
(clshortfuse's RenoDX DLSS 5 work, MIT) does: the neural model's answer is
composed back into the frame through a paper-white-normalised colour bridge.
The "Classic" mode here reimplements that first-generation bridge math —
paper-white gain with a diffuse-white shoulder — as a pure post stage, so the
RenoDX-style controls are available in ComfyUI regardless of which bridge DLL
runs underneath:

- ``diffuse_white_nits``  — diffuse white of the display the grade targets
  (default 220 nits — owner-tuned for regular 8/16-bit images; the RenoDX
  addon's 237 nits assume a running game engine's higher output range),
- ``scene_paper_white_scale`` — the gain applied to scene linear before the
  shoulder (RenoDX "Scene Paper-White Scale"; default 1.0 for still images —
  the game-engine default 2.537 overblew non-HDR buffers),
- ``hdr_transfer_strength`` — how strongly the linear bridge transfer is
  applied (0 = pass-through, 1 = full classic transfer, >1 = extra gain),
- ``color_strength`` — chroma preservation around luma (1 = unchanged),
- ``black_lever`` (Anchored mode) — black-floor restore lever.

The "Anchored" mode implements the second-generation idea: instead of a
fixed white shoulder, the white point is anchored to the frame's own
measured highlight exposure (self-calibrating per frame — the single-image
equivalent of RenoDX's exposure-scan anchoring), and the black lever then
restores the floor the gain lifted.

Backends: every function accepts a numpy array **or** a torch tensor and
runs the identical math through thin op shims, so the bridge can run
GPU-resident (zero PCIe round-trips) when the DLSS5 node takes the CUDA
path. Numpy remains fully unit-testable without torch.
"""

import numpy as np

# Owner-tuned image defaults (RenoDX game-engine reference values were 237 /
# 2.537 - see module docstring)
DIFFUSE_WHITE_NITS_DEFAULT = 220.0
PAPER_WHITE_SCALE_DEFAULT = 1.0
SRGB_LINEAR_ALPHA = 0.2126  # Rec.709 luma weight (R channel), used via dot


def _is_torch(x):
    return type(x).__module__.startswith("torch")


# --- backend-agnostic op shims (numpy OR torch; identical semantics) -------

def _clip(x, lo, hi):
    return x.clamp(lo, hi) if _is_torch(x) else np.clip(x, lo, hi)


def _clip_min(x, lo):
    return x.clamp_min(lo) if _is_torch(x) else np.maximum(x, lo)


def _where(cond, a, b):
    return torch_where(cond, a, b) if _is_torch(cond) else np.where(cond, a, b)


def torch_where(cond, a, b):
    import torch
    return torch.where(cond, a, b)


def _pow(x, p):
    return x ** p


def _exp(x):
    return x.exp() if _is_torch(x) else np.exp(x)


def _luma_weight(rgb):
    """Rec.709 luma weights as a same-kind tensor for the given frame."""
    w = [0.2126, 0.7152, 0.0722]
    if _is_torch(rgb):
        import torch
        return torch.tensor(w, dtype=rgb.dtype, device=rgb.device)
    return np.array(w, dtype=np.float32)


def _luma(rgb):
    """Rec.709 luma of a linear-light frame (..., 3)."""
    return rgb @ _luma_weight(rgb)


def _percentile(x, q):
    """Index-based percentile (identical semantics in numpy and torch; avoids
    torch.quantile's element-count limit on huge frames)."""
    flat = x.reshape(-1)
    n = flat.numel() if _is_torch(x) else flat.size
    idx = min(n - 1, max(0, int(round(float(q) / 100.0 * (n - 1)))))
    if _is_torch(x):
        values, _ = flat.sort()
        return float(values[idx])
    return float(np.sort(flat)[idx])


def _finalize(frame, graded, blend):
    out = frame * (1.0 - blend) + graded * blend
    out = _clip(out, 0.0, 1.0)
    if _is_torch(out):
        import torch
        return out if out.dtype == torch.float32 else out.to(torch.float32)
    return out.astype(np.float32)


def _srgb_to_linear(x):
    """Standard sRGB EOTF, piecewise."""
    a = 0.055
    return _where(x <= 0.04045, x / 12.92, ((x + a) / (1 + a)) ** 2.4)


def _linear_to_srgb(x):
    """Standard sRGB inverse EOTF, piecewise, clamped to [0,1]."""
    a = 0.055
    x = _clip_min(x, 0.0)
    out = _where(x <= 0.0031308, x * 12.92, (1 + a) * _pow(_clip_min(x, 1e-12), 1 / 2.4) - a)
    return _clip(out, 0.0, 1.0)


def _apply_color_strength(rgb, strength):
    """Scale chroma around luma (1.0 = untouched)."""
    if abs(strength - 1.0) < 1e-4:
        return rgb
    y = _luma(rgb)[..., None]
    return y + (rgb - y) * float(strength)


def _prep(frame):
    if _is_torch(frame):
        import torch
        return frame if frame.dtype == torch.float32 else frame.to(torch.float32)
    return np.asarray(frame, dtype=np.float32)


def classic_bridge(frame, diffuse_white_nits=DIFFUSE_WHITE_NITS_DEFAULT,
                   paper_white_scale=PAPER_WHITE_SCALE_DEFAULT,
                   transfer_strength=1.0, color_strength=1.0):
    """Classic (paper-white gain) HDR Colour Bridge.

    frame: float32 [0,1] RGB (display-referred sRGB-ish), HWC — numpy or torch.
    Returns the graded frame in the same layout/range/backend.
    """
    frame = _prep(frame)
    t = float(_clip(np.float32(transfer_strength), 0.0, 2.0))
    if t <= 1e-4:
        return frame

    lin = _srgb_to_linear(_clip(frame, 0.0, 1.0))
    gain = float(paper_white_scale) * (1.0 + max(t - 1.0, 0.0))  # >1 strength adds gain
    x = lin * gain

    # extended-Reinhard shoulder kneeing at diffuse white (normalised to the
    # 220-nit default): x == w maps to ~1.0, above rolls off softly.
    w = max(float(diffuse_white_nits) / DIFFUSE_WHITE_NITS_DEFAULT, 1e-4)
    mapped = x * (1.0 + x / (w * w)) / (1.0 + x)
    out = mapped / w

    out = _apply_color_strength(out, color_strength)
    graded = _linear_to_srgb(out)

    # transfer strength below 1 blends the grade back toward the input
    blend = min(t, 1.0)
    return _finalize(frame, graded, blend)


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
    frame = _prep(frame)
    t = float(_clip(np.float32(transfer_strength), 0.0, 2.0))
    if t <= 1e-4:
        return frame

    lin = _srgb_to_linear(_clip(frame, 0.0, 1.0))
    gain = float(paper_white_scale) * (1.0 + max(t - 1.0, 0.0))
    x = lin * gain

    luma = _clip_min(_luma(x), 0.0)
    measured = _percentile(luma, highlight_percentile)
    w = max(float(diffuse_white_nits) / DIFFUSE_WHITE_NITS_DEFAULT,
            measured * (1.0 + float(highlight_bias)), 1e-4)

    mapped = x * (1.0 + x / (w * w)) / (1.0 + x)
    out = mapped / w

    # black lever: restore the shadow floor the gain lifted
    lever = float(_clip(np.float32(black_lever), 0.0, 1.0))
    if lever > 1e-4:
        shadow = _exp(_clip_min(lin, 0.0) * -8.0)  # ~1 in shadows, ~0 in highlights
        k = lever * 0.5 * shadow
        out = out * (1.0 - k) + lin * k

    out = _apply_color_strength(out, color_strength)
    graded = _linear_to_srgb(out)
    blend = min(t, 1.0)
    return _finalize(frame, graded, blend)


def apply_bridge(frame, mode="classic", **kwargs):
    """Dispatch: mode 'classic' | 'anchored' (anything else = pass-through)."""
    if mode == "classic":
        keys = ("diffuse_white_nits", "paper_white_scale", "transfer_strength", "color_strength")
        return classic_bridge(frame, **{k: v for k, v in kwargs.items() if k in keys})
    if mode == "anchored":
        return anchored_bridge(frame, **kwargs)
    return frame

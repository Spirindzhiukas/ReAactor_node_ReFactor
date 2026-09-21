"""DLSS5 Frame Enhancer node.

NVIDIA's DLSS-NR DLLs are 3rd-party, manually installed binaries (see
``discovery.py``). This node gained, per the project owner's request, a
``dll_version`` selector so several user-supplied DLL generations can live
side by side (``models/dlssnr/<version>/``) and be switched per workflow —
useful as new DLSS 5.x DLL releases appear.
"""

import os

import numpy as np
import torch

import comfy.model_management as model_management

from ..dlsssr import crashlog     # C++-throw serial + report (rig run 32)
from ..dlsssr import cuda_flags   # context flags around the SR pass
from ..log import dlss_logger as logger
from ..scripting import state
from ..utils import (
    progress_bar,
    progress_bar_reset,
)
from . import discovery
from . import schedule as nr_schedule_lib
from .core import DLSSStandaloneManager

ENGINE_NATIVE = "ANTs native NGX"

# SR model presets: the letters the installed nvngx_dlss.dll understands,
# with the artist-friendly community names (J/K/L/M transformer models).
SR_MODEL_CHOICES = ["Default", "J - Transformer I Crisp", "K - Transformer I Stable",
                    "L - Transformer II Quality", "M - Transformer II Fast"]
SR_MODEL_DEFAULT = "L - Transformer II Quality"  # newest + highest quality
_PRESET_TO_LETTER = {"Default": "Default", "J - Transformer I Crisp": "J",
                     "K - Transformer I Stable": "K",
                     "L - Transformer II Quality": "L", "M - Transformer II Fast": "M"}
# NR preset hint (DLSSNR.Hint.Render.Preset; best-effort - the stock NR
# runtime ignores unknown parameters, the RenoDX build may honor it).
_NR_PRESET_TO_INT = dict(_PRESET_TO_LETTER, **{})
_NR_PRESET_TO_INT = {"Default": 0, "J - Transformer I Crisp": 10,
                     "K - Transformer I Stable": 11,
                     "L - Transformer II Quality": 12, "M - Transformer II Fast": 13}
PRE_DENOISE_SR = "SR (DLSS denoise)"
PRE_DENOISE_MODEL = "Denoise Model"
# OFF is a real mode (owner request): until it existed, "Denoise Model" without
# a connected model was the only way to say "no pre-denoise", and a connected
# model + strength > 0 always ran. OFF disables the stage completely, whatever
# is connected, and the JS greys `pre_denoise_strength` out with it.
PRE_DENOISE_OFF = "OFF (no pre-denoise)"
ENGINE_LEGACY = "Legacy neuroframe DLLs"
from .hdr_bridge import (
    DIFFUSE_WHITE_NITS_DEFAULT,
    PAPER_WHITE_SCALE_DEFAULT,
    apply_bridge,
)


GPU_AUTO = "Auto (GPU when available)"
GPU_FORCE = "Force GPU (CUDA)"
GPU_OFF = "CPU (host staging)"


def blend_frames(base, processed, amount: float):
    """Lerp two same-layout frames: amount 0 -> base, 1 -> processed.

    Numpy or torch, agnostic (used for the pre-SR denoise strength).
    ALWAYS returns a C-contiguous frame: the engine entry points take raw
    pointers and assume interleaved [H,W,C] float memory. `processed`
    commonly arrives as a movedim VIEW with planar memory (from the tiled
    upscale mirror) - handing that view's pointer to the dll produced the
    rig's "9 gray tiles" output (planar RGB decoded as interleaved).
    """
    amount = float(amount)
    if hasattr(processed, "contiguous"):  # torch (may be a non-contig view)
        processed = processed.contiguous()
    else:  # numpy
        processed = np.ascontiguousarray(processed)
    if amount >= 1.0 - 1e-4:
        return processed
    if amount <= 1e-4:
        return base
    return base * (1.0 - amount) + processed * amount


def decide_cuda_acceleration(mode: str, torch_cuda_available: bool,
                             engine_cuda_ok: bool, engine_why: str = ""):
    """Pure decision: run the CUDA device-pointer path? (unit-testable)

    `engine_why` is the engine's own explanation (core.cuda_available()[1]) -
    the rig log used to print a bare "engine lacks CUDA interop (False)",
    which said nothing about WHICH engine build was loaded or why.
    """
    if mode == GPU_OFF:
        return False, "CPU mode selected (host staging)"
    if not torch_cuda_available:
        return False, "torch reports no CUDA device"
    if not engine_cuda_ok:
        return False, ("engine has no CUDA interop"
                       + (f": {engine_why}" if engine_why else ""))
    return True, "CUDA device-pointer path"


# --- the output smoke test + the soak instrument (2026-09-21) ---------------
# The native host runs end to end now (owner run 01:50: three prompts, styles
# 0/1/2, CreateFeature and EvaluateFeature both hr=0x00000001, visibly different
# output per mode). Two silent failures cost the nights before that, so the two
# things a clamp cannot hide are checked out loud on the first native frame:
# non-finite values (an fp16 overflow arrives as inf, a broken engine as NaN)
# and a payload that comes back byte-identical (the engine did nothing).
def rgba_bytes_from_rgb8(rgb8):
    """(H, W, 3) uint8 -> the (H, W, 4) RGBA8 payload the NR host uploads.

    Pure numpy on purpose: the smoke test builds a synthetic image and checks
    this path where no GPU exists, and the tensor wrapper around it stays a
    two-liner.
    """
    h, w = rgb8.shape[0], rgb8.shape[1]
    rgba = np.empty((h, w, 4), dtype=np.uint8)
    rgba[:, :, :3] = rgb8
    rgba[:, :, 3] = 255
    return rgba.tobytes()


# A few clipped texels are normal: the host maps the engine's RGBA16F readback
# into RGBA8 by clamping. A frame where a real share of the payload saturates
# means the engine left its [0, 1] contract, and that is worth a line.
NR_OUT_OF_RANGE_FRACTION = 0.01


def native_output_verdict(same_as_input, nonfinite, clipped=0, values=0):
    """(level, text) for the first native frame - the output smoke test.

    ``same_as_input``: the RGBA8 payload the engine returned is byte-identical
    to the one it was given. ``nonfinite``: non-finite values (NaN/Inf) in the
    RGBA16F readback, before the host clamps them into range. ``clipped`` /
    ``values``: how many of the scanned readback values sat outside [0, 1].
    The caller logs, never raises: intensity 0 is a legitimate no-op and the
    user still gets a frame either way, now with the reason attached.
    """
    if nonfinite:
        return ("error",
                f"[ANTs] the native NR output holds {int(nonfinite)} non-finite "
                "values (NaN/Inf) - that is an engine (or driver) fault, not a "
                "look. The frame is shown clamped; report this line.")
    if values and clipped > NR_OUT_OF_RANGE_FRACTION * values:
        return ("warning",
                f"[ANTs] {int(clipped)} of {int(values)} native NR readback "
                "values were outside [0, 1] and got clamped - the engine left "
                "its range. Report this line.")
    if same_as_input:
        return ("warning",
                "[ANTs] the native NR output is byte-identical to its input - "
                "the engine did nothing for this frame. Expected only when "
                "intensity is 0 or the style is a no-op; otherwise report this "
                "line.")
    return ("ok", "")


def session_cache_enabled():
    """``ANTS_NR_SESSION_CACHE=1`` - keep the NR session across prompts.

    ComfyUI builds a FRESH node instance per prompt, so the size-keyed cache in
    :meth:`_native_session_for` only spans the frames of one queue item: every
    prompt pays Init_ProjectID + provider load + CreateFeature again (owner run
    01:50: all of it inside a 1.6-1.9 s prompt). The key itself is unchanged
    (dll, size, preset), so a workflow change still rebuilds the session.
    OFF by default: a session that outlives a prompt is a lifecycle change, and
    the run that made this path work used per-prompt init - the owner can A/B
    it and watch the soak line.
    """
    return os.environ.get("ANTS_NR_SESSION_CACHE", "").strip().lower() in (
        "1", "true", "yes", "on")


def sr_output_verdict(payload_in, payload_out):
    """(level, text) for one SR pre-denoise pass - the SR output smoke test.

    Nothing watched the SR stage's own output until rig run 32: the pass
    answered ``hr=0x1`` while the frame the user finally saw was BLACK, so we
    could not tell an SR pass that produced a real image from one that produced
    an empty buffer (or a no-op). ``payload_in``/``payload_out`` are the RGBA8
    byte payloads handed to and returned by ``EvaluateFeature``.

    Levels mirror ``native_output_verdict``: "error" for an output that cannot
    be a real DLAA result (all-black from a non-black input), "warning" for a
    no-op (byte-identical output - the pass ran and changed nothing), "info"
    when it did something. The caller logs; it never raises.
    """
    if payload_in == payload_out:
        return "warning", ("[ANTs] SR stage: the DLAA pass returned the input "
                           "BYTE-IDENTICAL - the pass ran (hr=1) and changed "
                           "nothing. With MV.Scale 0 + zeroed depth/motion "
                           "that is the shape of a no-op; if the frame should "
                           "be denoised, the parameters are the place to look.")
    if not payload_out.strip(b"\x00"):
        return "error", ("[ANTs] SR stage: the DLAA pass returned an ALL-BLACK "
                         "frame for a non-black input - the output texture was "
                         "never written, or the feature evaluated into a "
                         "buffer that is not the output we read back. The "
                         "frame handed to the engine is black regardless of "
                         "the engine's own state; report this line.\n"
                         "    To skip the stage: pre_denoise_mode OFF or "
                         "sr_strength 0.")
    zero = payload_out.count(0)
    return "info", ("[ANTs] SR stage: DLAA output differs from the input "
                    "(%d of %d bytes zero) - the pass produced a real image.",
                    zero, len(payload_out))


def engine_output_verdict(dest_all_zero, source_has_content, cxx_report):
    """(level, text) for one legacy-engine pass over a CUDA destination.

    Rig 04:19 (run 32): the SR stage and the legacy engine are two NGX clients
    in one process now; ``dlss5nr_process_cuda_v6`` threw a C++ exception
    (0xE06D7363), the engine's own handler swallowed it and the call returned
    "fine" - while the destination (``torch.empty``) was never written. The
    user saw a black frame and no error at all. Two signals, both cheap:

    * an all-black destination produced from a non-black source - the
      never-written buffer signature;
    * a C++ throw recorded during the call (``crashlog.cxx_serial()`` moved).
    """
    if dest_all_zero and source_has_content:
        text = ("[ANTs] the legacy engine returned an EMPTY (all-black) frame "
                "for a non-black input - the destination buffer was never "
                "written, so this frame is not the engine's output.")
        if cxx_report:
            text += (" A C++ exception was recorded during the call:\n    "
                     + cxx_report + "\n    The engine caught its own throw and "
                     "returned as if fine, which is why nothing was reported "
                     "until now.")
        text += ("\n    The SR pre-denoise stage and the legacy engine are two "
                 "NGX clients in one process (NGX keeps ONE context per "
                 "process - see ants/dlsssr/ngx.py); if this began exactly "
                 "when the SR stage started to run, that combination is the "
                 "one to bisect.\n"
                 "    Ways out: engine 'ANTs native NGX' (no 3rd-party "
                 "engine), pre_denoise_mode OFF, or sr_strength 0.")
        return "error", text
    if cxx_report:
        return "warning", ("[ANTs] the legacy engine threw a C++ exception "
                           "during this pass but the frame came back "
                           "written:\n    " + cxx_report + "\n"
                           "    Report this line if the image looks wrong - "
                           "the engine is recovering from its own error.")
    return None, ""


def soak_enabled():
    """``ANTS_NR_SOAK=1`` - one line per run with handles + torch VRAM.

    The 50-prompt soak: both numbers should stay flat. A per-prompt init that
    leaked a handle or a hundred megabytes would show long before prompt 50.
    """
    return os.environ.get("ANTS_NR_SOAK", "").strip().lower() in (
        "1", "true", "yes", "on")


def soak_line(uses=None):
    """Handles + torch VRAM as one line (see :func:`soak_enabled`)."""
    from ..dlsssr import win32
    parts = []
    handles = win32.process_handle_count()
    if handles is not None:
        parts.append(f"process handles {handles}")
    try:
        import torch as _torch
        if _torch.cuda.is_available():
            parts.append("torch VRAM %.0f MiB allocated, %.0f MiB reserved" % (
                _torch.cuda.memory_allocated() / 1048576.0,
                _torch.cuda.memory_reserved() / 1048576.0))
    except Exception:
        pass
    if uses is not None:
        parts.append(f"NR session frames {int(uses)}")
    return "[ANTs] soak: " + (", ".join(parts) if parts
                              else "no data on this host")


# --- pre-denoise stage selection (pure; exactly the rule the loop uses) ------
def pre_denoise_action(mode, strength, has_model):
    """What the pre-denoise stage does for ONE pass: "sr", "model" or None.

    SR mode needs no model - the 1:1 DLAA pass through our own SR host IS the
    stage, and the strength is only its on/off gate. Model mode needs the wired
    model. OFF, or a strength of ~0, disables the stage for that pass.

    Engine-independent by construction: the SR stage runs BEFORE whichever NR
    engine is selected (native NGX or the legacy neuroframe engine), because it
    is our own SR host and not part of either engine.
    """
    if mode == PRE_DENOISE_OFF or float(strength) <= 1e-4:
        return None
    if mode == PRE_DENOISE_SR:
        return "sr"
    return "model" if has_model else None


# Cross-prompt session cache: key -> (session, gpu). Only touched when
# ANTS_NR_SESSION_CACHE=1. A dict, not one slot, so a workflow that runs two
# sizes (or two engines) never has ONE node close the OTHER node's feature:
# entries are evicted only when their own session is closed (`_close_native`,
# `_drop_session`).
_NR_SESSION_CACHE = {}


class ReFactorDLSS5Enhancer:
    def __init__(self):
        self.device = model_management.get_torch_device()
        self.manager = None
        self.manager_dll_dir = None
        self._ordinal = 0
        self.native_gpu = None
        self.native_session = None
        self.native_key = None
        self.native_dll_path = None
        self._native_checked = False     # first native frame of this run
        self._soak_logged = False
        self._sr_checked = False         # the SR pass's output verdict (rig 32)
        self._engine_warned = False      # "did your C++ throw recover?" (rig 32)
        self._nr_preset = 0
        self._sr_choice = "auto"
        self._sr_preset = "Default"
        self._sr_stage_dir = None
        self._sr_sessions = {}

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "nr_dll_version": (discovery.category_choices("NR"),
                                   {"tooltip": "NR runtime for the native engine - EVERY .dll directly "
                                               "inside models/DLSS/NR/ is listed individually (plus one "
                                               "entry per version subfolder), NEWEST FIRST. 'auto' loads "
                                               "the newest build according to the naming rule: put a date "
                                               "(nvngx_dlssnr_2026-09-14.dll) or a version "
                                               "(nvngx_dlssnr_310.9.1.dll) in the file name - see "
                                               "docs/MODELS_DLSS_LAYOUT.md. An explicit pick always wins. "
                                               "Use the refresh button after adding files."}),
                "sr_dll_version": (discovery.category_choices("SR"),
                                   {"tooltip": "SR runtime (nvngx_dlss*.dll) - each .dll directly inside "
                                               "models/DLSS/SR/ is listed individually (e.g. nvngx_dlss.dll "
                                               "AND nvngx_dlss_310.9.1.dll), plus version subfolders, NEWEST "
                                               "FIRST. 'auto' loads the newest build by the naming rule "
                                               "(NVIDIA's own version numbers in the file name). Used by the "
                                               "pre-denoise SR pass (and the SR node)."}),
                "fg_dll_version": (discovery.category_choices("FG"),
                                   {"tooltip": "Frame Generation builds (models/DLSS/FG/). RESERVED for a "
                                               "future release - selecting a build has no effect yet."}),
                "sr_model": (SR_MODEL_CHOICES,
                             {"default": SR_MODEL_DEFAULT,
                              "tooltip": "SR transformer model (DLSS.Hint.Render.Preset): J=Transformer I "
                                         "Crisp, K=Transformer I Stable, L=Transformer II Quality, "
                                         "M=Transformer II Fast. Default: L (newest, highest quality)."}),
                "nr_model_preset": (list(_NR_PRESET_TO_INT),
                                    {"default": "Default",
                                     "tooltip": "NR model preset hint (J/K/L/M). Best-effort: the stock NR "
                                                "runtime ignores unknown preset hints; builds that read the "
                                                "hint switch transformer models."}),
                "pre_denoise_mode": ([PRE_DENOISE_OFF, PRE_DENOISE_SR, PRE_DENOISE_MODEL],
                                     {"default": PRE_DENOISE_SR,
                                      "tooltip": "What runs as the pre-SR denoise pass: the ANTs DLSS SR host "
                                                 "(1:1 DLAA with the chosen sr_dll_version + sr_model; runs on BOTH "
                                                 "engines - it is our host, not the engine's; needs "
                                                 "nvngx_dlss*.dll in models/DLSS/SR/), the "
                                                 "wired upscale/denoise model (SCUNet-style), or OFF - no "
                                                 "pre-denoise stage at all, even with a model connected and "
                                                 "pre_denoise_strength above 0 (the widget greys out). "
                                                 "Default: SR."}),
                "gpu_acceleration": ([GPU_AUTO, GPU_FORCE, GPU_OFF],
                                     {"default": GPU_AUTO,
                                      "tooltip": "Auto/Force: frames are processed GPU-resident via the engine's "
                                                 "CUDA entry (dlss5nr_process_cuda_v6) - zero PCIe copies when the "
                                                 "batch already lives in VRAM; orders of magnitude faster at 4K and "
                                                 "with nr_passes > 1. CPU: legacy host staging (compat fallback). "
                                                 "If the engine DLL is too old for CUDA, a clear error tells you."}),
                "engine": ([ENGINE_NATIVE, ENGINE_LEGACY],
                           {"default": ENGINE_NATIVE,
                            "tooltip": "ANTs native NGX: our own pure-Python host drives nvngx_dlssnr.dll "
                                       "directly (full control, no 3rd-party helper DLLs; 8-bit RGBA path). "
                                       "Legacy neuroframe: the original helper-DLL engine (float32 path, "
                                       "kept as a fallback; scheduled for removal once the native host "
                                       "is validated on your rig)."}),
                "use_nr_schedule": ("BOOLEAN", {"default": False, "label_off": "OFF", "label_on": "ON",
                                                "tooltip": "Replace the engine's single-pass reconstruction with a multi-pass "
                                                           "NR Schedule from the ANTs DLSS NR Scheduler node (varied styles per "
                                                           "pass - measurably better than the old nr_passes repeat). When the "
                                                           "schedule carries per-pass settings, those bypass this node's widgets."}),
                "style": (["Default", "Natural", "Cinematic"],),
                "intensity": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.05, "tooltip": "0..2, def: 1.0"}),
                "local_tone": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 2.0, "step": 0.05, "tooltip": "0..2, def: 0.0"}),
                "local_structure": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.05, "tooltip": "0..2, def: 1.0"}),
                "skin_structure": ("FLOAT", {"default": 0.5, "min": -1.0, "max": 2.0, "step": 0.05, "tooltip": "-1..2, def: 0.5"}),
                "color_strength": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05, "tooltip": "0..1, def: 0.5"}),
                "tone_preservation": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05, "tooltip": "0..1, def: 0.5"}),
                "face_skin_protection": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05, "tooltip": "0..1, def: 0.0"}),
                "grain_preservation": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05, "tooltip": "0..1, def: 0.0"}),
                "auto_mask": ("BOOLEAN", {"default": False, "label_off": "OFF", "label_on": "ON", "tooltip": "Smart Protection Mask"}),
                "temporal_history": (["Auto (scene-aware)", "Continuous", "Per-frame reset"],
                                     {"default": "Auto (scene-aware)",
                                      "tooltip": "Auto: the DLL's temporal history resets only on detected scene changes "
                                                 "(threshold below). Continuous: never resets after the first frame (best for "
                                                 "video-like batches). Per-frame reset: classic single-image behavior."}),
                "scene_change_threshold": ("FLOAT", {"default": 0.24, "min": 0.01, "max": 1.0, "step": 0.01,
                                                     "tooltip": "Mean-abs frame difference above which Auto history resets (OreX-style)."}),
                "hdr_bridge_mode": (["Classic (Paper-White Gain)", "Anchored (Auto White Point)", "Off"],
                                    {"default": "Classic (Paper-White Gain)",
                                     "tooltip": "HDR Colour Bridge around the DLSS-NR model (RenoDX-inspired). Classic: fixed "
                                                "paper-white gain (the first-generation bridge). Anchored: the white point "
                                                "auto-anchors to each frame's measured highlights, with a black-floor lever."}),
                "diffuse_white_nits": ("FLOAT", {"default": DIFFUSE_WHITE_NITS_DEFAULT, "min": 80.0, "max": 480.0, "step": 1.0,
                                                 "tooltip": "Diffuse white of the target display in nits (RenoDX default 237)."}),
                "scene_paper_white_scale": ("FLOAT", {"default": PAPER_WHITE_SCALE_DEFAULT, "min": 0.1, "max": 10.0, "step": 0.01,
                                                      "tooltip": "Gain applied to scene linear before the shoulder (RenoDX 'Scene Paper-White Scale')."}),
                "hdr_transfer_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.05,
                                                    "tooltip": "How strongly the bridge transfer applies (0 = off, 1 = full, >1 adds gain)."}),
                "bridge_color_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.05,
                                                    "tooltip": "Chroma preservation around luma in the bridge (1 = unchanged)."}),
                "black_lever": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05,
                                          "tooltip": "Anchored mode only: restores the black floor after the highlight-anchored gain."}),
                "pre_denoise_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05,
                                                   "tooltip": "Strength of the optional pre-SR denoise model (0 = off). "
                                                              "Only matters when a denoise_model is connected."}),
            },
            "optional": {
                "mask": ("MASK",),
                "denoise_model": ("UPSCALE_MODEL",),
                "nr_schedule": ("NR_SCHEDULE",),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("enhanced_image",)
    FUNCTION = "enhance"
    CATEGORY = "ANTs"
    DESCRIPTION = (
        "Hybrid DLSS-NR enhancer: helper-bridge engine (neuroframe, by Merserk,\n"
        "distributed via Gourieff's ReActor dataset) + HDR Colour Bridge stage\n"
        "(Classic / Anchored, after RenoDX's DLSS 5 colour work by clshortfuse,\n"
        "MIT) + OreX-inspired temporal history (github.com/orex2121/\n"
        "ComfyUI-DLSS5-orex).\n"
        "Optional pre-SR denoise: connect a 1x denoising/restoration model from\n"
        "ANTs Upscale Model Loader (SCUNet, PureScale2 1x_PureVision, ...) - it\n"
        "runs through the comfy-native tiled pipeline before the engine.\n"
        "Requirements:\n"
        "- NVIDIA display driver >= 616.x, RTX 40/50-series GPU\n"
        "- NR builds into ComfyUI/models/DLSS/NR/ - ANY .dll filenames accepted\n"
        "(the engine is identified by its exports, not by name); the neuroframe\n"
        "helper pair goes into ComfyUI/models/DLSS/Merserk_DLLS/ once (it is\n"
        "copied into the staged working folder automatically).\n"
        "nvngx_dlssnr.dll must be procured by you (redistribution prohibited).\n"
        "Layout, and what is safe to delete: docs/MODELS_DLSS_LAYOUT.md"
    )

    @staticmethod
    def _forget_cached(session):
        """Evict a session that is being closed - the cache must never hand out
        a closed feature (it is what makes the cross-prompt reuse safe)."""
        if session is None:
            return
        for key, entry in list(_NR_SESSION_CACHE.items()):
            if entry[0] is session:
                del _NR_SESSION_CACHE[key]

    def _drop_session(self):
        """Close this instance's NR session (the device stays; see
        :meth:`_close_native`)."""
        if self.native_session is not None:
            self._forget_cached(self.native_session)
            try:
                self.native_session.close()
            except Exception:
                pass
            self.native_session = None
            self.native_key = None

    def _close_native(self):
        self._forget_cached(self.native_session)
        if self.native_session is not None:
            try:
                self.native_session.close()
            except Exception:
                pass
            self.native_session = None
        if self.native_gpu is not None:
            try:
                self.native_gpu.close()
            except Exception:
                pass
            self.native_gpu = None
        # the SR pre-denoise sessions live on that same device - drop them too,
        # or a later frame would reuse a feature on a closed device
        self._sr_sessions.clear()
        self.native_key = None

    def load_bridge(self, nr_choice: str, engine: str = ENGINE_LEGACY):
        if engine == ENGINE_NATIVE:
            # Tear down the legacy engine if it was up; bring up our host.
            if self.manager is not None:
                try:
                    self.manager.shutdown()
                except Exception:
                    pass
                del self.manager
                self.manager = None
            dll_path = discovery.resolve_nr_runtime_path(nr_choice)
            note = discovery.provenance_note(dll_path)
            if note:
                # Informational, never a gate: the community RenoDX-derived
                # builds are the working path on RTX 30/40 series (the
                # official DLSS 5 NR runtime targets RTX 50). The history
                # behind this line lives at the top of ants/dlssnr/discovery.py.
                logger.status(f"[ANTs] NR engine: {note}")
            self._ordinal = self.device.index if getattr(self.device, "index", None) is not None else 0
            # Sessions are size-keyed and created lazily on the first frame
            # (see _native_session_for); NGX providers must not be churned.
            self.native_key = None
            self.native_dll_path = dll_path
            how = ("auto - newest by the naming rule"
                   if (not nr_choice or nr_choice in ("auto", "refresh"))
                   else "explicitly selected")
            logger.status(
                f"DLSS-5 native NGX host engine: "
                f"{discovery.describe_runtime(dll_path)} [{how}]; the session "
                "is created on the first frame.")
            return
        # Legacy helper engine
        self._close_native()
        dll_path = discovery.resolve_nr_runtime_path(nr_choice)
        dll_dir = discovery.stage_legacy_runtime(dll_path)  # canonical names
        if self.manager is None or self.manager_dll_dir != dll_dir:
            if self.manager is not None:
                try:
                    self.manager.shutdown()
                except Exception:
                    pass
                del self.manager
                self.manager = None
            self.manager = DLSSStandaloneManager(dll_dir)
            self._ordinal = self.device.index if getattr(self.device, "index", None) is not None else 0
            try:
                self.manager.initialize(self._ordinal)
            except Exception as exc:
                text = str(exc)
                # The engine matches its CUDA device by adapter LUID. When it
                # cannot, in practice it is either a wedged process (a D3D12
                # device was REMOVED earlier in this same ComfyUI session - the
                # driver then refuses new devices) or a helper build that does
                # not belong to this GPU generation. Say both, with the fix.
                if "by LUID" in text or "LUID" in text:
                    raise RuntimeError(
                        f"[ANTs] The neuroframe engine could not create a "
                        f"D3D12 device for CUDA device {self._ordinal}: {text}\n"
                        "    The engine matches its CUDA device to the D3D12 "
                        "device by adapter LUID. Two causes, in order of "
                        "likelihood:\n"
                        "      (1) an earlier run in THIS ComfyUI process lost "
                        "its D3D12 device (removed / GPU timeout) - after that "
                        "the driver refuses new devices until the process "
                        "restarts. Restart ComfyUI and run again.\n"
                        "      (2) a FRESH process on a machine with more than "
                        "one GPU: launch ComfyUI with --cuda-device <the one "
                        "id> (a single GPU) and/or --disable-pinned-memory - a "
                        "Windows CUDA driver bug can poison the process once "
                        "more than one GPU is touched (ComfyUI issue #15255).\n"
                        "    Compare the '[ANTs] D3D12 host adapter' line above "
                        "(our pick, matched to this CUDA ordinal by LUID) with "
                        "the engine's own CUDA device.\n"
                        "    If it still fails in a fresh single-GPU process, "
                        "the helper pair in models/DLSS/Merserk_DLLS is not "
                        "the one this GPU needs - run "
                        "tools\\collect_rig_evidence.bat and send the "
                        "HELPER / ENGINE INVENTORY section (it lists every "
                        "helper DLL with its size, hash and exports).\n"
                        f"    Engine set in use: {dll_dir}") from exc
                raise

            self.manager_dll_dir = dll_dir
            gpu = self.manager.gpu_name() or f"GPU {self._ordinal}"
            logger.status(f"DLSS-5 Bridge initialized on {gpu} using DLL set: "
                          f"{dll_dir} "
                          f"[nvngx_dlssnr: {discovery.describe_runtime(dll_path)}]")

    def _ensure_native_gpu(self):
        """The D3D12 GPU context is created once, on demand - whichever
        session type (NR or the SR pre-denoise) needs it first."""
        if self.native_gpu is None:
            # The engine matches its CUDA device by LUID, so the D3D12 device
            # must be created on the adapter that belongs to self._ordinal.
            from ..dlsssr.d3d12 import make_gpu_context
            self.native_gpu = make_gpu_context(self._ordinal)
        return self.native_gpu

    def _native_session_for(self, width, height, pass_settings):
        """Size-keyed native NR session (created lazily, reused across frames).

        With ``ANTS_NR_SESSION_CACHE=1`` it also survives the PROMPT boundary
        (ComfyUI makes a new node instance per prompt, so without the switch the
        whole NGX init runs again for every prompt - see
        :func:`session_cache_enabled`).
        """
        key = (self.native_dll_path, width, height, self._nr_preset)
        cached = _NR_SESSION_CACHE.get(key) if session_cache_enabled() else None
        if cached is not None:
            # same dll/size/preset as an earlier PROMPT: reuse its device and
            # feature as they are - do not build a second device just to drop it
            if self.native_session is not None and self.native_session is not cached[0]:
                self._drop_session()
            self.native_session, self.native_gpu = cached
            self.native_key = key
            self.native_session.settings.update(pass_settings)
            return self.native_session
        self._ensure_native_gpu()
        if self.native_session is None or self.native_key != key:
            self._drop_session()
            from ..dlsssr.nr import DlssNrSession
            self.native_session = DlssNrSession(self.native_gpu, width, height,
                                                self.native_dll_path,
                                                nr_preset=self._nr_preset)
            self.native_key = key
        if session_cache_enabled():
            _NR_SESSION_CACHE[key] = (self.native_session, self.native_gpu)
        # look controls are re-set by the host before every evaluate; keep
        # the session's dict in sync with the pass plan
        self.native_session.settings.update(pass_settings)
        return self.native_session

    def _check_native_output(self, out, payload, sess):
        """Log once per prompt whether the engine actually changed the frame.

        See :func:`native_output_verdict`. The counts come from the RGBA16F
        readback (``sess.last_output_anomalies``) - by the time the frame is a
        tensor it has passed the host clamp, which is exactly what would hide a
        NaN or a saturated output.
        """
        if self._native_checked:
            return
        self._native_checked = True
        anomalies = getattr(sess, "last_output_anomalies", None) or (0, 0, 0)
        level, text = native_output_verdict(out == payload, anomalies[0],
                                            anomalies[1], anomalies[2])
        if level == "error":
            logger.error("%s", text)
        elif level == "warning":
            logger.warning("%s", text)

    def _prime_sr_before_engine(self, image, engine, mode, strength):
        """Opt-in: create the SR session BEFORE the legacy engine inits NGX.

        Rig 32 (04:19): the SR pass itself now works (Init_ProjectID, feature
        1, EvaluateFeature all hr=1, a 168 MB DLSS feature built by the core),
        and the LEGACY ENGINE then threw a C++ exception inside
        ``dlss5nr_process_cuda_v6`` - the SR stage and the engine are two NGX
        clients in ONE process (NGX keeps one context per process), and the
        order has always been "engine inits first" because ``load_bridge``
        runs before the first SR pass. This switch creates our SR session
        (D3D12 device + NGX feature) first, so the engine becomes the second
        client - the ordering that has never run. Opt-in, best-effort: a
        failure here is logged and the normal lazy path takes over.
        """
        if os.environ.get("ANTS_LEGACY_SR_FIRST") != "1":
            return
        if engine != ENGINE_LEGACY or mode == PRE_DENOISE_OFF:
            return
        if pre_denoise_action(mode, strength, None) != "sr":
            return
        try:
            height, width = int(image.shape[-3]), int(image.shape[-2])
            logger.status(
                "[ANTs] ANTS_LEGACY_SR_FIRST=1 EXPERIMENT: creating the SR "
                "session (%dx%d) BEFORE the legacy engine inits NGX - the "
                "engine then becomes the second NGX client in this process.",
                width, height)
            self._sr_session_for(width, height)
        except Exception as exc:
            logger.warning("[ANTs] early SR session failed (%s) - the normal "
                           "order takes over.", exc)

    def _sr_session_for(self, width, height):
        """Lazily created 1:1 DLAA SR session (pre-denoise pass), keyed by
        dll stage / size / preset so model swaps never churn the GPU side."""
        from ..dlsssr import discovery as sr_disc
        self._ensure_native_gpu()  # may run before the NR session exists
        if self._sr_stage_dir is None:
            dll_path = sr_disc.resolve_sr_dll(self._sr_choice)
            self._sr_stage_dir = sr_disc.stage_sr_dll(dll_path)
        key = (self._sr_stage_dir, width, height, self._sr_preset)
        sess = self._sr_sessions.get(key)
        if sess is None:
            from ..dlsssr.sr import DlssSrSession
            sess = DlssSrSession(self.native_gpu, width, height, width, height,
                                 mode="DLAA", preset=self._sr_preset,
                                 sr_dll_dir=self._sr_stage_dir)
            self._sr_sessions[key] = sess
        return sess

    @staticmethod
    def _frame_digest(frame_t):
        """A cheap content digest of a torch frame (SR verdict identity test).

        Byte-identity is the honest no-op test, but holding two full frames to
        compare them doubles the peak; a digest compares a few thousand values
        instead (identical digest == identical frame for this purpose).
        """
        if frame_t.numel() == 0:
            return ()
        flat = frame_t.reshape(-1)
        idx = np.unique(np.linspace(0, flat.numel() - 1, 4096,
                                    dtype=np.int64))
        return tuple(float(v) for v in flat[idx].float().cpu().tolist())

    def _sr_denoise_frame(self, frame_t, reset, device=None, report=False):
        """One 1:1 DLAA pass over a torch frame; returns the denoised frame.

        ``device`` is where the result lands (default: the node's torch
        device). The legacy CUDA path passes its own ``cuda:<ordinal>`` and the
        host-staging path passes CPU, so neither bounces the frame through a
        device it does not use. ``report=True`` also returns the output
        verdict (see :func:`sr_output_verdict`) instead of only the frame.
        """
        if not report:
            payload, w, h = self._frame_to_rgba8(frame_t)
            out = self._sr_session_for(w, h).evaluate(payload, reset=reset)
            return self._rgba8_to_frame(out, w, h,
                                        self.device if device is None else device)
        # report=True: collect the verdict (rig 32). The frame is compared in
        # its OWN dtype/range so the digest is not perturbed by the transfer
        # (a [0,1] byte round trip would make the SR pass look like a change
        # even when it is the identity); only values the byte transfer would
        # clip anyway are clipped here, which is what the blackness test means.
        digest_before = self._frame_digest(frame_t)
        payload, w, h = self._frame_to_rgba8(frame_t)
        out = self._sr_session_for(w, h).evaluate(payload, reset=reset)
        frame = self._rgba8_to_frame(out, w, h,
                                     self.device if device is None else device)
        if self._frame_digest(frame) == digest_before:
            return frame, ("warning",
                           "[ANTs] SR stage: the DLAA pass left the frame "
                           "IDENTICAL (all-black stays all-black) - it ran "
                           "(hr=1) and changed nothing. With MV.Scale 0 + "
                           "zeroed depth/motion that is the shape of a no-op; "
                           "if the frame should be denoised, the parameters "
                           "are the place to look.")
        return frame, sr_output_verdict(payload, out)

    def _log_sr_output(self, note):
        """Log the SR pass's own output verdict once per prompt (rig 32).

        The pass answered hr=0x1 while the frame the user saw was black, and
        nothing in the pack watched the SR stage's output - this closes that
        blind spot. An all-black SR output is an ERROR (loud, named), a
        byte-identical one is a warning, a real image is a status line.
        """
        if self._sr_checked:
            return
        self._sr_checked = True
        level, text = note
        if isinstance(text, tuple):
            fmt, args = text[0], text[1:]
        else:
            fmt, args = "%s", (text,)
        if level == "error":
            raise RuntimeError(fmt % args if args else fmt)
        if level == "warning":
            logger.warning(fmt, *args)
        else:
            logger.status(fmt, *args)

    def _engine_verdict(self, dest, source, cxx_before, entry):
        """Loud-failure guard for one legacy-engine pass (rig run 32).

        ``dest``/``source`` are the CUDA tensors (None on the host-staging
        path, where the destination is a reused ping-pong buffer). A pass that
        produced an all-black destination from a non-black source, or that
        threw a C++ exception, is reported - never a silent black frame.
        """
        report = (crashlog.last_cxx_report()
                  if crashlog.cxx_serial() != cxx_before else None)
        dest_all_zero = source_has = None
        if dest is not None:
            dest_all_zero = not bool(dest.any())
            source_has = bool(source.any()) if dest_all_zero else True
        if report:
            # The engine's own error buffer survives its swallowed throw (rig
            # 32) - always carry it, it is the only place its complaint could
            # be spelled out.
            engine_says = getattr(self.manager, "last_error", "") or ""
            if engine_says.strip():
                report += f"\n    the engine's own error buffer: {engine_says!r}"
        level, text = engine_output_verdict(dest_all_zero, source_has, report)
        if level == "error":
            raise RuntimeError(text)
        if level == "warning" and not self._engine_warned:
            self._engine_warned = True
            logger.warning("%s", text)

    def _sr_denoise_np(self, frame_np, reset, report=False):
        """The SR stage for the numpy (legacy host-staging) path: numpy in, out."""
        t = torch.from_numpy(np.ascontiguousarray(frame_np))
        out = self._sr_denoise_frame(t, reset, device="cpu", report=report)
        if report:
            frame, note = out
            return frame.numpy(), note
        return out.numpy()

    @staticmethod
    def _frame_to_rgba8(frame_t):
        import numpy as _np
        rgb8 = (_np.clip(frame_t.cpu().numpy(), 0.0, 1.0) * 255.0).round().astype(_np.uint8)
        h, w = rgb8.shape[0], rgb8.shape[1]
        return rgba_bytes_from_rgb8(rgb8), w, h

    @staticmethod
    def _rgba8_to_frame(payload, width, height, device):
        import numpy as _np
        arr = _np.frombuffer(payload, dtype=_np.uint8).reshape(height, width, 4)
        t = torch.from_numpy(arr[:, :, :3].astype(_np.float32) / 255.0)
        return t.to(device)

    def _pre_denoise_frame(self, frame, model, strength):
        """Run a comfy-style 1x denoise/restoration model on one [H,W,C] torch
        frame (any device), then blend it with the original by `strength`.

        Uses the comfy-core mirror (spandrel model, tiled, OOM-aware); output
        is resized back to the input resolution, so 1x models are identity-size
        and larger models degrade gracefully into denoisers.
        """
        from ..upscaler import upscale_image_with_model

        import comfy.utils

        height, width = int(frame.shape[0]), int(frame.shape[1])
        out = upscale_image_with_model(model, frame.unsqueeze(0))  # [1,h,w,3]
        if tuple(out.shape[1:3]) != (height, width):
            out = comfy.utils.common_upscale(out.movedim(-1, 1), width, height,
                                             "lanczos", "disabled").movedim(1, -1)
        out = out[0].to(device=frame.device, dtype=torch.float32).contiguous()
        return blend_frames(frame, out, float(strength))

    # Focused subclasses (see ReFactorDLSS5Processor / ...Native) drop whole
    # widgets, so every input they may drop carries a default here and lives
    # at the END of the signature. ComfyUI calls by keyword, so the order is
    # irrelevant to it.
    ENGINE_MODE = None          # forced engine for subclasses (None = the widget)

    def enhance(self, image, nr_dll_version, use_nr_schedule,
                style, intensity, local_tone, local_structure, skin_structure,
                color_strength, tone_preservation, face_skin_protection,
                grain_preservation, auto_mask, temporal_history, scene_change_threshold,
                hdr_bridge_mode, diffuse_white_nits, scene_paper_white_scale,
                hdr_transfer_strength, bridge_color_strength, black_lever,
                pre_denoise_strength, denoise_model=None, nr_schedule=None, mask=None,
                engine=ENGINE_NATIVE, sr_dll_version="auto", fg_dll_version="auto",
                sr_model=SR_MODEL_DEFAULT, pre_denoise_mode=PRE_DENOISE_SR,
                gpu_acceleration=GPU_AUTO, nr_model_preset="Default"):

        if self.ENGINE_MODE is not None:
            engine = self.ENGINE_MODE
        # ANTS_LEGACY_SR_FIRST=1 creates the SR session BEFORE the legacy
        # engine's own NGX init - the one ordering rig 32 has not tested.
        self._prime_sr_before_engine(image, engine, pre_denoise_mode,
                                     pre_denoise_strength)
        self.load_bridge(nr_dll_version, engine)
        native = engine == ENGINE_NATIVE
        self._nr_preset = int(_NR_PRESET_TO_INT.get(nr_model_preset, 0))
        self._sr_choice = sr_dll_version
        self._sr_preset = _PRESET_TO_LETTER.get(sr_model, "Default")
        # The FIRST NGX init in a process pins the core's feature-library
        # search paths (rig 02:48): record the selection NOW so that init can
        # already list the SR build - see dlsssr.discovery.ensure_staged_sr_dir.
        try:
            from ..dlsssr import discovery as _sr_discovery
            _sr_discovery.remember_sr_choice(self._sr_choice)
        except Exception:
            pass
        if fg_dll_version not in ("auto",):
            logger.status(f"FG build '{fg_dll_version}' selected - Frame Generation "
                          "is reserved for a future release; no effect yet.")
        if pre_denoise_mode == PRE_DENOISE_OFF:
            logger.status("[ANTs] pre-denoise is OFF - nothing runs before the "
                          "engine (a connected denoise_model and "
                          "pre_denoise_strength are ignored for this run).")
        elif pre_denoise_mode == PRE_DENOISE_SR:
            # The 1:1 DLAA stage is OUR SR host, not a part of the NR engine,
            # so it runs before either engine - it only needs an SR runtime
            # (a loud error names models/DLSS/SR if there is none).
            logger.status("[ANTs] pre-denoise: SR mode - the 1:1 DLAA pass runs "
                          "before the engine on ANY engine (needs nvngx_dlss*.dll "
                          "in models/DLSS/SR/).")

        settings = {
            "style": nr_schedule_lib.STYLES[style], "intensity": intensity,
            "local_tone": local_tone, "local_structure": local_structure,
            "skin_structure": skin_structure, "color_strength": color_strength,
            "tone_preservation": tone_preservation,
            "face_skin_protection": face_skin_protection,
            "grain_preservation": grain_preservation, "auto_mask": auto_mask,
            "nr_passes": 1,  # schedules replace the engine-side repeat counter
            "shimmer_suppression": 0.0, "prefer_nvof": False
        }

        # ---- resolve the per-pass plan (schedules vs single pass) ----
        if use_nr_schedule:
            if not nr_schedule or not isinstance(nr_schedule, dict) or "schedule" not in nr_schedule:
                raise RuntimeError(
                    "[ANTs] Use NR Schedule is ON, but no NR_SCHEDULE is connected. "
                    "Add an 'ANTs DLSS NR Scheduler' node and connect its nr_schedule "
                    "output to this node's nr_schedule input.")
            plan = nr_schedule_lib.build_pass_plan(
                nr_schedule["schedule"], nr_schedule.get("denoise_models", ()),
                main_settings=settings,
                main_denoise_model=denoise_model,
                main_denoise_strength=float(pre_denoise_strength),
                sr_stage=pre_denoise_mode == PRE_DENOISE_SR)
            logger.status(f"ANTs DLSS5 NR Schedule engaged: {nr_schedule_lib.describe(nr_schedule['schedule'])}")
        else:
            plan = [{
                "style": settings["style"], "settings": settings,
                "denoise_model": denoise_model,
                "denoise_strength": float(pre_denoise_strength)
                if (denoise_model is not None
                    or pre_denoise_mode == PRE_DENOISE_SR) else 0.0,
            }]

        enhanced_batch = []

        pbar = progress_bar(len(image))

        if native:
            use_cuda, cuda_why = False, "native NGX host (D3D12 path)"
            if gpu_acceleration == GPU_OFF:
                logger.status(
                    "[ANTs] note: this node always runs on the GPU (the native "
                    "NGX host is a D3D12 + device-pointer path) - the 'CPU "
                    "(host staging)' choice applies to the legacy engine only.")
            if mask is not None:
                logger.status("DLSS5 note: the native NGX engine has no mask-plane input - "
                              "the connected mask is ignored (Auto Mask still applies).")
        else:
            engine_ok, engine_why = self.manager.cuda_available()
            # The engine's own gate is the "FFmpeg blocking-sync" context-flag
            # check; name what the process's CUDA context carries and, if the
            # operator asks for it, try the CUDA entry point anyway (A/B - the
            # crash box is armed on this path, so a refusal is captured).
            cuda_flags.log_early()
            if os.environ.get("ANTS_NR_CUDA_FORCE", "") == "1" and not engine_ok:
                engine_ok = True
                engine_why = (engine_why + " - FORCED by ANTS_NR_CUDA_FORCE=1: "
                              "the engine's own gate refused, the CUDA entry "
                              "point is tried anyway")
            use_cuda, cuda_why = decide_cuda_acceleration(
                gpu_acceleration, torch.cuda.is_available(), engine_ok,
                engine_why)
            # Always name the helper build we are running: when the GPU path
            # is missing, THIS line is what says which engine caused it.
            try:
                logger.status(f"[ANTs] {self.manager.engine_report()}")
            except Exception as exc:                     # never fatal
                logger.status(f"[ANTs] engine report unavailable: {exc}")
        logger.status(f"DLSS5 processing via {'CUDA' if use_cuda else 'host staging (CPU)'} - {cuda_why}")
        if not use_cuda and gpu_acceleration != GPU_OFF and not native:
            logger.warning(
                "[ANTs] GPU acceleration is ON but this run uses CPU staging. "
                "On a CUDA machine that is a 20-25x slowdown for DLSS-NR. "
                "The engine line above names the helper build that is loaded "
                "and whether it exports the CUDA entry points; if it does not, "
                "replace the neuroframe pair in models/DLSS/Merserk_DLLS with "
                "a build that carries dlss5nr_process_cuda_v6 "
                "(Gourieff's neuroframe_dlls.zip has the current one). "
                "Context flags: " + cuda_flags.summary())

        if use_cuda:
            cuda_dev = torch.device(f"cuda:{self._ordinal}")
            # one PCIe transfer for the whole batch when it starts on CPU;
            # no copy at all when it is already GPU-resident
            src_gpu = image.to(device=cuda_dev, dtype=torch.float32)
            if not src_gpu.is_contiguous():
                src_gpu = src_gpu.contiguous()
            torch.cuda.synchronize(cuda_dev)

        denoise_passes = [] if pre_denoise_mode == PRE_DENOISE_OFF else [
            i + 1 for i, spec in enumerate(plan)
            if pre_denoise_action(pre_denoise_mode, spec["denoise_strength"],
                                 spec["denoise_model"] is not None) is not None]
        if denoise_passes:
            if pre_denoise_mode == PRE_DENOISE_SR:
                engine_name = "native NGX" if native else "legacy"
                logger.status(f"DLSS5 pre-SR denoise on pass(es) {denoise_passes}: "
                              f"ANTs SR host, 1:1 DLAA before the {engine_name} "
                              f"engine (dll '{sr_dll_version}', "
                              f"model '{sr_model}')")
            else:
                first = next((spec["denoise_model"] for spec in plan
                              if spec["denoise_model"] is not None), None)
                if first is not None:
                    scale = getattr(first, "scale", 1)
                    logger.status(f"DLSS5 pre-SR denoise on pass(es) {denoise_passes}: "
                                  f"{scale}x model from the denoise_model input")
                    if scale != 1:
                        logger.status("DLSS5 pre-SR denoise note: non-1x model connected - its "
                                      "output is resized back to the input resolution before the engine.")

        bridge_kwargs = dict(
            diffuse_white_nits=float(diffuse_white_nits),
            paper_white_scale=float(scene_paper_white_scale),
            transfer_strength=float(hdr_transfer_strength),
            color_strength=float(bridge_color_strength),
            black_lever=float(black_lever),
        )
        bridge_mode = {"Classic (Paper-White Gain)": "classic",
                       "Anchored (Auto White Point)": "anchored"}.get(hdr_bridge_mode, "off")
        _reset_next = True
        _prev_thumb = None

        src_np = None
        cpu_bufs = None
        if not use_cuda:
            # legacy host staging: convert the batch ONCE; multi-pass plans
            # ping-pong between two destination buffers (the engine must
            # never read and write the same memory in one call)
            src_np = np.ascontiguousarray(image.cpu().numpy().astype(np.float32))
            cpu_bufs = [np.empty_like(src_np[0])]
            if len(plan) > 1:
                cpu_bufs.append(np.empty_like(src_np[0]))

        for i in range(len(image)):

            if state.interrupted or model_management.processing_interrupted():
                logger.status("Interrupted by User")
                break

            if use_cuda:
                frame = src_gpu[i]
            elif native:
                frame_t = image[i]
            else:
                frame_np = src_np[i]

            mask_np = None
            if mask is not None:
                mask_np = np.ascontiguousarray(mask[i].cpu().numpy().astype(np.float32))

            # NR schedule: passes chain (each pass re-processes the previous
            # pass output - the owner-validated pattern). Temporal history is
            # a main-node-only setting applied to every pass.
            for pass_idx, pass_spec in enumerate(plan):
                pass_settings = {"style": pass_spec["style"],
                                 **pass_spec["settings"],
                                 "nr_passes": 1,
                                 "shimmer_suppression": 0.0, "prefer_nvof": False}

                # temporal history: decided per pass input (OreX-style
                # scene-aware auto at frame level)
                do_reset = True
                if temporal_history == "Continuous":
                    do_reset = _reset_next
                elif temporal_history == "Auto (scene-aware)":
                    if use_cuda:
                        thumb = frame[::16, ::16].mean(axis=2).cpu().numpy()
                    elif native:
                        thumb = frame_t[::16, ::16].mean(axis=2).cpu().numpy()
                    else:
                        thumb = frame_np[::16, ::16].mean(axis=2)
                    if _prev_thumb is not None and _prev_thumb.shape == thumb.shape:
                        diff = float(np.abs(thumb - _prev_thumb).mean())
                        do_reset = diff > float(scene_change_threshold)
                    else:
                        do_reset = True
                    _prev_thumb = thumb
                _reset_next = False

                d_model = pass_spec["denoise_model"]
                d_strength = pass_spec["denoise_strength"]
                action = pre_denoise_action(pre_denoise_mode, d_strength,
                                            d_model is not None)

                if native:
                    if action == "sr":
                        frame_t, sr_note = self._sr_denoise_frame(
                            frame_t, do_reset, report=True)
                        self._log_sr_output(sr_note)
                    elif action == "model":
                        frame_t = self._pre_denoise_frame(frame_t, d_model, d_strength)
                    look = {"style": pass_spec["style"], **pass_spec["settings"]}
                    sess = self._native_session_for(int(frame_t.shape[1]),
                                                    int(frame_t.shape[0]), look)
                    payload, w_px, h_px = self._frame_to_rgba8(frame_t)
                    try:
                        out = sess.evaluate(
                            payload, reset=do_reset,
                            check_anomalies=not self._native_checked)
                    except Exception as exc:
                        # A failing evaluate can leave the NGX feature (and the
                        # snippet's internal state) mid-flight; run 30 showed a
                        # C++ throw from the snippet. Drop the session so the
                        # next queue item builds a fresh one instead of
                        # evaluating into a half-dead feature, and keep the
                        # original error as the one the user sees.
                        #
                        # A REMOVED device is worse than a failed feature: the
                        # D3D12 device itself is gone, so the GPU context must
                        # go too, or every later frame fails on a dead object
                        # (rig 20:39: two E_INVALIDARGs then CreateCommandAllocator
                        # 0x887A0005).
                        if getattr(self.native_gpu, "dead", None) is not None \
                                or "REMOVED" in str(exc).upper():
                            self._close_native()
                            raise RuntimeError(
                                str(exc) + "\n    The GPU context and the NGX "
                                "session were dropped; the next queued frame "
                                "will build a fresh device (if the driver "
                                "stays wedged, restart ComfyUI).") from exc
                        self._close_native()
                        raise
                    frame_t = self._rgba8_to_frame(out, w_px, h_px, self.device)
                    self._check_native_output(out, payload, sess)
                    if soak_enabled() and not self._soak_logged:
                        self._soak_logged = True
                        logger.status("%s", soak_line(
                            getattr(sess, "evaluates", None)))
                elif use_cuda:
                    if action == "sr":
                        # NGX's DLSS kernels run through the CUDA DRIVER (the
                        # core's log: NGXCubinD3D12 "Enabling CuModule kernel
                        # path"), and the engine's zero-copy path assumes the
                        # torch primary context that owns its device pointers.
                        # Observe before/after; re-assert only on evidence.
                        ctx_before = cuda_flags.ctx_flags()[0]
                        frame, sr_note = self._sr_denoise_frame(
                            frame, do_reset, device=cuda_dev, report=True)
                        self._log_sr_output(sr_note)
                        ctx_after = cuda_flags.ctx_flags()[0]
                        if ctx_before is not None and ctx_after is not None \
                                and ctx_after != ctx_before:
                            logger.warning(
                                "[ANTs] the SR pass left the CUDA context "
                                "flags at 0x%02X (they were 0x%02X) - NGX ran "
                                "CUDA kernels of its own. Re-asserting the "
                                "torch primary context (device %s) before the "
                                "legacy engine call, which assumes it.",
                                ctx_after, ctx_before, cuda_dev)
                            torch.cuda.set_device(cuda_dev)
                    elif action == "model":
                        frame = self._pre_denoise_frame(frame, d_model, d_strength)
                    if not frame.is_contiguous():
                        frame = frame.contiguous()
                    # not empty_like: it preserves the planar strides of a
                    # movedim view, and the engine writes interleaved rows
                    dest = torch.empty(tuple(frame.shape), device=frame.device,
                                       dtype=torch.float32)
                    cxx_before = crashlog.cxx_serial()
                    self.manager.process_cuda(
                        frame.data_ptr(), dest.data_ptr(),
                        frame.shape[1], frame.shape[0],
                        settings=pass_settings, reset=do_reset, mask=mask_np)
                    # the engine's D3D12/NGX work runs on the shared primary
                    # context - this makes the result visible to torch
                    torch.cuda.synchronize(cuda_dev)
                    self._engine_verdict(dest, frame, cxx_before,
                                         "dlss5nr_process_cuda_v6")
                    frame = dest
                else:
                    if action == "sr":
                        frame_np, sr_note = self._sr_denoise_np(
                            frame_np, do_reset, report=True)
                        self._log_sr_output(sr_note)
                    elif action == "model":
                        frame_np = self._pre_denoise_frame(
                            torch.from_numpy(frame_np), d_model, d_strength).numpy()
                    dest_np = cpu_bufs[pass_idx % len(cpu_bufs)]
                    cxx_before = crashlog.cxx_serial()
                    self.manager.process_host(
                        source=frame_np,
                        destination=dest_np,
                        settings=pass_settings,
                        reset=do_reset,
                        mask=mask_np
                    )
                    self._engine_verdict(None, None, cxx_before,
                                         "dlss5nr_process_v6")
                    frame_np = dest_np

            # HDR Colour Bridge: global, applied once after the final pass
            if native:
                if bridge_mode != "off":
                    frame_t = apply_bridge(frame_t, bridge_mode, **bridge_kwargs)
                out_tensor = frame_t
            elif use_cuda:
                if bridge_mode != "off":
                    frame = apply_bridge(frame, bridge_mode, **bridge_kwargs)
                out_tensor = frame if frame.device == self.device else frame.to(self.device)
            else:
                np_out = frame_np if bridge_mode == "off" else apply_bridge(frame_np, bridge_mode, **bridge_kwargs)
                t = torch.from_numpy(np.ascontiguousarray(np_out))
                out_tensor = t.to(self.device) if self.device.type != "cpu" else t.clone()

            enhanced_batch.append(out_tensor)

            pbar.update(1)

        progress_bar_reset(pbar)

        return (torch.stack(enhanced_batch),)


# --------------------------------------------------------------------------
# Focused processors (owner request, 2026-09-20): one node per engine, so a
# run can never pick the other path by accident and the debug surface of each
# engine stays independent. Both take the SAME ANTs DLSS NR Scheduler output,
# both keep the HDR Colour Bridge and the whole look control set; they differ
# only in which engine they drive and therefore in which widgets they show.
#
# Naming (owner asked): the DLL lineage is the RenoDX DLSS-5 addon
# (clshortfuse, MIT) - a ReShade addon - repacked by Merserk and bridged by
# the community "neuroframe" helper pair. OptiScaler is a DIFFERENT project (a
# DLSS/XeSS/FSR call redirector) and none of its code is involved here, so the
# accurate short name is ReShade-based.
# --------------------------------------------------------------------------

def _without_inputs(types, names):
    """A copy of an INPUT_TYPES dict without the named widgets."""
    import copy
    types = copy.deepcopy(types)
    for section in types.values():
        if isinstance(section, dict):
            for name in names:
                section.pop(name, None)
    return types


class ReFactorDLSS5Processor(ReFactorDLSS5Enhancer):
    """ANTs DLSS5 Processor (legacy / ReShade-RenoDX-derived DLL engine).

    Identical to the full enhancer EXCEPT that the engine is locked: the
    ``engine`` selector is not rendered and the engine cannot be changed at
    run time (owner request - the three nodes must keep full settings and
    feature parity, so the only difference between them is the engine).

    The engine it drives is the one that already works on this rig (the
    RenoDX-derived nvngx_dlssnr.dll through the neuroframe helper pair,
    including its CUDA zero-copy path).
    """

    ENGINE_MODE = ENGINE_LEGACY
    DESCRIPTION = (
        "DLSS5 frame enhancement through the legacy DLL engine (RenoDX-derived "
        "nvngx_dlssnr.dll + the neuroframe helper pair by Merserk, credit to "
        "clshortfuse's RenoDX work) - the engine that runs this rig today, "
        "including its CUDA zero-copy path (GPU acceleration ON = CUDA).\n"
        "Same controls as ANTs DLSS5 Frame Enhancer; the engine selector is "
        "locked to this engine and the engine-related widgets that path cannot "
        "use say so instead of lying.\n"
        "Multi-pass plans come from the ANTs DLSS NR Scheduler node "
        "(nr_schedule input)."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return _without_inputs(ReFactorDLSS5Enhancer.INPUT_TYPES(), ("engine",))


class ReFactorDLSS5ProcessorNative(ReFactorDLSS5Enhancer):
    """ANTs DLSS5 Processor (experimental, the pack's own native NGX host).

    Identical to the full enhancer EXCEPT that the engine is locked - here to
    the pack's own pure-Python D3D12 + NGX host (no 3rd-party helper DLLs,
    feature 18 through the caller shim, SR pre-denoise available). Separate
    node so the native path can fail loudly without touching the working one.
    """

    ENGINE_MODE = ENGINE_NATIVE
    DESCRIPTION = (
        "EXPERIMENTAL: the pack's own pure-Python NGX host drives "
        "nvngx_dlssnr.dll directly (D3D12 device, feature 18 through the "
        "caller shim, no 3rd-party helper DLLs). Separate node so the native "
        "path can fail loudly without touching the working legacy processor.\n"
        "Same controls as ANTs DLSS5 Frame Enhancer; the engine selector is "
        "locked to this engine.\n"
        "Requirements: an NR runtime in ComfyUI/models/DLSS/NR/ (any filename; "
        "the pack probes exports, never names) and, on RTX 30/40, a "
        "RenoDX-derived build. Diagnostics: the crash black box, the in-flight "
        "call labels and the CUDA context-flag report all land in the console "
        "and in models/DLSS/staged/ANTs/appdata/logs/native-crash.log.\n"
        "Multi-pass plans come from the ANTs DLSS NR Scheduler node."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return _without_inputs(ReFactorDLSS5Enhancer.INPUT_TYPES(), ("engine",))

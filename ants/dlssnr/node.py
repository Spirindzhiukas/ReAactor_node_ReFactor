"""DLSS5 Frame Enhancer node.

NVIDIA's DLSS-NR DLLs are 3rd-party, manually installed binaries (see
``discovery.py``). This node gained, per the project owner's request, a
``dll_version`` selector so several user-supplied DLL generations can live
side by side (``models/dlssnr/<version>/``) and be switched per workflow —
useful as new DLSS 5.x DLL releases appear.
"""

import numpy as np
import torch

import comfy.model_management as model_management

from ..log import logger
from ..scripting import state
from ..utils import (
    progress_bar,
    progress_bar_reset,
)
from . import discovery
from . import schedule as nr_schedule_lib
from .core import DLSSStandaloneManager
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
    """
    amount = float(amount)
    if amount >= 1.0 - 1e-4:
        return processed
    if amount <= 1e-4:
        return base
    return base * (1.0 - amount) + processed * amount


def decide_cuda_acceleration(mode: str, torch_cuda_available: bool, engine_cuda_ok: bool):
    """Pure decision: run the CUDA device-pointer path? (unit-testable)"""
    if mode == GPU_OFF:
        return False, "CPU mode selected (host staging)"
    if not torch_cuda_available:
        return False, "torch reports no CUDA device"
    if not engine_cuda_ok:
        return False, f"engine lacks CUDA interop ({engine_cuda_ok})"
    return True, "CUDA device-pointer path"


class ReFactorDLSS5Enhancer:
    def __init__(self):
        self.device = model_management.get_torch_device()
        self.manager = None
        self.manager_dll_dir = None
        self._ordinal = 0

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "dll_version": (discovery.combo_choices(),
                                {"tooltip": "Which user-supplied DLSS-NR DLL set to use "
                                            "(models/DLSS/dlssnr_<version>/, any .dll filenames). 'auto' picks "
                                            "the first found set; 'refresh' re-scans after you add DLLs (then re-select)."}),
                "gpu_acceleration": ([GPU_AUTO, GPU_FORCE, GPU_OFF],
                                     {"default": GPU_AUTO,
                                      "tooltip": "Auto/Force: frames are processed GPU-resident via the engine's "
                                                 "CUDA entry (dlss5nr_process_cuda_v6) - zero PCIe copies when the "
                                                 "batch already lives in VRAM; orders of magnitude faster at 4K and "
                                                 "with nr_passes > 1. CPU: legacy host staging (compat fallback). "
                                                 "If the engine DLL is too old for CUDA, a clear error tells you."}),
                "use_nr_schedule": ("BOOLEAN", {"default": False, "label_off": "OFF", "label_on": "ON",
                                                "tooltip": "Replace the engine's single-pass reconstruction with a multi-pass "
                                                           "NR Schedule from the ANTs DLSS NR Scheduler node (varied styles per "
                                                           "pass - measurably better than the old nr_passes repeat). When the "
                                                           "schedule carries per-pass settings, those bypass this node's widgets."}),
                "style": (["Default", "Nature", "Cinematic"],),
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
        "- DLLs into ComfyUI/models/DLSS/dlssnr_<version>/ - ANY .dll filenames\n"
        "accepted (the engine is identified by its exports, not by name);\n"
        "nvngx_dlssnr.dll must be procured by you (redistribution prohibited)."
    )

    def load_bridge(self, dll_version: str):
        dll_dir = discovery.resolve_dll_dir(dll_version)
        if self.manager is None or self.manager_dll_dir != dll_dir:
            if self.manager is not None:
                try:
                    del self.manager
                except Exception:
                    pass
                self.manager = None
            self.manager = DLSSStandaloneManager(dll_dir)
            self._ordinal = self.device.index if getattr(self.device, "index", None) is not None else 0
            self.manager.initialize(self._ordinal)
            self.manager_dll_dir = dll_dir
            gpu = self.manager.gpu_name() or f"GPU {self._ordinal}"
            logger.status(f"DLSS-5 Bridge initialized on {gpu} using DLL set: {dll_dir}")

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
        out = out[0].to(device=frame.device, dtype=torch.float32)
        return blend_frames(frame, out, float(strength))

    def enhance(self, image, dll_version, gpu_acceleration, use_nr_schedule, style,
                intensity, local_tone, local_structure, skin_structure,
                color_strength, tone_preservation, face_skin_protection,
                grain_preservation, auto_mask, temporal_history, scene_change_threshold,
                hdr_bridge_mode, diffuse_white_nits, scene_paper_white_scale,
                hdr_transfer_strength, bridge_color_strength, black_lever,
                pre_denoise_strength, denoise_model=None, nr_schedule=None, mask=None):

        if dll_version == "refresh":
            discovery.combo_choices()  # re-scan; user then re-selects the new entry

        self.load_bridge(dll_version)

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
                main_denoise_strength=float(pre_denoise_strength))
            logger.status(f"ANTs DLSS5 NR Schedule engaged: {nr_schedule_lib.describe(nr_schedule['schedule'])}")
        else:
            plan = [{
                "style": settings["style"], "settings": settings,
                "denoise_model": denoise_model,
                "denoise_strength": float(pre_denoise_strength) if denoise_model is not None else 0.0,
            }]

        enhanced_batch = []

        pbar = progress_bar(len(image))

        use_cuda, cuda_why = decide_cuda_acceleration(
            gpu_acceleration, torch.cuda.is_available(), self.manager.cuda_available()[0])
        logger.status(f"DLSS5 processing via {'CUDA' if use_cuda else 'host staging (CPU)'} - {cuda_why}")

        if use_cuda:
            cuda_dev = torch.device(f"cuda:{self._ordinal}")
            # one PCIe transfer for the whole batch when it starts on CPU;
            # no copy at all when it is already GPU-resident
            src_gpu = image.to(device=cuda_dev, dtype=torch.float32)
            if not src_gpu.is_contiguous():
                src_gpu = src_gpu.contiguous()
            torch.cuda.synchronize(cuda_dev)

        denoise_passes = [i + 1 for i, spec in enumerate(plan)
                          if spec["denoise_model"] is not None and spec["denoise_strength"] > 1e-4]
        if denoise_passes:
            first = next(spec["denoise_model"] for spec in plan
                         if spec["denoise_model"] is not None)
            scale = getattr(first, "scale", 1)
            logger.status(f"DLSS5 pre-SR denoise on pass(es) {denoise_passes}: "
                          f"{scale}x model from the Upscale-Model pipeline")
            if scale != 1:
                logger.status("DLSS5 pre-SR denoise note: non-1x model connected - its output is "
                              "resized back to the input resolution before the engine.")

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
                denoise_this = d_model is not None and d_strength > 1e-4

                if use_cuda:
                    if denoise_this:
                        frame = self._pre_denoise_frame(frame, d_model, d_strength)
                    dest = torch.empty_like(frame)
                    self.manager.process_cuda(
                        frame.data_ptr(), dest.data_ptr(),
                        frame.shape[1], frame.shape[0],
                        settings=pass_settings, reset=do_reset, mask=mask_np)
                    # the engine's D3D12/NGX work runs on the shared primary
                    # context - this makes the result visible to torch
                    torch.cuda.synchronize(cuda_dev)
                    frame = dest
                else:
                    if denoise_this:
                        frame_np = self._pre_denoise_frame(
                            torch.from_numpy(frame_np), d_model, d_strength).numpy()
                    dest_np = cpu_bufs[pass_idx % len(cpu_bufs)]
                    self.manager.process_host(
                        source=frame_np,
                        destination=dest_np,
                        settings=pass_settings,
                        reset=do_reset,
                        mask=mask_np
                    )
                    frame_np = dest_np

            # HDR Colour Bridge: global, applied once after the final pass
            if use_cuda:
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

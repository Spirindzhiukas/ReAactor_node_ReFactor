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
from .core import DLSSStandaloneManager
from .hdr_bridge import (
    DIFFUSE_WHITE_NITS_DEFAULT,
    PAPER_WHITE_SCALE_DEFAULT,
    apply_bridge,
)


GPU_AUTO = "Auto (GPU when available)"
GPU_FORCE = "Force GPU (CUDA)"
GPU_OFF = "CPU (host staging)"


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
                "style": (["Default", "Nature", "Cinematic"],),
                "intensity": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.05, "tooltip": "0..2, def: 1.0"}),
                "local_tone": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 2.0, "step": 0.05, "tooltip": "0..2, def: 0.0"}),
                "local_structure": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.05, "tooltip": "0..2, def: 1.0"}),
                "skin_structure": ("FLOAT", {"default": 0.5, "min": -1.0, "max": 2.0, "step": 0.05, "tooltip": "-1..2, def: 0.5"}),
                "color_strength": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05, "tooltip": "0..1, def: 0.5"}),
                "tone_preservation": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05, "tooltip": "0..1, def: 0.5"}),
                "face_skin_protection": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05, "tooltip": "0..1, def: 0.0"}),
                "grain_preservation": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05, "tooltip": "0..1, def: 0.0"}),
                "nr_passes": ("INT", {"default": 1, "min": 1, "max": 4, "tooltip": "0..4, def: 1"}),
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
            },
            "optional": {
                "mask": ("MASK",),
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

    def enhance(self, image, dll_version, gpu_acceleration, style, intensity, local_tone,
                local_structure, skin_structure, color_strength, tone_preservation,
                face_skin_protection, grain_preservation, nr_passes, auto_mask,
                temporal_history, scene_change_threshold,
                hdr_bridge_mode, diffuse_white_nits, scene_paper_white_scale,
                hdr_transfer_strength, bridge_color_strength, black_lever, mask=None):

        if dll_version == "refresh":
            discovery.combo_choices()  # re-scan; user then re-selects the new entry

        self.load_bridge(dll_version)

        style_map = {"Default": 0, "Nature": 1, "Cinematic": 2}
        settings = {
            "style": style_map[style], "intensity": intensity, "local_tone": local_tone,
            "local_structure": local_structure, "skin_structure": skin_structure,
            "color_strength": color_strength, "tone_preservation": tone_preservation,
            "face_skin_protection": face_skin_protection, "grain_preservation": grain_preservation,
            "nr_passes": nr_passes, "auto_mask": auto_mask,
            "shimmer_suppression": 0.0, "prefer_nvof": False
        }

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
        dest_np = None
        if not use_cuda:
            # legacy host staging: convert the batch ONCE, reuse one
            # destination buffer across frames (still correct: the engine
            # writes every pixel of it per call)
            src_np = np.ascontiguousarray(image.cpu().numpy().astype(np.float32))
            dest_np = np.empty_like(src_np[0])

        for i in range(len(image)):

            if state.interrupted or model_management.processing_interrupted():
                logger.status("Interrupted by User")
                break

            # temporal history: decide the reset for this frame (OreX-style
            # scene-aware auto, at frame level)
            do_reset = True
            if temporal_history == "Continuous":
                do_reset = _reset_next
            elif temporal_history == "Auto (scene-aware)":
                if use_cuda:
                    thumb = src_gpu[i][::16, ::16].mean(axis=2).cpu().numpy()
                else:
                    thumb = src_np[i][::16, ::16].mean(axis=2)
                if _prev_thumb is not None and _prev_thumb.shape == thumb.shape:
                    diff = float(np.abs(thumb - _prev_thumb).mean())
                    do_reset = diff > float(scene_change_threshold)
                else:
                    do_reset = True
                _prev_thumb = thumb
            _reset_next = False

            mask_np = None
            if mask is not None:
                mask_np = np.ascontiguousarray(mask[i].cpu().numpy().astype(np.float32))

            if use_cuda:
                frame = src_gpu[i]
                dest = torch.empty_like(frame)
                self.manager.process_cuda(
                    frame.data_ptr(), dest.data_ptr(),
                    frame.shape[1], frame.shape[0],
                    settings=settings, reset=do_reset, mask=mask_np)
                # the engine's D3D12/NGX work runs on the shared primary
                # context - this makes the result visible to torch
                torch.cuda.synchronize(cuda_dev)

                if bridge_mode != "off":
                    dest = apply_bridge(dest, bridge_mode, **bridge_kwargs)

                out_tensor = dest if dest.device == self.device else dest.to(self.device)
            else:
                self.manager.process_host(
                    source=src_np[i],
                    destination=dest_np,
                    settings=settings,
                    reset=do_reset,
                    mask=mask_np
                )

                np_out = dest_np if bridge_mode == "off" else apply_bridge(dest_np, bridge_mode, **bridge_kwargs)
                t = torch.from_numpy(np.ascontiguousarray(np_out))
                out_tensor = t.to(self.device) if self.device.type != "cpu" else t.clone()

            enhanced_batch.append(out_tensor)

            pbar.update(1)

        progress_bar_reset(pbar)

        return (torch.stack(enhanced_batch),)

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
    batch_tensor_to_pil,
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


class ReFactorDLSS5Enhancer:
    def __init__(self):
        self.device = model_management.get_torch_device()
        self.manager = None
        self.manager_dll_dir = None

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "dll_version": (discovery.combo_choices(),
                                {"tooltip": "Which user-supplied DLSS-NR DLL set to use "
                                            "(models/dlssnr/<version>/). 'auto' picks the best complete set; "
                                            "'refresh' re-scans after you add DLLs (then re-select)."}),
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
            ordinal = getattr(self.device, "index", 0) if self.device.index is not None else 0
            self.manager.initialize(ordinal)
            self.manager_dll_dir = dll_dir
            logger.status(f"DLSS-5 Bridge initialized on GPU {ordinal} using DLL set: {dll_dir}")

    def enhance(self, image, dll_version, style, intensity, local_tone, local_structure,
                skin_structure, color_strength, tone_preservation,
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

        pil_images = batch_tensor_to_pil(image)
        pbar = progress_bar(len(pil_images))

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

        for i in range(len(image)):

            if state.interrupted or model_management.processing_interrupted():
                logger.status("Interrupted by User")
                break

            img_np = np.ascontiguousarray(image[i].cpu().numpy().astype(np.float32))

            # temporal history: decide the reset for this frame (OreX-style
            # scene-aware auto, at frame level)
            do_reset = True
            if temporal_history == "Continuous":
                do_reset = _reset_next
            elif temporal_history == "Auto (scene-aware)":
                thumb = img_np[::16, ::16].mean(axis=2)
                if _prev_thumb is not None and _prev_thumb.shape == thumb.shape:
                    diff = float(np.abs(thumb - _prev_thumb).mean())
                    do_reset = diff > float(scene_change_threshold)
                else:
                    do_reset = True
                _prev_thumb = thumb
            _reset_next = False

            dest_np = np.ascontiguousarray(np.zeros_like(img_np))

            mask_np = None
            if mask is not None:
                mask_np = np.ascontiguousarray(mask[i].cpu().numpy().astype(np.float32))

            # Host-side processing (no CUDA context conflicts)
            self.manager.process_host(
                source=img_np,
                destination=dest_np,
                settings=settings,
                reset=do_reset,
                mask=mask_np
            )

            if bridge_mode != "off":
                dest_np = apply_bridge(dest_np, bridge_mode, **bridge_kwargs)

            out_tensor = torch.from_numpy(dest_np).to(self.device)
            enhanced_batch.append(out_tensor)

            pbar.update(1)

        progress_bar_reset(pbar)

        return (torch.stack(enhanced_batch),)

"""DLSS5 Frame Enhancer node.

NVIDIA's DLSS-NR DLLs are 3rd-party, manually installed binaries (see
``discovery.py``). This node gained, per the project owner's request, a
``dll_version`` selector so several user-supplied DLL generations can live
side by side (``models/dlssnr/<version>/``) and be switched per workflow —
useful as new DLSS 5.x DLL releases appear.
"""

import numpy as np
import os
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
            },
            "optional": {
                "mask": ("MASK",),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("enhanced_image",)
    FUNCTION = "enhance"
    CATEGORY = "ReFactor"
    DESCRIPTION = (
        "Requirements:\n"
        "- NVIDIA display driver >= 616.x\n"
        "- NVIDIA RTX 40/50-series GPU\n"
        "(compatibility with older RTX series is unconfirmed)\n"
        "- DLLs: neuroframe_caller.dll, neuroframe_engine.dll, nvngx_dlssnr.dll\n"
        "in ComfyUI/models/dlssnr/<version>/ (3rd-party, manually installed —\n"
        "see rfactor/dlssnr/dll_README.md for sources)"
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
                face_skin_protection, grain_preservation, nr_passes, auto_mask, mask=None):

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

        for i in range(len(image)):

            if state.interrupted or model_management.processing_interrupted():
                logger.status("Interrupted by User")
                break

            img_np = np.ascontiguousarray(image[i].cpu().numpy().astype(np.float32))
            dest_np = np.ascontiguousarray(np.zeros_like(img_np))

            mask_np = None
            if mask is not None:
                mask_np = np.ascontiguousarray(mask[i].cpu().numpy().astype(np.float32))

            # Host-side processing (no CUDA context conflicts)
            self.manager.process_host(
                source=img_np,
                destination=dest_np,
                settings=settings,
                reset=True,
                mask=mask_np
            )

            out_tensor = torch.from_numpy(dest_np).to(self.device)
            enhanced_batch.append(out_tensor)

            pbar.update(1)

        progress_bar_reset(pbar)

        return (torch.stack(enhanced_batch),)

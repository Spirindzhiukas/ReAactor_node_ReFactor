"""MaskBuilder: the replacement for the old ultralytics+SAM ``MaskHelper``.

Design goals (owner's brief: "more robust and configurable than the original"):
- Segmentation comes from **ComfyUI's own ecosystem**: plug ``SAM3_Detect``
  (or SAM2, EfficientSAM, RMBG, LVM ... any MASK source) into the ``mask``
  socket, and/or its ``BOUNDING_BOX`` output into ``bboxes``.
- Zero extra pip dependencies: no ultralytics, no segment_anything package.
- A built-in, always-available fallback that derives soft face-region masks
  from the nodepack's own SCRFD detector.
- Full post-processing chain: grow / morphology / feather / invert.
"""

import cv2
import numpy as np
import torch

from ..log import logger
from ..swapper import analyze_faces
from . import ops


def _mask_to_numpy(mask, height: int, width: int) -> np.ndarray:
    """[H,W]/[B,H,W]/[B,1,H,W] tensor -> [H,W] float32 array at the target size."""
    if mask.dim() == 2:
        mask = mask.unsqueeze(0)
    if mask.dim() == 4:
        mask = mask.squeeze(1)
    mask = mask[0]  # external sources yield single masks here
    if mask.shape[-2] != height or mask.shape[-1] != width:
        mask = torch.nn.functional.interpolate(
            mask.unsqueeze(0).unsqueeze(0).float(), size=(height, width),
            mode="bilinear", align_corners=False,
        ).squeeze()
    return mask.detach().cpu().numpy().astype(np.float32)


class ReFactorMaskBuilder:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
                "fallback": (["Face Region (built-in)", "Full Frame", "None"],
                             {"tooltip": "What to do when neither 'mask' nor 'bboxes' is connected.\n"
                                         "Face Region: soft oval masks from the built-in face detector.\n"
                                         "Full Frame: mask the whole image.\nNone: empty mask (pass-through)."}),
                "face_crop_factor": ("FLOAT", {"default": 1.5, "min": 1.0, "max": 4.0, "step": 0.05,
                                               "tooltip": "Fallback face region size relative to the detected face box"}),
                "face_falloff": ("INT", {"default": 24, "min": 0, "max": 256, "step": 1,
                                         "tooltip": "Fallback face region edge softness (px)"}),
                "grow": ("INT", {"default": 0, "min": -512, "max": 512, "step": 1,
                                 "tooltip": "Expand (positive) or shrink (negative) the final mask"}),
                "morphology_operation": (["none", "dilate", "erode", "open", "close"],),
                "morphology_distance": ("INT", {"default": 0, "min": 0, "max": 128, "step": 1}),
                "blur_radius": ("INT", {"default": 9, "min": 0, "max": 64, "step": 1}),
                "sigma_factor": ("FLOAT", {"default": 1.0, "min": 0.01, "max": 3.0, "step": 0.01}),
                "invert": ("BOOLEAN", {"default": False, "label_off": "OFF", "label_on": "ON"}),
            },
            "optional": {
                "swapped_image": ("IMAGE",),
                "mask": ("MASK", {"force_input": True,
                                  "tooltip": "Optional segmentation mask, e.g. from ComfyUI's SAM3_Detect"}),
                "bboxes": ("BOUNDING_BOX", {"force_input": True,
                                            "tooltip": "Optional bounding boxes, e.g. from ComfyUI's SAM3_Detect"}),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK", "IMAGE", "IMAGE")
    RETURN_NAMES = ("IMAGE", "MASK", "MASK_PREVIEW", "SWAPPED_FACE")
    FUNCTION = "execute"
    CATEGORY = "ReFactor"

    def execute(self, image, fallback, face_crop_factor, face_falloff, grow,
                morphology_operation, morphology_distance, blur_radius, sigma_factor,
                invert, swapped_image=None, mask=None, bboxes=None):
        batch, height, width, _ = image.shape

        # Accumulate per-frame masks as numpy [H,W] stacks -> [B,H,W]
        layers = []  # each: np.ndarray [B,H,W]

        if mask is not None:
            layer = np.zeros((batch, height, width), dtype=np.float32)
            m = _mask_to_numpy(mask, height, width)
            layer[:] = m
            layers.append(layer)
            logger.status("MaskBuilder: using external MASK input")

        if bboxes is not None:
            layer = np.zeros((batch, height, width), dtype=np.float32)
            if isinstance(bboxes, list) and bboxes and isinstance(bboxes[0], (list, tuple)):
                for b in range(min(batch, len(bboxes))):
                    layer[b] = ops.bboxes_to_mask(bboxes[b], height, width)
                for b in range(len(bboxes), batch):
                    layer[b] = layer[0]
            else:
                single = ops.bboxes_to_mask(bboxes, height, width)
                layer[:] = single
            layers.append(layer)
            logger.status("MaskBuilder: using external BOUNDING_BOX input")

        if not layers:
            layer = np.zeros((batch, height, width), dtype=np.float32)
            if fallback == "Face Region (built-in)":
                for b in range(batch):
                    img_bgr = cv2.cvtColor((image[b].detach().cpu().numpy() * 255.0).astype(np.uint8),
                                           cv2.COLOR_RGB2BGR)
                    faces = analyze_faces(img_bgr)
                    if not faces:
                        logger.status("MaskBuilder: no faces found for the built-in face region fallback")
                    else:
                        layer[b] = ops.face_region_mask(img_bgr, faces, face_crop_factor, face_falloff)
                logger.status("MaskBuilder: using built-in face region fallback")
            elif fallback == "Full Frame":
                layer[:] = 1.0
            layers.append(layer)

        combined = np.zeros((batch, height, width), dtype=np.float32)
        for layer in layers:
            combined = np.maximum(combined, layer)

        for b in range(batch):
            m = combined[b]
            m = ops.grow_mask(m, grow)
            m = ops.apply_morphology(m, morphology_operation, morphology_distance)
            m = ops.feather_mask(m, blur_radius, sigma_factor)
            if invert:
                m = 1.0 - m
            combined[b] = np.clip(m, 0.0, 1.0)

        combined_t = torch.from_numpy(combined).to(image.device)
        combined_t = combined_t.unsqueeze(-1)  # [B,H,W,1]

        mask_preview = image.mul(combined_t).clamp(0.0, 1.0)

        if swapped_image is not None:
            swapped_image = swapped_image.to(image.device)
            if swapped_image.shape[-3:-1] != (height, width):
                swapped_image = torch.nn.functional.interpolate(
                    swapped_image.movedim(-1, 1), size=(height, width), mode="bilinear", align_corners=False
                ).movedim(1, -1)
            swapped_face = (swapped_image.mul(combined_t) + image.mul(1.0 - combined_t)).clamp(0.0, 1.0)
        else:
            swapped_face = mask_preview

        return (image, combined_t.squeeze(-1), mask_preview, swapped_face)

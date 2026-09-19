"""ComfyUI-native upscale-model loading and tiled upscaling.

This mirrors comfy_extras/nodes_upscale_model.py (UpscaleModelLoader +
ImageUpscaleWithModel) one-to-one - same spandrel loading, same
CoreModelPatcher handling when the running core provides it, same
tiled_scale with OOM tile-halving. Nothing invented here on purpose, so
behavior is identical to the stock "Upscale Image (using Model)" node.
"""

import numpy as np
import torch

import comfy.model_management
import comfy.utils

try:  # same optional extra-arch support as the core node
    from spandrel_extra_arches import EXTRA_REGISTRY
    from spandrel import MAIN_REGISTRY

    MAIN_REGISTRY.add(*EXTRA_REGISTRY)
except Exception:
    pass


def load_upscale_model(model_path):
    """Load an upscale model exactly like the core UpscaleModelLoader."""
    from spandrel import ImageModelDescriptor, ModelLoader

    sd = comfy.utils.load_torch_file(model_path, safe_load=True)
    if "module.layers.0.residual_group.blocks.0.norm1.weight" in sd:
        sd = comfy.utils.state_dict_prefix_replace(sd, {"module.": ""})
    out = ModelLoader().load_from_state_dict(sd).eval()

    if not isinstance(out, ImageModelDescriptor):
        raise Exception("Upscale model must be a single-image model.")

    if hasattr(comfy, "model_patcher"):
        try:
            out.patcher = comfy.model_patcher.CoreModelPatcher(
                out.model,
                load_device=comfy.model_management.get_torch_device(),
                offload_device=comfy.model_management.unet_offload_device(),
            )
        except Exception:
            pass  # older cores: the model is used via plain .to(device)
    return out


def upscale_image_with_model(upscale_model, image):
    """Upscale a (B,H,W,C) float [0,1] RGB tensor exactly like the core
    "Upscale Image (using Model)" node (tiled, OOM-aware)."""
    device = comfy.model_management.get_torch_device()
    patcher = getattr(upscale_model, "patcher", None)
    if patcher is not None:
        memory_required = (512 * 512 * 3) * image.element_size() * max(upscale_model.scale, 1.0) * 384.0
        memory_required += image.nelement() * image.element_size()
        comfy.model_management.load_models_gpu([patcher], memory_required=memory_required, force_full_load=True)
        device = patcher.load_device
    else:
        upscale_model.to(device)

    in_img = image.movedim(-1, -3).to(device)

    tile = 512
    overlap = 32
    output_device = comfy.model_management.intermediate_device()

    oom = True
    while oom:
        try:
            steps = in_img.shape[0] * comfy.utils.get_tiled_scale_steps(
                in_img.shape[3], in_img.shape[2], tile_x=tile, tile_y=tile, overlap=overlap)
            pbar = comfy.utils.ProgressBar(steps)
            s = comfy.utils.tiled_scale(
                in_img, lambda a: upscale_model(a.float()),
                tile_x=tile, tile_y=tile, overlap=overlap,
                upscale_amount=upscale_model.scale, pbar=pbar, output_device=output_device)
            oom = False
        except Exception as e:
            comfy.model_management.raise_non_oom(e)
            tile //= 2
            if tile < 128:
                raise e

    s = torch.clamp(s.movedim(-3, -1), min=0, max=1.0).to(comfy.model_management.intermediate_dtype())
    return s


def upscale_bgr_face(upscale_model, bgr_face):
    """numpy uint8 BGR in -> model-upscaled numpy uint8 BGR out."""
    rgb = bgr_face[:, :, ::-1].astype(np.float32) / 255.0
    t = torch.from_numpy(rgb).unsqueeze(0)  # (1,H,W,C)
    out = upscale_image_with_model(upscale_model, t)
    out = out[0].cpu().numpy()  # (H,W,C) RGB float
    return (np.clip(out, 0.0, 1.0) * 255.0).round().astype(np.uint8)[:, :, ::-1]

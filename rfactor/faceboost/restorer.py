import os
import sys

import cv2
import numpy as np
import torch
from ..torch_utils import normalize_ as normalize

try:
    import torch.cuda as cuda
except:
    cuda = None

import comfy.utils
import folder_paths
import comfy.model_management as model_management

from ..log import logger
from .. import model_paths
from .archs.registry import ARCH_REGISTRY
from .archs import model_loading
from ..utils import (
    tensor2img,
    img2tensor,
    prepare_cropped_face,
    normalize_cropped_face,
    run_facerestore_onnx
)

from ..ort_utils import create_session, resolve_providers


FACE_RESTORE_MODEL_URLS = {
    "GFPGANv1.3.pth": "https://huggingface.co/datasets/Gourieff/ReActor/resolve/main/models/facerestore_models/GFPGANv1.3.pth",
    "GFPGANv1.4.pth": "https://huggingface.co/datasets/Gourieff/ReActor/resolve/main/models/facerestore_models/GFPGANv1.4.pth",
    "codeformer-v0.1.0.pth": "https://huggingface.co/datasets/Gourieff/ReActor/resolve/main/models/facerestore_models/codeformer-v0.1.0.pth",
    "GPEN-BFR-512.onnx": "https://huggingface.co/datasets/Gourieff/ReActor/resolve/main/models/facerestore_models/GPEN-BFR-512.onnx",
}


def ensure_facerestore_model(face_restore_model):
    """Resolve a face-restoration model name to a path, downloading on first use.

    Deliberately NOT called from INPUT_TYPES: listing node options must never
    trigger multi-hundred-MB downloads (an upstream ReActor behavior).
    """
    if face_restore_model in (None, "", "none"):
        return None
    model_path = folder_paths.get_full_path("facerestore_models", face_restore_model)
    if model_path:
        return model_path
    url = FACE_RESTORE_MODEL_URLS.get(face_restore_model)
    if url is None:
        return None
    from ..download import safe_download

    target = os.path.join(model_paths.facerestore_models_path, face_restore_model)
    try:
        safe_download(url, target, face_restore_model, min_bytes=1024 * 1024)
    except Exception as e:
        logger.error(f"Could not download {face_restore_model}: {e}")
        return None
    return target


def get_restored_face(cropped_face,
                      face_restore_model,
                      face_restore_visibility,
                      codeformer_fidelity,
                      interpolation: str = "Bicubic"):
    """face_restore_model: FACE_RESTORE_MODEL dict {"name","path"} or a plain filename."""

    if isinstance(face_restore_model, dict):
        restore_model_name = face_restore_model.get("name") or os.path.basename(face_restore_model.get("path") or "")
        model_path = face_restore_model.get("path")
        if not model_path or not os.path.exists(model_path):
            model_path = ensure_facerestore_model(restore_model_name)
    else:
        restore_model_name = face_restore_model
        model_path = ensure_facerestore_model(face_restore_model)

    if interpolation == "Bicubic":
        interpolate = cv2.INTER_CUBIC
    elif interpolation == "Bilinear":
        interpolate = cv2.INTER_LINEAR
    elif interpolation == "Nearest":
        interpolate = cv2.INTER_NEAREST
    elif interpolation == "Lanczos":
        interpolate = cv2.INTER_LANCZOS4
    
    face_size = 512
    if "1024" in restore_model_name.lower():
        face_size = 1024
    elif "2048" in restore_model_name.lower():
        face_size = 2048

    scale = face_size / cropped_face.shape[0]
    
    logger.status(f"Boosting the Face with {restore_model_name} | Face Size is set to {face_size} with Scale Factor = {scale} and '{interpolation}' interpolation")

    cropped_face = cv2.resize(cropped_face, (face_size, face_size), interpolation=interpolate)

    # For upscaling the base 128px face, I found bicubic interpolation to be the best compromise targeting antialiasing
    # and detail preservation. Nearest is predictably unusable, Linear produces too much aliasing, and Lanczos produces
    # too many hallucinations and artifacts/fringing.

    if model_path is None:
        raise FileNotFoundError(
            f"Face restoration model '{restore_model_name}' not found in models/facerestore_models "
            "and could not be fetched."
        )
    device = model_management.get_torch_device()

    cropped_face_t = img2tensor(cropped_face / 255., bgr2rgb=True, float32=True)
    normalize(cropped_face_t, (0.5, 0.5, 0.5), (0.5, 0.5, 0.5), inplace=True)
    cropped_face_t = cropped_face_t.unsqueeze(0).to(device)

    try:

        with torch.no_grad():

            if ".onnx" in restore_model_name:  # ONNX models

                ort_session = create_session(model_path, providers=resolve_providers())
                facerestore_model = ort_session
                restored_face = run_facerestore_onnx(ort_session, cropped_face)

            else:  # PTH models

                if "codeformer" in restore_model_name.lower():
                    codeformer_net = ARCH_REGISTRY.get("CodeFormer")(
                        dim_embd=512,
                        codebook_size=1024,
                        n_head=8,
                        n_layers=9,
                        connect_list=["32", "64", "128", "256"],
                    ).to(device)
                    checkpoint = torch.load(model_path, weights_only=True, map_location="cpu")["params_ema"]
                    codeformer_net.load_state_dict(checkpoint)
                    facerestore_model = codeformer_net.eval()
                else:
                    sd = comfy.utils.load_torch_file(model_path, safe_load=True)
                    facerestore_model = model_loading.load_state_dict(sd).eval()
                    facerestore_model.to(device)

                output = facerestore_model(cropped_face_t, w=codeformer_fidelity)[
                    0] if "codeformer" in restore_model_name.lower() else facerestore_model(cropped_face_t)[0]
                restored_face = tensor2img(output, rgb2bgr=True, min_max=(-1, 1))

        del facerestore_model
        torch.cuda.empty_cache()

    except Exception as error:

        print(f"\tFailed inference: {error}", file=sys.stderr)
        restored_face = tensor2img(cropped_face_t, rgb2bgr=True, min_max=(-1, 1))

    if face_restore_visibility < 1:
        restored_face = cropped_face * (1 - face_restore_visibility) + restored_face * face_restore_visibility

    restored_face = restored_face.astype("uint8")
    return restored_face, scale

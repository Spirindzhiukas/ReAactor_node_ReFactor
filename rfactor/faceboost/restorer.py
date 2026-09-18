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
    normalize_cropped_face
)

from ..ort_utils import create_session, resolve_providers


def __getattr__(name):
    if name == "providers":
        return resolve_providers()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
                      codeformer_weight,
                      interpolation: str = "Bicubic"):

    if interpolation == "Bicubic":
        interpolate = cv2.INTER_CUBIC
    elif interpolation == "Bilinear":
        interpolate = cv2.INTER_LINEAR
    elif interpolation == "Nearest":
        interpolate = cv2.INTER_NEAREST
    elif interpolation == "Lanczos":
        interpolate = cv2.INTER_LANCZOS4
    
    face_size = 512
    if "1024" in face_restore_model.lower():
        face_size = 1024
    elif "2048" in face_restore_model.lower():
        face_size = 2048

    scale = face_size / cropped_face.shape[0]
    
    logger.status(f"Boosting the Face with {face_restore_model} | Face Size is set to {face_size} with Scale Factor = {scale} and '{interpolation}' interpolation")

    cropped_face = cv2.resize(cropped_face, (face_size, face_size), interpolation=interpolate)

    # For upscaling the base 128px face, I found bicubic interpolation to be the best compromise targeting antialiasing
    # and detail preservation. Nearest is predictably unusable, Linear produces too much aliasing, and Lanczos produces
    # too many hallucinations and artifacts/fringing.

    model_path = ensure_facerestore_model(face_restore_model)
    if model_path is None:
        raise FileNotFoundError(
            f"Face restoration model '{face_restore_model}' not found in models/facerestore_models "
            "and could not be fetched."
        )
    device = model_management.get_torch_device()

    cropped_face_t = img2tensor(cropped_face / 255., bgr2rgb=True, float32=True)
    normalize(cropped_face_t, (0.5, 0.5, 0.5), (0.5, 0.5, 0.5), inplace=True)
    cropped_face_t = cropped_face_t.unsqueeze(0).to(device)

    try:

        with torch.no_grad():

            if ".onnx" in face_restore_model:  # ONNX models

                ort_session = create_session(model_path, providers=resolve_providers())
                ort_session_inputs = {}
                facerestore_model = ort_session

                for ort_session_input in ort_session.get_inputs():
                    if ort_session_input.name == "input":
                        cropped_face_prep = prepare_cropped_face(cropped_face)
                        ort_session_inputs[ort_session_input.name] = cropped_face_prep
                    if ort_session_input.name == "weight":
                        weight = np.array([1], dtype=np.double)
                        ort_session_inputs[ort_session_input.name] = weight

                output = ort_session.run(None, ort_session_inputs)[0][0]
                restored_face = normalize_cropped_face(output)

            else:  # PTH models

                if "codeformer" in face_restore_model.lower():
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

                output = facerestore_model(cropped_face_t, w=codeformer_weight)[
                    0] if "codeformer" in face_restore_model.lower() else facerestore_model(cropped_face_t)[0]
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

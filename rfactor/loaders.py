"""Dedicated model-loader nodes (the facerestore_cf pattern, generalized).

Monolithic dropdowns inside the swap node are replaced by three small loader
nodes, each with its own typed output — cleaner to maintain, cache-friendly,
and the workflow itself documents which models are in play:

- ReFactor FaceSwap Model Loader      -> FACE_SWAP_MODEL   (onnx swap engines:
    inswapper_128 / reswapper / hyperswap families; persistent cached sessions)
- ReFactor FaceRestore Model Loader   -> FACE_RESTORE_MODEL (GFPGAN / CodeFormer /
    GPEN weights; loaded per-run on Comfy's device)
- ReFactor FaceDetection Model Loader -> FACE_DETECT_MODEL (retinaface / yolov5face
    detector weights used by the face-boost alignment step)

Swap models cannot share the restore loader: they are ONNX sessions with a
completely different lifecycle (single persistent session, family routing,
emap extraction), hence the separate loader + type.
"""

import os

import folder_paths

from . import model_paths
from .faceboost import restorer
from .log import logger
from .swapper import find_swap_model_file

FACE_SWAP_MODEL = "FACE_SWAP_MODEL"
FACE_RESTORE_MODEL = "FACE_RESTORE_MODEL"
FACE_DETECT_MODEL = "FACE_DETECT_MODEL"
UPSCALE_MODEL = "UPSCALE_MODEL"  # comfy-native type: our loader is wire-compatible

DETECTION_MODELS = [
    "retinaface_resnet50",
    "retinaface_mobile0.25",
    "YOLOv5l",
    "YOLOv5n",
]

_DEFAULT_DETECTION = "retinaface_resnet50"


# The insightface folder also hosts ANALYSIS models (scrfd_*/det_*, glintr*,
# genderage, 1k3d68, 2d106det, w600k*) used internally for detection and
# embeddings. They are NOT swappers and must not be offered as one.
_SWAP_MODEL_PREFIXES = ("inswapper", "reswapper", "hyperswap")


def get_swap_model_choices():
    """(display_name, abs_path) for every swap model in the three folders."""
    choices = []
    for folder in (model_paths.insightface_path, model_paths.reswapper_path, model_paths.hyperswap_path):
        if not os.path.isdir(folder):
            continue
        for f in sorted(os.listdir(folder)):
            if not f.lower().endswith((".onnx", ".pth")):
                continue
            if folder == model_paths.insightface_path and not f.lower().startswith(_SWAP_MODEL_PREFIXES):
                continue
            choices.append((f, os.path.join(folder, f)))
    return choices


_SWAP_FAMILY_HINTS = ("inswapper", "reswapper", "hyperswap")


def get_restore_model_choices():
    """Names available in models/facerestore_models, plus canonical downloads.

    Swap-family files (inswapper/reswapper/hyperswap) that users sometimes keep
    in this folder are excluded — they are swap engines, not restorers.
    """
    names = set()
    if os.path.isdir(model_paths.facerestore_models_path):
        for f in os.listdir(model_paths.facerestore_models_path):
            if f.lower().endswith((".pth", ".onnx", ".safetensors")):
                if any(h in f.lower() for h in _SWAP_FAMILY_HINTS):
                    continue
                names.add(f)
    names.update(restorer.FACE_RESTORE_MODEL_URLS.keys())
    return sorted(names, key=str.lower)


class ReFactorFaceSwapModelLoader:
    @classmethod
    def INPUT_TYPES(s):
        names = [name for name, _ in get_swap_model_choices()]
        return {
            "required": {
                "FaceSwap_model": (["none"] + names,
                                   {"tooltip": "Swap model from models/insightface, models/reswapper or "
                                               "models/hyperswap. 'none' disables swapping (pass-through)."}),
            }
        }

    RETURN_TYPES = (FACE_SWAP_MODEL,)
    RETURN_NAMES = ("FaceSwap_model",)
    FUNCTION = "load_model"
    CATEGORY = "ANTs/loaders"

    def load_model(self, FaceSwap_model):
        if FaceSwap_model == "none":
            return (None,)
        path = find_swap_model_file(FaceSwap_model)
        if path is None:
            raise FileNotFoundError(
                f"[ReFactor] Swap model '{FaceSwap_model}' vanished from the models folders — refresh the workflow."
            )
        logger.status(f"FaceSwap model: {FaceSwap_model}")
        return ({"name": FaceSwap_model, "path": path},)


class ReFactorFaceRestoreModelLoader:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "FaceRestore_model": (["none"] + get_restore_model_choices(),
                                      {"tooltip": "Face-restoration weights from models/facerestore_models. "
                                                  "Canonical GFPGAN/CodeFormer/GPEN entries download "
                                                  "automatically when the loader executes."}),
            }
        }

    RETURN_TYPES = (FACE_RESTORE_MODEL,)
    RETURN_NAMES = ("FaceRestore_model",)
    FUNCTION = "load_model"
    CATEGORY = "ANTs/loaders"

    def load_model(self, FaceRestore_model):
        if FaceRestore_model == "none":
            return (None,)
        path = restorer.ensure_facerestore_model(FaceRestore_model)
        if path is None:
            raise FileNotFoundError(
                f"[ReFactor] Face-restore model '{FaceRestore_model}' could not be found or downloaded. "
                f"Place it into {model_paths.facerestore_models_path}"
            )
        logger.status(f"FaceRestore model: {FaceRestore_model}")
        return ({"name": FaceRestore_model, "path": path},)


class ReFactorFaceDetectionModelLoader:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "FaceDetection_model": (DETECTION_MODELS,
                                        {"tooltip": "Face detector used by the face-boost alignment "
                                                    "(weights download on first use)."}),
            }
        }

    RETURN_TYPES = (FACE_DETECT_MODEL,)
    RETURN_NAMES = ("FaceDetection_model",)
    FUNCTION = "load_model"
    CATEGORY = "ANTs/loaders"

    def load_model(self, FaceDetection_model):
        return ({"name": FaceDetection_model},)


def detection_model_name(info) -> str:
    """Convenience: extraction of the detector name with the sane default."""
    if isinstance(info, dict) and info.get("name"):
        return info["name"]
    return _DEFAULT_DETECTION


class ReFactorUpscaleModelLoader:
    """ComfyUI-native upscale-model loader (same listing and same spandrel
    loading as the core "Load Upscale Model" node; the output can even be
    wired into the stock "Upscale Image (using Model)" node)."""

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model_name": (s._choices(), {"tooltip": "Upscale models from ComfyUI's upscale_models "
                                                         "folder (ESRGAN/SPAN/SwinIR/...). Used by the "
                                                         "swap node when upRes interpolation is set to "
                                                         "'Use Upscale model'."}),
            }
        }

    @classmethod
    def _choices(cls):
        try:
            names = folder_paths.get_filename_list("upscale_models")
            return list(names) if names else []
        except Exception:
            return []

    RETURN_TYPES = (UPSCALE_MODEL,)
    RETURN_NAMES = ("UpscaleModel",)
    FUNCTION = "load_model"
    CATEGORY = "ANTs/loaders"

    def load_model(self, model_name):
        from .upscaler import load_upscale_model

        model_path = folder_paths.get_full_path("upscale_models", model_name)
        if not model_path:
            raise FileNotFoundError(
                f"Upscale model '{model_name}' not found in ComfyUI's upscale_models folder. "
                "Put the model there (or refresh) and pick it again."
            )
        logger.status(f"Loading upscale model: {model_name}")
        return (load_upscale_model(model_path),)

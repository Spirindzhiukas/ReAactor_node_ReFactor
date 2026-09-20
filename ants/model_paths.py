"""Model directory layout, ComfyUI folder registration and legacy migration.

Layout (all under ComfyUI's shared ``models/`` tree so models survive
reinstalls and are shared with sibling node packs):

    models/insightface/            inswapper_128.onnx + models/buffalo_l/*   (kept for
                                   compatibility with existing ReActor downloads)
    models/reswapper/              reswapper_128/256.onnx
    models/hyperswap/              hyperswap_1x_256.onnx
    models/facerestore_models/     GFPGAN / CodeFormer (.pth/.onnx)
    models/facedetection/          retinaface / yolov5face detector weights (face boost)
    models/reactor/faces/          saved face models (.safetensors)  [legacy location]
    models/reactor_refactor/faces/ saved face models (.safetensors)  [ours]
    models/DLSS/dlssnr_<version>/  user-supplied DLSS NR DLL sets (3rd-party, manual;
                                   any .dll filenames accepted)
"""

import os

import folder_paths

from .utils import add_folder_path_and_extensions, move_path

models_path = folder_paths.models_dir

# swap models ---------------------------------------------------------------
insightface_path = os.path.join(models_path, "insightface")
insightface_models_path = os.path.join(insightface_path, "models")
reswapper_path = os.path.join(models_path, "reswapper")
hyperswap_path = os.path.join(models_path, "hyperswap")

# face boost ----------------------------------------------------------------
facerestore_models_path = os.path.join(models_path, "facerestore_models")
facedetection_path = os.path.join(models_path, "facedetection")

# saved faces ---------------------------------------------------------------
REACTOR_MODELS_PATH = os.path.join(models_path, "reactor_refactor")
FACE_MODELS_PATH = os.path.join(REACTOR_MODELS_PATH, "faces")
FACE_MODELS_PATH_LEGACY = os.path.join(models_path, "reactor", "faces")

# DLSS NR dll sets ----------------------------------------------------------
# Owner decision: DLLs are models -> they live under models/DLSS/ with one
# folder per version (dlssnr_<name>); ANY .dll filenames inside are accepted.
DLSS_MODELS_PATH = os.path.join(models_path, "DLSS")
DLSSNR_MODELS_PATH = os.path.join(models_path, "dlssnr")  # legacy fallback


def register_folders() -> None:
    """Create dirs and register folder_paths entries. Called once at nodepack import."""
    for path in (
        insightface_path,
        reswapper_path,
        hyperswap_path,
        facerestore_models_path,
        facedetection_path,
        FACE_MODELS_PATH,
        DLSS_MODELS_PATH,
        DLSSNR_MODELS_PATH,
    ):
        os.makedirs(path, exist_ok=True)

    add_folder_path_and_extensions(
        "facerestore_models", [facerestore_models_path], folder_paths.supported_pt_extensions
    )
    # CodeFormer/GFPGAN onnx builds are also accepted in the same folder.
    add_folder_path_and_extensions("facerestore_models", [facerestore_models_path], {".onnx"})
    add_folder_path_and_extensions("facedetection", [facedetection_path], folder_paths.supported_pt_extensions)

    _migrate_legacy_dirs()


def face_models_dirs():
    """All dirs scanned for saved face models (ours first, then upstream's)."""
    return [FACE_MODELS_PATH, FACE_MODELS_PATH_LEGACY]


def _migrate_legacy_dirs() -> None:
    """Upstream kept a private models/ dir inside the node folder; relocate once."""
    node_pkg_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    models_path_old = os.path.join(node_pkg_dir, "models")
    insightface_path_old = os.path.join(models_path_old, "insightface")
    insightface_models_path_old = os.path.join(insightface_path_old, "models")

    if os.path.exists(models_path_old):
        if os.path.exists(insightface_models_path_old):
            move_path(insightface_models_path_old, insightface_models_path)
        if os.path.exists(insightface_path_old):
            move_path(insightface_path_old, insightface_path)
        move_path(models_path_old, models_path)
        if os.path.exists(models_path_old):
            try:
                import shutil

                shutil.rmtree(models_path_old, ignore_errors=True)
            except Exception:
                pass

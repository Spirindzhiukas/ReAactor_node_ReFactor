"""Image/tensor conversion helpers and face-model (safetensors) storage.

Ported from the upstream ``reactor_utils.py`` with the download logic moved to
``download.py``, logging to ``log.py``, and ORT session handling to ``ort_utils``.
"""

import hashlib
import os

import cv2
import numpy as np
import torch
from PIL import Image
from safetensors.torch import save_file, safe_open

from .engine.face_objects import Face
from .torch_utils import make_grid

# ---------------------------------------------------------------- conversions


def tensor_to_pil(img_tensor, batch_index=0) -> Image.Image:
    img_tensor = img_tensor[batch_index].unsqueeze(0)
    i = 255.0 * img_tensor.cpu().numpy()
    return Image.fromarray(np.clip(i, 0, 255).astype(np.uint8).squeeze())


def batch_tensor_to_pil(img_tensor):
    return [tensor_to_pil(img_tensor, i) for i in range(img_tensor.shape[0])]


def pil_to_tensor(image):
    image = np.array(image).astype(np.float32) / 255.0
    image = torch.from_numpy(image).unsqueeze(0)
    if len(image.shape) == 3:
        image = image.unsqueeze(-1)
    return image


def batched_pil_to_tensor(images):
    return torch.cat([pil_to_tensor(image) for image in images], dim=0)


def img2tensor(imgs, bgr2rgb=True, float32=True):
    def _totensor(img, bgr2rgb, float32):
        if img.shape[2] == 3 and bgr2rgb:
            if img.dtype == "float64":
                img = img.astype("float32")
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = torch.from_numpy(img.transpose(2, 0, 1))
        if float32:
            img = img.float()
        return img

    if isinstance(imgs, list):
        return [_totensor(img, bgr2rgb, float32) for img in imgs]
    return _totensor(imgs, bgr2rgb, float32)


def tensor2img(tensor, rgb2bgr=True, out_type=np.uint8, min_max=(0, 1)):
    if not (torch.is_tensor(tensor) or (isinstance(tensor, list) and all(torch.is_tensor(t) for t in tensor))):
        raise TypeError(f"tensor or list of tensors expected, got {type(tensor)}")

    if torch.is_tensor(tensor):
        tensor = [tensor]
    result = []
    for _tensor in tensor:
        _tensor = _tensor.squeeze(0).float().detach().cpu().clamp_(*min_max)
        _tensor = (_tensor - min_max[0]) / (min_max[1] - min_max[0])

        n_dim = _tensor.dim()
        if n_dim == 4:
            img_np = make_grid(_tensor, nrow=int(np.sqrt(_tensor.size(0)))).numpy()
            img_np = img_np.transpose(1, 2, 0)
            if rgb2bgr:
                img_np = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        elif n_dim == 3:
            img_np = _tensor.numpy()
            img_np = img_np.transpose(1, 2, 0)
            if img_np.shape[2] == 1:
                img_np = np.squeeze(img_np, axis=2)
            elif rgb2bgr:
                img_np = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        elif n_dim == 2:
            img_np = _tensor.numpy()
        else:
            raise TypeError("Only support 4D, 3D or 2D tensor. " f"But received with dimension: {n_dim}")
        if out_type == np.uint8:
            img_np = (img_np * 255.0).round()
        result.append(img_np.astype(out_type))
    return result[0] if len(result) == 1 else result


def rgba2rgb_tensor(rgba):
    r, g, b = rgba[..., 0], rgba[..., 1], rgba[..., 2]
    return torch.stack([r, g, b], dim=3)


def get_image_md5hash(image: Image.Image):
    return hashlib.md5(image.tobytes()).hexdigest()


def prepare_cropped_face(cropped_face):
    cropped_face = cropped_face[:, :, ::-1] / 255.0
    cropped_face = (cropped_face - 0.5) / 0.5
    return np.expand_dims(cropped_face.transpose(2, 0, 1), axis=0).astype(np.float32)


def normalize_cropped_face(cropped_face):
    cropped_face = np.clip(cropped_face, -1, 1)
    cropped_face = (cropped_face + 1) / 2
    cropped_face = cropped_face.transpose(1, 2, 0)
    cropped_face = (cropped_face * 255.0).round()
    return cropped_face.astype(np.uint8)[:, :, ::-1]


# ------------------------------------------------------------- face models


def save_face_model(face: Face, filename: str) -> None:
    try:
        tensors = {
            "bbox": torch.tensor(face["bbox"]),
            "kps": torch.tensor(face["kps"]),
            "det_score": torch.tensor(face["det_score"]),
            "landmark_3d_68": torch.tensor(face["landmark_3d_68"]),
            "pose": torch.tensor(face["pose"]),
            "landmark_2d_106": torch.tensor(face["landmark_2d_106"]),
            "embedding": torch.tensor(face["embedding"]),
            "gender": torch.tensor(face["gender"]),
            "age": torch.tensor(face["age"]),
        }
        save_file(tensors, filename)
        print(f"Face model has been saved to '{filename}'")
    except Exception as e:
        print(f"Error: {e}")


def load_face_model(filename: str):
    face = {}
    with safe_open(filename, framework="pt") as f:
        for k in f.keys():
            face[k] = f.get_tensor(k).numpy()
    return Face(face)


# ------------------------------------------------------------- progress bar


def progress_bar(total):
    from comfy.utils import ProgressBar

    return ProgressBar(total)


def progress_bar_reset(pbar):
    pbar.current = 0
    pbar.update(0)


# ------------------------------------------------------------------- misc


def move_path(old_path, new_path):
    """Legacy-layout migration helper: moves files from old custom_nodes/models dir."""
    if os.path.exists(old_path):
        try:
            for model in os.listdir(old_path):
                os.rename(os.path.join(old_path, model), os.path.join(new_path, model))
            os.rmdir(old_path)
        except Exception as e:
            print(f"Error: {e}")


def add_folder_path_and_extensions(folder_name, full_folder_paths, extensions):
    """Register model folders with ComfyUI without clobbering existing entries."""
    import folder_paths

    for full_folder_path in full_folder_paths:
        folder_paths.add_model_folder_path(folder_name, full_folder_path)

    if folder_name in folder_paths.folder_names_and_paths:
        current_paths, current_extensions = folder_paths.folder_names_and_paths[folder_name]
        folder_paths.folder_names_and_paths[folder_name] = (
            current_paths,
            current_extensions | extensions,
        )
    else:
        folder_paths.folder_names_and_paths[folder_name] = (full_folder_paths, extensions)

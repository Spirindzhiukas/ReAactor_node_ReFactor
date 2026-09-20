"""ANTs DLSS SR Upscaler — regular DLSS SR via the pure-Python NGX host.

Render at the input resolution, let DLSS upscale by the mode's fixed ratio
(DLAA 1x / Quality 1.5x / Balanced ~1.72x / Performance 2x / Ultra
Performance 3x), then resize the output BACK to the input resolution with a
pixel interpolation of choice — the owner's SR-then-downsample detail pass.
The J/K/L/M presets are the real NGX parameters (which model a letter
selects belongs to the installed nvngx_dlss.dll build).
"""

import numpy as np
import torch

import comfy.model_management
import comfy.utils

from ..log import dlss_logger as logger
from ..scripting import state
from ..utils import progress_bar, progress_bar_reset
from . import discovery
from .errors import DlssSrError
from .sr import ARTIST_PRESETS, PERF_RATIO, DlssSrSession

_MODES = ["DLAA (1x)", "Quality (1.5x)", "Balanced (1.724x)",
          "Performance (2x)", "Ultra Performance (3x)"]
_MODE_KEY = {
    "DLAA (1x)": "DLAA", "Quality (1.5x)": "Quality", "Balanced (1.724x)": "Balanced",
    "Performance (2x)": "Performance", "Ultra Performance (3x)": "Ultra Performance",
}
_INTERP = ["lanczos", "bicubic", "bilinear", "nearest-exact"]


class DLSSSRUpscaler:
    @classmethod
    def INPUT_TYPES(cls):
        preset_labels = [f"{letter} - {name}" for letter, name in ARTIST_PRESETS]
        return {
            "required": {
                "image": ("IMAGE",),
                "sr_model": (discovery.sr_set_choices(),
                             {"tooltip": "Which nvngx_dlss*.dll build to use (models/DLSS/SR/ - "
                                         "each flat .dll is its own set). 'auto' picks the first; "
                                         "'refresh' re-scans."}),
                "sr_mode": (_MODES, {"default": "Performance (2x)",
                                     "tooltip": "The DLSS mode; its fixed ratio sets the internal "
                                                "upscale size. The result is always resized back to "
                                                "the input resolution."}),
                "sr_preset": (preset_labels, {"default": preset_labels[0],
                                              "tooltip": "Model preset hint (J/K/L/M). Which weights a "
                                                         "letter maps to is a property of the installed "
                                                         "nvngx_dlss.dll build."}),
                "return_interpolation": (_INTERP, {"default": "lanczos",
                                                   "tooltip": "Interpolation for resizing the SR output "
                                                              "back to the input resolution."}),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "upscale"
    CATEGORY = "ANTs"

    DESCRIPTION = (
        "DLSS Super Resolution through our own pure-Python NGX host: DLAA/\n"
        "Quality/Balanced/Performance/Ultra Performance + model presets, with\n"
        "the output always resized back to the input resolution. Still-image\n"
        "feeding uses zeroed motion vectors (temporal accumulation is limited\n"
        "compared to a game). Requires: Windows, RTX GPU, NVIDIA driver (NGX\n"
        "core), and your nvngx_dlss*.dll in ComfyUI/models/DLSS/SR/."
    )

    def __init__(self):
        self.gpu = None
        self.session = None
        self._session_key = None

    def upscale(self, image, sr_model, sr_mode, sr_preset, return_interpolation):
        if sr_model == "refresh":
            discovery.sr_set_choices()  # re-scan; user re-selects

        mode_key = _MODE_KEY[sr_mode]
        preset_letter = sr_preset.split(" - ")[0]
        dll_path = discovery.resolve_sr_dll(sr_model)
        height, width = int(image.shape[1]), int(image.shape[2])
        ratio = PERF_RATIO[mode_key]
        out_w = max(8, int(round(width * ratio)))
        out_h = max(8, int(round(height * ratio)))
        key = (dll_path, mode_key, preset_letter, width, height, out_w, out_h)

        ordinal = 0
        device = comfy.model_management.get_torch_device()
        if getattr(device, "index", None) is not None:
            ordinal = device.index
        if self.gpu is None:
            from .d3d12 import D3D12Device, GpuContext
            gpu_device = D3D12Device.create()
            self.gpu = GpuContext(gpu_device, adapter_index=ordinal)

        if self.session is None or self._session_key != key:
            if self.session is not None:
                try:
                    self.session.close()
                except Exception:
                    pass
                self.session = None
            logger.status(f"DLSS SR: {mode_key} (internal {width}x{height} -> {out_w}x{out_h}), "
                          f"preset {preset_letter}, dll {dll_path}")
            self.session = DlssSrSession(
                self.gpu, width, height, out_w, out_h, mode=mode_key,
                preset=preset_letter, sr_dll_dir=discovery.stage_sr_dll(dll_path))
            self._session_key = key

        results = []
        pbar = progress_bar(len(image))
        try:
            for i in range(len(image)):
                if state.interrupted or comfy.model_management.processing_interrupted():
                    logger.status("Interrupted by User")
                    break
                frame = image[i].cpu().numpy()
                rgba = _frame_to_rgba8(frame)
                out = self.session.evaluate(rgba, reset=True)
                tensor = _rgba8_to_frame(out, out_w, out_h).to(device)
                if tuple(tensor.shape[:2]) != (height, width):
                    tensor = comfy.utils.common_upscale(
                        tensor.movedim(-1, 1), width, height,
                        return_interpolation, "disabled").movedim(1, -1)
                results.append(tensor.clamp(0.0, 1.0))
                pbar.update(1)
        except DlssSrError as exc:
            raise RuntimeError(str(exc))
        finally:
            progress_bar_reset(pbar)

        if not results:
            raise RuntimeError("[ANTs] DLSS SR produced no frames (interrupted?).")
        return (torch.stack(results),)


def _frame_to_rgba8(frame):
    """float32 [0,1] HWC RGB -> packed RGBA8 bytes."""
    rgb8 = (np.clip(frame, 0.0, 1.0) * 255.0).round().astype(np.uint8)
    h, w = rgb8.shape[0], rgb8.shape[1]
    rgba = np.empty((h, w, 4), dtype=np.uint8)
    rgba[:, :, :3] = rgb8
    rgba[:, :, 3] = 255
    return rgba.tobytes()


def _rgba8_to_frame(payload, width, height):
    """Packed RGBA8 bytes -> float32 [0,1] HWC RGB torch tensor (CPU)."""
    arr = np.frombuffer(payload, dtype=np.uint8).reshape(height, width, 4)
    return torch.from_numpy(arr[:, :, :3].astype(np.float32) / 255.0)

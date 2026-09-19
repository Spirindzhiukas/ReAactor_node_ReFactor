"""DLSS Neural Rendering (feature 18) — the DLSS5 node's engine, pure Python.

Snippet-direct route: `nvngx_dlssnr.dll` exports the whole NGX D3D12 API
except the parameter allocator, so we load it as the provider and hand it
our own parameter object (``parameters.OwnParameterObject``). Its look
controls are plain NGX parameters (``DLSSNR.*``), which means FULL control
from our side — no helper DLL ABI in between.

The neuroframe helper pair this replaces is documented in the package
docstring: Merserk's repository remains the designated learning source for
future NR-integration capabilities.

Parameter mapping (v6-helper control -> raw runtime parameter):
  style -> DLSSNR.Style (u32)            intensity -> DLSSNR.Intensity (f32)
  local_tone -> ..LocalToneStrength      local_structure -> ..LocalStructureStrength
  skin_structure -> ..SkinStructureStrength
  tone_preservation -> ..GlobalToneStrength
  color_strength / face_skin_protection / grain_preservation /
  shimmer_suppression -> best-effort extras (inert if this runtime build
  has no such parameter — unset/unknown parameters are simply never read).
"""

from . import d3d12 as d3d
from .errors import DlssSrError
from .ngx import FEATURE_NR, NGX_MODELS_DIR, NgxSession

_STYLE_TO_INT = {"Default": 0, "Natural": 1, "Cinematic": 2}
NR_INT_TO_STYLE = {v: k for k, v in _STYLE_TO_INT.items()}


class DlssNrSession:
    """One created NR feature (1:1 enhancement); evaluate() runs one frame."""

    def __init__(self, gpu, width, height, dll_path, style="Default",
                 intensity=1.0, local_tone=0.0, local_structure=1.0,
                 skin_structure=0.5, color_strength=0.5, tone_preservation=0.5,
                 face_skin_protection=0.0, grain_preservation=0.0,
                 auto_mask=False, nr_preset=0, app_data_path=None):
        self.w, self.h = int(width), int(height)
        self.settings = {
            "style": _STYLE_TO_INT.get(style, 0),
            "intensity": float(intensity),
            "local_tone": float(local_tone),
            "local_structure": float(local_structure),
            "skin_structure": float(skin_structure),
            "color_strength": float(color_strength),
            "tone_preservation": float(tone_preservation),
            "face_skin_protection": float(face_skin_protection),
            "grain_preservation": float(grain_preservation),
            "auto_mask": bool(auto_mask),
        }
        self.ngx = NgxSession(
            gpu, dll_path, search_paths=[os.path.dirname(dll_path), NGX_MODELS_DIR],
            use_own_parameters=True, app_data_path=app_data_path)

        p = self.ngx.params
        p.set_u32("DLSSNR.Width", self.w)      # DLSSNR.* only: generic Width/Height do not exist
        p.set_u32("DLSSNR.Height", self.h)
        p.set_u32("PerfQualityValue", 5)       # DLAA (1:1)
        p.set_u32("CreationNodeMask", 1)
        p.set_u32("VisibilityNodeMask", 1)
        p.set_u32("DLSSNR.Enabled", 1)
        p.set_i32("DLSSNR.Hint.Render.Preset", int(nr_preset))

        self.ngx.create_feature(FEATURE_NR)

        dev = gpu.device
        self.color = dev.create_texture2d(self.w, self.h, d3d.DXGI_FORMAT_R8G8B8A8_UNORM,
                                          label="nr color")
        self.output = dev.create_texture2d(self.w, self.h, d3d.DXGI_FORMAT_R8G8B8A8_UNORM,
                                           label="nr output")

    def _set_eval_params(self, reset):
        p = self.ngx.params
        s = self.settings
        p.set_resource("DLSSNR.Color", self.color.ptr)
        p.set_resource("DLSSNR.Output", self.output.ptr)
        p.set_u32("DLSSNR.Reset", 1 if reset else 0)
        p.set_f32("DLSSNR.MVecScaleX", 0.0)    # feature 18 consumes no motion vectors
        p.set_f32("DLSSNR.MVecScaleY", 0.0)
        p.set_f32("DLSSNR.Intensity", s["intensity"])
        p.set_f32("DLSSNR.LocalToneStrength", s["local_tone"])
        p.set_f32("DLSSNR.LocalStructureStrength", s["local_structure"])
        p.set_f32("DLSSNR.SkinStructureStrength", s["skin_structure"])
        p.set_u32("DLSSNR.Style", s["style"])
        p.set_u32("DLSSNR.UseAutoMask", 1 if s["auto_mask"] else 0)
        # Best-effort extras (inert when this build lacks the parameter):
        p.set_f32("DLSSNR.GlobalToneStrength", s["tone_preservation"])
        p.set_f32("DLSSNR.ColorStrength", s["color_strength"])
        p.set_f32("DLSSNR.FaceSkinProtection", s["face_skin_protection"])
        p.set_f32("DLSSNR.GrainPreservation", s["grain_preservation"])

    def evaluate(self, color_rgba, reset=True):
        """Enhance one RGBA8 frame 1:1; returns the enhanced RGBA8 frame."""
        expected = self.w * self.h * 4
        if len(color_rgba) != expected:
            raise DlssSrError(
                f"[ANTs] DLSS NR expected {expected} color bytes, got {len(color_rgba)}.")
        uav = d3d.D3D12_RESOURCE_STATE_UNORDERED_ACCESS
        self.gpu.upload_texture(self.color, color_rgba, uav)
        self.gpu.transition(self.output, uav)
        self._set_eval_params(reset)
        self.ngx.evaluate()
        return self.gpu.readback_texture(self.output, uav)

    def close(self):
        self.ngx.close()


import os  # noqa: E402  (kept last to keep the mapping table above readable)

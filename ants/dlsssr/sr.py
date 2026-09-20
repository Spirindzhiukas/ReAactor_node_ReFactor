"""DLSS Super Resolution (feature 1) — the regular upscaler, pure Python.

Driver-core route: the core owns `nvngx_dlss.dll` (found via the
FeatureCommonInfo path list pointing at the chosen SR set folder) and the
parameter allocator. Modes map to fixed ratios (per the public SDK:
DLAA 1.0, Quality 1.5, Balanced ~1.724, Performance 2.0, Ultra Performance
3.0); the model presets (J/K/L/M and friends) are per-mode NGX parameters —
which model a letter selects is a property of the installed
nvngx_dlss.dll.

Stills use zeroed motion vectors with ``MV.Scale = 0`` + ``Reset`` (the
DVT-proven pattern). The caller gets the OUTPUT-resolution RGBA bytes and
resizes back to the input resolution on the torch side.
"""

import os

from . import d3d12 as d3d
from .errors import DlssSrError
from .ngx import FEATURE_SR, NgxSession, locate_ngx_core

# NVSDK_NGX_PerfQuality_Value (public SDK enum; UltraQuality is unavailable
# in current nvngx_dlss.dll builds - CreateFeature rejects it)
PERF_QUALITY = {
    "Ultra Performance": 3,
    "Performance": 0,
    "Balanced": 1,
    "Quality": 2,
    "DLAA": 5,
}
PERF_RATIO = {3: 3.0, 0: 2.0, 1: 1.7241379, 2: 1.5, 5: 1.0}

# The per-mode preset parameter (which model a letter selects belongs to the
# loaded nvngx_dlss.dll). Values: NVSDK_NGX_DLSS_Hint_Render_Preset.
DLSS_RENDER_PRESETS = {"Default": 0, "A": 1, "B": 2, "C": 3, "D": 4, "E": 5,
                       "F": 6, "J": 10, "K": 11, "L": 12, "M": 13}

# Artist names for the preset letters (community lore, owner-approved)
ARTIST_PRESETS = [
    ("Default", "Default"),
    ("J", "Transformer I - Crisp (sharpest, a bit more flicker)"),
    ("K", "Transformer I - Stable (DLSS 4 Latest)"),
    ("L", "Transformer II - Quality (heavy, Ultra-Perf default)"),
    ("M", "Transformer II - Fast (Performance default)"),
]

DLSS_CREATE_FLAG_IS_HDR = 0x01
DLSS_CREATE_FLAG_MVLOW_RES = 0x02
DLSS_CREATE_FLAG_AUTO_EXPOSURE = 0x40

# Per-quality preset parameter names (verified against nvngx_dlss.dll)
_PRESET_PARAM = {
    0: "DLSS.Hint.Render.Preset.Performance",
    1: "DLSS.Hint.Render.Preset.Balanced",
    2: "DLSS.Hint.Render.Preset.Quality",
    3: "DLSS.Hint.Render.Preset.UltraPerformance",
    5: "DLSS.Hint.Render.Preset.DLAA",
}


class DlssSrSession:
    """One created SR feature; evaluate() runs one frame."""

    def __init__(self, gpu, render_width, render_height, output_width, output_height,
                 mode="Performance", preset="Default", hdr=False, sr_dll_dir=None,
                 app_data_path=None):
        if mode not in PERF_QUALITY:
            raise DlssSrError(f"[ANTs] Unknown SR mode {mode!r}. Known: {sorted(PERF_QUALITY)}.")
        if preset not in DLSS_RENDER_PRESETS:
            raise DlssSrError(f"[ANTs] Unknown SR preset {preset!r}. Known: {sorted(DLSS_RENDER_PRESETS)}.")
        self.gpu = gpu
        self.rw, self.rh = int(render_width), int(render_height)
        self.ow, self.oh = int(output_width), int(output_height)
        quality = PERF_QUALITY[mode]

        from .ngx import NGX_MODELS_DIR
        core_path = locate_ngx_core()
        # Stage dir FIRST (owner's chosen build), then NVIDIA's own managed
        # models dir - the stock core prefers its properly-installed runtime.
        search = [d for d in (sr_dll_dir, NGX_MODELS_DIR) if d]
        self.ngx = NgxSession(
            gpu, core_path, search_paths=search, app_data_path=app_data_path)
        self._apply_create_params(quality, hdr, preset)
        try:
            self.ngx.create_feature(FEATURE_SR)
        except DlssSrError as core_err:
            if "0xBAD0000B" not in str(core_err):
                raise
            # FeatureNotSupported from the DRIVER CORE: most often the core
            # refusing to load a snippet outside its managed models root.
            # Fallback: snippet-direct - load nvngx_dlss.dll as the provider
            # with OUR parameter object (same route as the NR host).
            from ..log import dlss_logger as logger
            logger.status("DLSS SR: driver core rejected feature 1 (FeatureNotSupported) "
                          "- retrying snippet-direct with our own parameter object.")
            self.ngx.close()
            snippet = os.path.join(sr_dll_dir, "nvngx_dlss.dll")
            if not os.path.isfile(snippet):
                raise core_err from None
            self.ngx = NgxSession(
                gpu, snippet, search_paths=[sr_dll_dir],
                use_own_parameters=True, app_data_path=app_data_path)
            self._apply_create_params(quality, hdr, preset)
            self.ngx.create_feature(FEATURE_SR)

        dev = gpu.device
        self.color = dev.create_texture2d(self.rw, self.rh, d3d.DXGI_FORMAT_R8G8B8A8_UNORM,
                                          label="sr color")
        self.output = dev.create_texture2d(self.ow, self.oh, d3d.DXGI_FORMAT_R8G8B8A8_UNORM,
                                           label="sr output")
        self.depth = dev.create_texture2d(self.rw, self.rh, d3d.DXGI_FORMAT_R32_FLOAT,
                                          label="sr depth")
        self.motion = dev.create_texture2d(self.rw, self.rh, d3d.DXGI_FORMAT_R16G16_FLOAT,
                                           label="sr motion")
        # Depth + motion are unused for stills: zero once. Both are INPUTS, so
        # they are left in the shader-resource state NGX expects (see
        # d3d12.input_state) - the same contract as the NR path.
        zeros = bytes(self.rw * self.rh * 4)
        input_state = d3d.input_state()
        self.gpu.upload_texture(self.depth, zeros, input_state)
        self.gpu.upload_texture(self.motion, zeros, input_state)
        self.gpu.submit_and_wait()

    def _apply_create_params(self, quality, hdr, preset):
        p = self.ngx.params
        p.set_u32("PerfQualityValue", quality)
        p.set_u32("Width", self.rw)
        p.set_u32("Height", self.rh)
        p.set_u32("OutWidth", self.ow)
        p.set_u32("OutHeight", self.oh)
        flags = DLSS_CREATE_FLAG_AUTO_EXPOSURE | DLSS_CREATE_FLAG_MVLOW_RES
        if hdr:
            flags |= DLSS_CREATE_FLAG_IS_HDR
        p.set_u32("DLSS.Feature.Create.Flags", flags)
        p.set_u32(_PRESET_PARAM.get(quality, _PRESET_PARAM[0]), DLSS_RENDER_PRESETS[preset])
        p.set_u32("CreationNodeMask", 1)
        p.set_u32("VisibilityNodeMask", 1)

    def evaluate(self, color_rgba, reset=True):
        """Upscale one render-size RGBA8 frame; returns the output-size frame."""
        expected = self.rw * self.rh * 4
        if len(color_rgba) != expected:
            raise DlssSrError(
                f"[ANTs] DLSS SR expected {expected} color bytes, got {len(color_rgba)}.")
        uav = d3d.D3D12_RESOURCE_STATE_UNORDERED_ACCESS
        self.gpu.upload_texture(self.color, color_rgba, d3d.input_state())
        self.gpu.transition(self.output, uav)
        p = self.ngx.params
        p.set_resource("Color", self.color.ptr)
        p.set_resource("Output", self.output.ptr)
        p.set_resource("Depth", self.depth.ptr)
        p.set_resource("MotionVectors", self.motion.ptr)
        p.set_f32("MV.Scale.X", 0.0)
        p.set_f32("MV.Scale.Y", 0.0)
        p.set_f32("Jitter.Offset.X", 0.0)
        p.set_f32("Jitter.Offset.Y", 0.0)
        p.set_u32("Reset", 1 if reset else 0)
        p.set_u32("DLSS.Render.Subrect.Dimensions.Width", self.rw)
        p.set_u32("DLSS.Render.Subrect.Dimensions.Height", self.rh)
        self.ngx.evaluate()
        return self.gpu.readback_texture(self.output, uav)

    def close(self):
        self.ngx.close()

"""DLSS Super Resolution (feature 1) — the regular upscaler, pure Python.

Session routes (rig 2026-09-21, run 29 — the full story in
``_open_first_working``):

1. **the staged runtime as the app-facing module** (``nvngx_dlss.dll``): its
   ``NVSDK_NGX_D3D12_*`` exports ARE the public API in the PUBLIC argument
   order, and the runtime loads the driver core itself. This is the route
   every DLSS application uses, and the only one that creates feature 1;
2. **the driver core alone**, with the SR folder in the search-path list
   (the previous primary);
3. **snippet-direct** (opt-in, ``ANTS_SR_SNIPPET_DIRECT=1``) for NR-style
   builds whose ``Init_Ext`` takes the SWAPPED argument order.

Rig 02:48 (fresh build, no crash) failed BOTH working routes because of ONE
process-wide fact: the NR stage had already inited the driver core in prompt
1, and NGX keeps the FIRST init's app id and feature-library search paths.
Route 1 inited with a different app id -> ``0xBAD00002 PlatformError``; route
2 landed in the old context, whose paths contained no ``nvngx_dlss.dll`` ->
``CreateFeature(1)`` answered ``0xBAD0000B`` = **UnableToInitializeFeature**
(Fail|11 - not "FeatureNotSupported", which is Fail|1). Both stages now init
with the same app id / project id / app data path and the same union search
paths (``ngx.feature_search_paths``), so whichever stage runs first, the other
one's library is already on the list.

Modes map to fixed ratios (per the public SDK: DLAA 1.0, Quality 1.5,
Balanced ~1.724, Performance 2.0, Ultra Performance 3.0); the model presets
(J/K/L/M and friends) are per-mode NGX parameters — which model a letter
selects is a property of the installed nvngx_dlss.dll.

Stills use zeroed motion vectors with ``MV.Scale = 0`` + ``Reset`` (the
DVT-proven pattern). The caller gets the OUTPUT-resolution RGBA bytes and
resizes back to the input resolution on the torch side.
"""

import os

from . import d3d12 as d3d
from .errors import DlssSrError
from .ngx import (FEATURE_SR, NR_APP_ID, NR_PROJECT_ID, NgxSession,
                  locate_ngx_core)


def sr_app_id():
    """The app id every ANTs NGX session inits with (rig 02:48).

    NGX keeps the FIRST Init's context for the whole process; a second stage
    that inits with a different app id is refused (route 1 answered
    0xBAD00002 = PlatformError) or lands in a context whose search paths never
    saw its feature library (route 2 answered 0xBAD0000B =
    UnableToInitializeFeature). One app id, one project id, one app data path,
    one search-path list - so the SR stage inits exactly like the NR stage.
    ``ANTS_SR_APP_ID`` (int, 0x allowed) restores an A/B against another
    identity without a code change.
    """
    raw = os.environ.get("ANTS_SR_APP_ID", "").strip()
    if raw:
        try:
            return int(raw, 0)
        except ValueError:
            from ..log import dlss_logger
            dlss_logger.warning(
                "[ANTs] ANTS_SR_APP_ID='%s' is not an integer - using the "
                "shared ANTs app id %d.", raw, NR_APP_ID)
    return NR_APP_ID

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


# The literal file name the staging dir holds: ants/dlssnr/discovery.py's
# ``stage_sr_dll`` canonicalises the CHOSEN build to it, whatever the source
# file was called (the file NAME is never the identity - the exports are).
_SR_DLL_NAME = "nvngx_dlss.dll"

# The public NGX API a DLSS SR runtime must export. Probed, never loaded, so
# the log says what the file is BEFORE any call goes into it.
_PUBLIC_ENTRYPOINTS = ("NVSDK_NGX_D3D12_Init_Ext",
                       "NVSDK_NGX_D3D12_CreateFeature",
                       "NVSDK_NGX_D3D12_EvaluateFeature")


def _log():
    from ..log import dlss_logger
    return dlss_logger


def sr_runtime_path(sr_dll_dir):
    """The staged ``nvngx_dlss.dll`` inside a staging/search dir (or None)."""
    if not sr_dll_dir:
        return None
    candidate = os.path.join(sr_dll_dir, _SR_DLL_NAME)
    return candidate if os.path.isfile(candidate) else None


def describe_runtime(path):
    """What the file claims to be, read from its export table (no load)."""
    try:
        from ..dlssnr.peexports import export_names
        names = export_names(path)
    except Exception as exc:            # pragma: no cover - defensive
        return f"export probe unavailable ({exc})"
    if not names:
        return "export probe: no readable export table (not a PE?)"
    missing = [n for n in _PUBLIC_ENTRYPOINTS if n not in names]
    if missing:
        return "export probe: MISSING " + ", ".join(missing)
    return "exports the public NGX API (Init_Ext/CreateFeature/EvaluateFeature)"


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

        core_path = locate_ngx_core()
        # The session unions the staged feature dirs and NVIDIA's own managed
        # models dir onto this list (ngx.feature_search_paths) - the core keeps
        # the FIRST init's paths for the whole process, so the SR folder alone
        # is not enough when the NR stage inited first (rig 02:48).
        search = [sr_dll_dir] if sr_dll_dir else []
        self.ngx = self._open_first_working(core_path, search, sr_dll_dir,
                                            app_data_path, quality, hdr, preset)

        dev = gpu.device
        self.color = dev.create_input_texture2d(self.rw, self.rh,
                                                d3d.DXGI_FORMAT_R8G8B8A8_UNORM,
                                                label="sr color")
        self.output = dev.create_texture2d(self.ow, self.oh, d3d.DXGI_FORMAT_R8G8B8A8_UNORM,
                                           label="sr output")
        self.depth = dev.create_input_texture2d(self.rw, self.rh,
                                                d3d.DXGI_FORMAT_R32_FLOAT,
                                                label="sr depth")
        self.motion = dev.create_input_texture2d(self.rw, self.rh,
                                                 d3d.DXGI_FORMAT_R16G16_FLOAT,
                                                 label="sr motion")
        # Depth + motion are unused for stills: zero once. Both are INPUTS, so
        # they are left in the shader-resource state NGX expects (see
        # d3d12.input_state) - the same contract as the NR path.
        zeros = bytes(self.rw * self.rh * 4)
        input_state = d3d.input_state()
        self.gpu.upload_texture(self.depth, zeros, input_state)
        self.gpu.upload_texture(self.motion, zeros, input_state)
        self.gpu.submit_and_wait()

    # ------------------------------------------------------------ session
    def _open_first_working(self, core_path, search, sr_dll_dir, app_data_path,
                            quality, hdr, preset):
        """Start the SR feature on the first route this build accepts.

        Rig 29 (2026-09-21, owner) decided the order:

        * route 2 below was the old primary and returned ``0xBAD0000B`` =
          ``Fail|11 UnableToInitializeFeature``: the feature is not available
          in the NGX context the core already holds (rig 02:48: that context
          was inited by the NR stage in an earlier prompt, and its
          feature-library search paths held no ``nvngx_dlss.dll`` at all);
        * the old fallback was route 3's SWAPPED ``Init_Ext`` layout against
          the SDK runtime, and it faulted INSIDE the runtime: ctypes reported
          ``access violation reading 0x15`` - 0x15 is ``NGX_VERSION_API``, so
          the runtime dereferenced our version constant as the feature-info
          pointer. The SDK runtime is called in the PUBLIC argument order.

        An NGX *error* moves on to the next route. A *fault* (OSError from
        ctypes) stops the ladder: a process whose NGX runtime faulted is not a
        process to keep working in, so that case raises loudly and says so.
        """
        runtime = sr_runtime_path(sr_dll_dir)
        if runtime:
            _log().status(
                "[ANTs] SR stage: %s (%d bytes) from %s - %s",
                os.path.basename(runtime), os.path.getsize(runtime),
                os.path.dirname(runtime), describe_runtime(runtime))
        else:
            _log().status(
                "[ANTs] SR stage: no %s under %s - starting from the driver "
                "core alone.", _SR_DLL_NAME, sr_dll_dir)

        # (label, path, own parameters?, preload the core?, owner via shim?)
        routes = []
        if runtime:
            routes.append((
                f"the SR runtime '{os.path.basename(runtime)}' as the "
                "app-facing module (public Init_Ext order)",
                runtime, False, True, False))
            # The one geometry both rig runs left standing: a direct
            # public-order call is rejected as coming from the wrong caller
            # (0xBAD00002, run 30) and the shim with the snippet's SWAPPED
            # order faults inside the runtime (rig 29) - so a feature
            # provider's caller check needs the shim AND the public order.
            routes.append((
                f"the SR runtime '{os.path.basename(runtime)}' as the "
                "app-facing module, called through the caller shim",
                runtime, False, True, True))
        if core_path:
            routes.append((
                f"the driver core '{os.path.basename(str(core_path))}' with "
                "the SR search path", core_path, False, False, None))
        if runtime and os.environ.get("ANTS_SR_SNIPPET_DIRECT") == "1":
            routes.append(("snippet-direct (NR-style build, swapped Init_Ext "
                           "argument order)", runtime, True, False, None))
        if not routes:
            raise DlssSrError(
                "[ANTs] No DLSS SR runtime and no NGX driver core to start the "
                f"pre-denoise stage with (sr_dll_dir={sr_dll_dir!r}).\n"
                "    Install nvngx_dlss*.dll into ComfyUI/models/DLSS/SR, pick "
                "it in sr_dll_version and press refresh.")

        failures = []
        for label, path, own_params, preload_core, owner_shim in routes:
            session = None
            try:
                # ONE identity for every ANTs NGX session (rig 02:48 + Claude
                # Sonnet 5: the core keeps the FIRST init's app id and search
                # paths for the whole process), so the SR stage inits exactly
                # like the NR stage - same app id, same project id, same app
                # data path, same union search paths. ANTS_SR_APP_ID restores
                # an A/B against a different identity without a code change.
                # The project-id init call is the shape the NR stage uses for
                # a CORE-owned session, so it stays on that lane: the SR
                # runtime (routes 1/1b) keeps the public Init_Ext form the rig
                # has already seen, now with the shared app id and the shim
                # geometry.
                session = NgxSession(self.gpu, path, search_paths=search,
                                     app_id=sr_app_id(),
                                     project_id=(None if (own_params or preload_core)
                                                 else NR_PROJECT_ID),
                                     use_own_parameters=own_params,
                                     preload_core=preload_core,
                                     app_data_path=app_data_path,
                                     owner_via_shim=owner_shim)
                self._apply_create_params(session, quality, hdr, preset)
                session.create_feature(FEATURE_SR)
            except OSError as exc:
                # ctypes turns a fault inside the runtime into OSError. Never
                # try another route after that (rig 29).
                self._quiet_close(session)
                raise DlssSrError(
                    "[ANTs] The DLSS SR runtime FAULTED (access violation) "
                    f"inside {path}: {exc}\n"
                    "    The call layout does not match this build (see "
                    "ANTS_SR_SNIPPET_DIRECT) or the file is not an SR "
                    "runtime.\n"
                    "    RESTART ComfyUI before running again - the process "
                    "is no longer trustworthy.\n"
                    "    To skip the stage entirely: pre_denoise_mode OFF or "
                    "sr_strength 0.") from exc
            except DlssSrError as exc:
                self._quiet_close(session)
                failures.append((label, exc))
                continue
            _log().status("[ANTs] SR session route: %s - %s", label,
                          os.path.basename(str(path)))
            return session

        detail = "\n".join(f"    - {label}: {exc}" for label, exc in failures)
        raise DlssSrError(
            "[ANTs] The DLSS SR pre-denoise stage could not create feature 1.\n"
            f"    runtime: {runtime or '<no nvngx_dlss.dll in the stage dir>'}\n"
            f"{detail}\n"
            "    Check that ComfyUI/models/DLSS/SR holds a real nvngx_dlss*.dll "
            "(tens of MB), pick it in sr_dll_version and press refresh; the "
            "driver must be DLSS-capable.\n"
            "    NGX keeps ONE context per process: the app id and the "
            "feature-library search paths come from the FIRST init in this "
            "ComfyUI run. If an earlier prompt inited the NR stage, that "
            "context is the one this stage must fit into; the '[ANTs] NGX "
            "init' lines above list what it saw, and a CONTEXT MISMATCH "
            "warning names the folders it does not have. Restarting ComfyUI "
            "and running this stage first is the quick way to tell the two "
            "apart.\n"
            "    To skip the stage: pre_denoise_mode OFF or sr_strength 0.")

    @staticmethod
    def _quiet_close(session):
        """Close a half-built session; a teardown complaint is not the story."""
        if session is None:
            return
        try:
            session.close()
        except Exception:
            pass

    def _apply_create_params(self, ngx, quality, hdr, preset):
        p = ngx.params
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

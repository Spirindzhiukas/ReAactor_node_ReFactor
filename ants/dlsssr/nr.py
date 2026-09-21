"""DLSS Neural Rendering (feature 18) — the DLSS5 node's native engine.

Core-owned route (run 28+, the layout every working host of this runtime
uses): the driver core ``_nvngx.dll`` owns the NGX session and hands out the
**capability parameter map**, while ``nvngx_dlssnr.dll`` — a *snippet*, not a
standalone runtime — hosts feature 18 and receives Create/Evaluate/Release
through the caller shim, with its own ``Init_Ext`` argument order.

Surfaces follow the reference host: ``DLSSNR.Color``/``Output`` are
``R16G16B16A16_FLOAT`` (the runtime renders in the HDR-capable domain),
``DLSSNR.MVec`` is ``R16G16_FLOAT`` and ``DLSSNR.Depth`` ``R32_FLOAT``; a
still frame has no motion, so both guides are zero-filled with
``DepthInverted=1`` and unit motion-vector scales (a zeroed guide with unit
scale is what the reference host feeds for video frames as well).

The runtime validates its create contract against the host's parameters — a
missing guide resource, subrect, ``ScalingRatio`` or the
``DLSSNRComputeScalingRatioCallback`` is not a warning but a hard failure —
so the full contract is written before ``CreateFeature`` and refreshed (the
look controls) before every evaluate.

Look-control mapping (helper-ABI control -> raw NGX parameter):
  style -> DLSSNR.Style (u32)            intensity -> DLSSNR.Intensity (f32)
  local_tone -> ..LocalToneStrength      local_structure -> ..LocalStructureStrength
  skin_structure -> ..SkinStructureStrength
  tone_preservation -> ..GlobalToneStrength
  color_strength / face_skin_protection / grain_preservation /
  shimmer_suppression -> best-effort extras (inert if this runtime build
  has no such parameter — unset/unknown parameters are simply never read).
"""

import ctypes
import os

import numpy as np

from . import d3d12 as d3d
from .errors import DlssSrError
from .ngx import FEATURE_NR, NR_APP_ID, NgxSession

_CVOID = ctypes.c_void_p
_CI32 = ctypes.c_int32

_STYLE_TO_INT = {"Default": 0, "Natural": 1, "Cinematic": 2}
NR_INT_TO_STYLE = {v: k for k, v in _STYLE_TO_INT.items()}

# Feature 18 quality contract (both reference hosts, credited in CLAUDE.md):
#  * 1x / native (our node): ``PerfQualityValue`` is the request's own quality
#    - a native-ratio request is DLAA (5) and the runtime derives the fixed
#    1.0 scaling ratio from it ("case 5: *ratio = 1.0f").
#  * ``6`` is the NEURAL POST-PASS value: it is only correct when an ordinary
#    DLSS carrier already enlarged the frame and feature 18 runs on top of it.
# Writing 6 for a 1x request asks the runtime for a network shape that does
# not match the 1.0 ratio our scaling callback reports - a contract mismatch.
# ANTS_NR_PERF_QUALITY=none leaves the parameter unset (the still-image host
# does exactly that), so both geometries stay one env line apart.
NR_PERF_QUALITY_1X = 5          # DLAA / native
NR_POSTPASS_PERF_QUALITY = 6    # neural post-pass over a DLSS carrier
NR_SCALING_RATIO = 1.0


def _rgba8_to_fp16(payload, width, height):
    """RGBA8 bytes -> RGBA16F bytes (host-side, numpy)."""
    arr = np.frombuffer(payload, dtype=np.uint8).reshape(height, width, 4)
    return (arr.astype(np.float32) / 255.0).astype(np.float16).tobytes()


def _fp16_to_rgba8(payload, width, height):
    """RGBA16F bytes -> clipped RGBA8 bytes (host-side, numpy)."""
    arr = np.frombuffer(payload, dtype=np.float16).reshape(height, width, 4)
    arr = np.clip(arr.astype(np.float32), 0.0, 1.0)
    return (arr * 255.0 + 0.5).astype(np.uint8).tobytes()


def fp16_anomalies(payload):
    """(nonfinite, out_of_range, values) counts for an RGBA16F readback.

    The RGBA8 conversion below CLAMPS (and casts), so an Inf, a NaN or a wildly
    saturated value would reach the user as 0 or 255 without a word - the host
    clamp is exactly what hides an engine that produces garbage. The node asks
    for this scan on the FIRST frame of a prompt only
    (``evaluate(check_anomalies=True)``), so a running session never pays for
    a whole-payload pass.
    """
    arr = np.frombuffer(payload, dtype=np.float16)
    finite = np.isfinite(arr)
    nonfinite = int(arr.size - int(finite.sum()))
    vals = arr[finite].astype(np.float32)
    out_of_range = int(((vals < 0.0) | (vals > 1.0)).sum())
    return nonfinite, out_of_range, int(arr.size)


class DlssNrSession:
    """One created NR feature (1:1 enhancement); evaluate() runs one frame."""

    def __init__(self, gpu, width, height, dll_path, style="Default",
                 intensity=1.0, local_tone=0.0, local_structure=1.0,
                 skin_structure=0.5, color_strength=0.5, tone_preservation=0.5,
                 face_skin_protection=0.0, grain_preservation=0.0,
                 auto_mask=False, nr_preset=0, app_data_path=None,
                 use_own_parameters=None):
        from .discovery import stage_nr_runtime
        from .ngx import locate_ngx_core

        self.gpu = gpu
        self.w, self.h = int(width), int(height)
        # frames this session has produced (soak line: a reused session's count
        # keeps climbing across prompts, a per-prompt session restarts at 1)
        self.evaluates = 0
        # last fp16_anomalies() tuple, filled only when the caller asked for it
        self.last_output_anomalies = None
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
        env_quality = os.environ.get("ANTS_NR_PERF_QUALITY", "").strip()
        self.perf_quality = NR_PERF_QUALITY_1X
        if env_quality.lower() in ("none", "unset", "off"):
            self.perf_quality = None       # never write the parameter at all
        elif env_quality:
            try:
                self.perf_quality = int(env_quality, 0)
            except ValueError:
                from ..log import dlss_logger
                dlss_logger.warning(
                    "[ANTs] ANTS_NR_PERF_QUALITY='%s' is not a number - using "
                    "the native value %d.", env_quality, NR_PERF_QUALITY_1X)
        # The runtime is loaded as nvngx_dlssnr.dll from a staged folder: every
        # host that works loads it under that name, and a renamed copy is the
        # one configuration that exists nowhere in the wild.
        from ..log import dlss_logger
        self.stage_dir = stage_nr_runtime(dll_path)
        runtime = os.path.join(self.stage_dir, "nvngx_dlssnr.dll")
        if not os.path.isfile(runtime):
            raise DlssSrError(
                "[ANTs] canonical NR runtime missing from the staging folder:\n"
                f"    {runtime}\n"
                f"    (staged from {dll_path})")
        selected = os.path.normcase(os.path.abspath(dll_path)) == \
            os.path.normcase(os.path.abspath(runtime))
        # The ONE file we run must be the file the owner selected: a same-
        # named sibling in the same folder silently became the runtime on
        # run 28 and confounded that experiment (see stage_nr_runtime).
        if not selected and os.path.getsize(runtime) != os.path.getsize(dll_path):
            raise DlssSrError(
                "[ANTs] NR staging mismatch: the canonical runtime is not a "
                "copy of the selected build.\n"
                f"    selected: {dll_path} ({os.path.getsize(dll_path)} bytes)\n"
                f"    staged:   {runtime} ({os.path.getsize(runtime)} bytes)\n"
                "    Nothing is loaded. Delete the staged folder and retry.")
        dlss_logger.status(
            "[ANTs] NR runtime in use: "
            f"{runtime} ({os.path.getsize(runtime)} bytes)"
            + ("" if selected else
               f" - a staged copy of the selected {os.path.basename(dll_path)}"))

        # Legacy snippet-direct mode (ANTS_NR_USE_OWN_PARAMS=1) stays
        # available for A/Bs: it is the configuration runs 14-27c used.
        if use_own_parameters is None:
            use_own_parameters = os.environ.get("ANTS_NR_USE_OWN_PARAMS") == "1"

        # The session unions this with every staged feature dir and NVIDIA's
        # models dir (ngx.feature_search_paths): the core keeps the FIRST init's
        # search paths for the whole process, so the NR stage must hand it a
        # list that already contains the SR library - otherwise the SR stage
        # in a later prompt cannot register its provider (rig 02:48).
        search = [self.stage_dir]
        if use_own_parameters:
            # legacy snippet-direct: the snippet is the session owner (and the
            # core is merely preloaded for the snippet's evaluate path)
            self.ngx = NgxSession(
                gpu, runtime, app_id=NR_APP_ID, search_paths=search,
                use_own_parameters=True, app_data_path=app_data_path)
        else:
            self.ngx = NgxSession(
                gpu, locate_ngx_core(), app_id=NR_APP_ID, search_paths=search,
                app_data_path=app_data_path, feature_module_path=runtime)

        dev = gpu.device
        # Input state contract (rig 18:25/20:39/21:52): the colour/guide
        # INPUTS live in a shader-resource state and only the OUTPUT sits in
        # UNORDERED_ACCESS - the same split the shipped open-source ComfyUI
        # host uses. Handing NGX an input in the UAV state is what D3D12
        # answers with E_INVALIDARG at Close() (and what the driver can turn
        # into a GPU fault). ANTS_NR_INPUT_STATE=uav restores the old state.
        self.color = dev.create_input_texture2d(
            self.w, self.h, d3d.DXGI_FORMAT_R16G16B16A16_FLOAT,
            label="nr color")
        self.output = dev.create_texture2d(
            self.w, self.h, d3d.DXGI_FORMAT_R16G16B16A16_FLOAT,
            label="nr output")
        # Guides: a still frame has no motion and no depth, so they must read
        # as zeros - and D3D12 guarantees that: a resource created with
        # CreateCommittedResource is zero-initialized before any copy touches
        # it. Uploading zeros would be a wasted submit (and was the first
        # thing this session recorded, i.e. the first thing that could go
        # wrong before the feature even exists). They are created straight in
        # the input state, so no barrier is ever needed for them.
        self.motion = dev.create_input_texture2d(
            self.w, self.h, d3d.DXGI_FORMAT_R16G16_FLOAT, label="nr motion")
        self.depth = dev.create_input_texture2d(
            self.w, self.h, d3d.DXGI_FORMAT_R32_FLOAT, label="nr depth")

        self._scaling_cb = None
        self._contract_logged = False
        self._apply_create_params(int(nr_preset))
        self.ngx.create_feature(FEATURE_NR)

    # ------------------------------------------------------------ params
    def _apply_create_params(self, nr_preset):
        """The full feature-18 create contract (reference-host parity)."""
        p = self.ngx.params
        w, h = self.w, self.h
        p.set_u32("DLSSNR.Width", w)
        p.set_u32("DLSSNR.Height", h)
        p.set_u32("DLSSNR.InputWidth", w)
        p.set_u32("DLSSNR.InputHeight", h)
        p.set_u32("DLSSNR.OutputWidth", w)
        p.set_u32("DLSSNR.OutputHeight", h)
        p.set_u32("DLSSNR.Output.Width", w)
        p.set_u32("DLSSNR.Output.Height", h)
        if self.perf_quality is not None:
            p.set_u32("PerfQualityValue", self.perf_quality)
        p.set_u32("CreationNodeMask", 1)
        p.set_u32("VisibilityNodeMask", 1)
        p.set_u32("DLSSNR.Enabled", 1)
        p.set_u32("DLSSNR.Upscaling", 0)
        p.set_u32("DLSSNR.UICorrection", 0)
        p.set_u32("DLSSNR.DepthInverted", 1)
        p.set_u32("DLSSNR.UseAutoMask", 1 if self.settings["auto_mask"] else 0)
        p.set_i32("DLSSNR.Hint.Render.Preset", int(nr_preset))
        p.set_u32("DLSSNR.Style", self.settings["style"])
        # Scale contract: 1x post-pass, unit guide scales (the zeroed motion
        # guide makes the value irrelevant numerically - the runtime checks it
        # anyway).
        p.set_f32("DLSSNR.ScalingRatio", NR_SCALING_RATIO)
        p.set_f32("DLSSNR.Scale", 1.0)
        p.set_f32("DLSSNR.MVecScaleX", 1.0)
        p.set_f32("DLSSNR.MVecScaleY", 1.0)
        p.set_f32("DLSSNR.GlobalToneStrength", self.settings["tone_preservation"])
        self._write_surfaces(p)
        self._register_scaling_callback()

    def _write_surfaces(self, p):
        """Guides + surfaces + the subrects the runtime validates against.

        Called at create AND before every evaluate (reference-host parity:
        both working hosts re-apply the whole parameter set per frame, so a
        runtime that clears or invalidates entries between frames cannot see
        a half-written contract).
        """
        w, h = self.w, self.h
        p.set_resource("DLSSNR.Color", self.color.ptr)
        p.set_resource("DLSSNR.MVec", self.motion.ptr)
        p.set_resource("DLSSNR.Depth", self.depth.ptr)
        p.set_resource("DLSSNR.Output", self.output.ptr)
        p.set_resource("DLSSNR.Backbuffer", self.output.ptr)
        for prefix in ("Color", "MVec", "Depth", "Output"):
            p.set_u32(f"DLSSNR.{prefix}SubrectBaseX", 0)
            p.set_u32(f"DLSSNR.{prefix}SubrectBaseY", 0)
            p.set_u32(f"DLSSNR.{prefix}SubrectWidth", w)
            p.set_u32(f"DLSSNR.{prefix}SubrectHeight", h)
        p.set_u32("DLSSNR.InputWidth", w)
        p.set_u32("DLSSNR.InputHeight", h)
        p.set_u32("DLSSNR.OutputWidth", w)
        p.set_u32("DLSSNR.OutputHeight", h)

    def _register_scaling_callback(self):
        """``DLSSNRComputeScalingRatioCallback`` — the runtime calls this while
        it validates the create contract; it must see a 1x post-pass ratio."""
        params_ptr = int(self.ngx.params.ptr.value or 0)
        flat = getattr(self.ngx.params, "flat_api", {}) or {}
        setter = flat.get("NVSDK_NGX_Parameter_SetF")

        @ctypes.CFUNCTYPE(_CI32, _CVOID)
        def _cb(param_map):
            """Answer with ScalingRatio=1.0 for the map the runtime hands us."""
            target = int(param_map or 0) or params_ptr
            try:
                if setter is not None:
                    setter(ctypes.c_void_p(target), ctypes.create_string_buffer(
                        b"DLSSNR.ScalingRatio\x00"),
                        ctypes.c_float(NR_SCALING_RATIO))
                elif target == params_ptr:
                    self.ngx.params.set_f32("DLSSNR.ScalingRatio",
                                            NR_SCALING_RATIO)
            except Exception:
                pass
            return 1  # NGX_SUCCESS
        self._scaling_cb = _cb
        self.ngx.params.set_pointer("DLSSNRComputeScalingRatioCallback",
                                    ctypes.cast(_cb, _CVOID))
        self.ngx._cb_keep.append(_cb)

    def _set_eval_params(self, reset):
        p = self.ngx.params
        s = self.settings
        p.set_u32("DLSSNR.Reset", 1 if reset else 0)
        p.set_f32("DLSSNR.Intensity", s["intensity"])
        p.set_f32("DLSSNR.LocalToneStrength", s["local_tone"])
        p.set_f32("DLSSNR.LocalStructureStrength", s["local_structure"])
        p.set_f32("DLSSNR.SkinStructureStrength", s["skin_structure"])
        p.set_f32("DLSSNR.GlobalToneStrength", s["tone_preservation"])
        p.set_u32("DLSSNR.Style", s["style"])
        p.set_u32("DLSSNR.UseAutoMask", 1 if s["auto_mask"] else 0)
        p.set_u32("DLSSNR.UICorrection", 0)
        p.set_u32("DLSSNR.DepthInverted", 1)
        p.set_f32("DLSSNR.ScalingRatio", NR_SCALING_RATIO)
        p.set_u32("DLSSNR.Enabled", 1)
        p.set_u32("DLSSNR.Upscaling", 0)
        self._write_surfaces(p)
        # Best-effort extras (inert when this build lacks the parameter):
        p.set_f32("DLSSNR.ColorStrength", s["color_strength"])
        p.set_f32("DLSSNR.FaceSkinProtection", s["face_skin_protection"])
        p.set_f32("DLSSNR.GrainPreservation", s["grain_preservation"])

    def evaluate(self, color_rgba, reset=True, check_anomalies=False):
        """Enhance one RGBA8 frame 1:1; returns the enhanced RGBA8 frame.

        ``check_anomalies`` scans the RGBA16F readback for NaN/Inf and for
        out-of-range values before they are clamped away (see
        :func:`fp16_anomalies`); the result lands in
        ``self.last_output_anomalies``. The node asks for it on the first frame
        of a prompt - see ``_check_native_output``.
        """
        self.evaluates += 1
        expected = self.w * self.h * 4
        if len(color_rgba) != expected:
            raise DlssSrError(
                f"[ANTs] DLSS NR expected {expected} color bytes, got {len(color_rgba)}.")
        uav = d3d.D3D12_RESOURCE_STATE_UNORDERED_ACCESS
        # Colour goes in as a shader-resource read (see the note in __init__):
        # the upload leaves it in the INPUT state, not in UAV.
        self.gpu.upload_texture(self.color,
                                _rgba8_to_fp16(color_rgba, self.w, self.h),
                                d3d.input_state())
        self.gpu.transition(self.output, uav)
        # Drain before the feature call: the proven hosts close, execute and
        # fence-wait every copy they make, so the runtime always receives a
        # freshly reset, EMPTY command list. Ours used to hand it a list with
        # our unexecuted copy and barriers still pending.
        self.gpu.submit_and_wait()
        self._set_eval_params(reset)
        if not self._contract_logged:
            # One-shot contract dump: if the snippet throws on the first
            # frame, these are the numbers its validation saw. One line per
            # session, right before the evaluate that may refuse them.
            self._contract_logged = True
            from ..log import dlss_logger as _dl
            _dl.status(
                "[ANTs] NR contract (first frame): "
                f"in {self.w}x{self.h} out {self.w}x{self.h} "
                f"quality {self.perf_quality} scaling {NR_SCALING_RATIO} "
                f"scale 1.0 mvec_scale 1.0 "
                f"style {self.settings['style']} "
                f"intensity {float(self.settings['intensity']):.3f} "
                f"tone {float(self.settings['tone_preservation']):.3f} "
                f"uicorr 0 depth_inverted 1 upscaling 0 "
                f"auto_mask {int(bool(self.settings['auto_mask']))} "
                f"surfaces color/output RGBA16F mvec R16G16_FLOAT "
                f"depth R32_FLOAT (inputs {d3d.state_name(d3d.input_state())}, "
                f"output {d3d.state_name(uav)}) subrects full-frame")
        try:
            self.ngx.evaluate()
        except Exception as exc:
            # C++ exceptions out of the snippet are CATCHABLE (run 30): the
            # first time the failure is not a silent process kill, so make
            # the most of it - name the throw and where it came from.
            from .crashlog import last_cxx_report, crash_file_path
            cxx = last_cxx_report()
            log = crash_file_path()
            detail = f"{cxx}\n    original Python exception: {exc}" if cxx \
                else str(exc)
            raise DlssSrError(
                "[ANTs] NGX EvaluateFeature failed for the NR snippet.\n"
                f"    {detail}\n"
                + (f"    Full detail: {log}\n" if log else "")
                + "    Send that file (and the console) - a C++ throw from "
                  "the snippet means it ran and rejected the frame or the\n"
                  "    parameter contract; the type and throw site above name "
                  "the check that refused.") from exc
        # The runtime records its work into the dedicated list it was handed
        # (GpuContext.command_list()), so THAT list has to be closed, executed
        # and waited on before the output means anything - the proven hosts do
        # exactly this. Our own copies/barriers live in the other list, so a
        # runtime that poisons its list cannot take the frame upload with it.
        self.gpu.runtime_submit_and_wait()
        raw = self.gpu.readback_texture(self.output, uav)
        self.last_output_anomalies = (fp16_anomalies(raw) if check_anomalies
                                      else None)
        return _fp16_to_rgba8(raw, self.w, self.h)

    def close(self):
        self.ngx.close()
        self._scaling_cb = None

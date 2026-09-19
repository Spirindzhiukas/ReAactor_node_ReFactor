import ctypes
import os
import threading

import numpy as np

from ..log import logger

# --- constants ---
BRIDGE_ABI_VERSION = 6
MEMORY_HOST = 0
MEMORY_CUDA = 1
MEMORY_NONE = 2

class NeuralBridgeError(Exception):
    pass

# --- C structs for the DLL bridge ---

class RenderParameters(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("abi_version", ctypes.c_uint32),
        ("style", ctypes.c_int32),
        ("intensity", ctypes.c_float),
        ("tone", ctypes.c_float),
        ("structure", ctypes.c_float),
        ("skin", ctypes.c_float),
        ("automask", ctypes.c_int32),
        ("reset", ctypes.c_int32),
        ("color_strength", ctypes.c_float),
        ("tone_preservation", ctypes.c_float),
        ("mask_memory_type", ctypes.c_uint32),
        ("mask_width", ctypes.c_uint32),
        ("mask_height", ctypes.c_uint32),
        ("mask_stride", ctypes.c_uint32),
        ("mask_plane", ctypes.c_uint64),          # uint64 here -> 4 bytes of system padding
        ("face_skin_protection", ctypes.c_float),
        ("grain_preservation", ctypes.c_float),
        ("nr_passes", ctypes.c_int32),
        ("shimmer_suppression", ctypes.c_float),
        ("prefer_nvof", ctypes.c_int32),
    ]


def _render_parameters(settings: dict, reset: bool, mask, abi_version: int) -> RenderParameters:
    """Pack the v6 control struct (shared by the host and CUDA paths).

    Masks always travel as HOST memory inside the struct (mask_memory_type,
    mask_plane = RAM pointer) - this matches Merserk's reference driver, which
    passes device-pointer 0 in the dedicated CUDA mask slot and keeps host
    masks valid in CUDA mode too.
    """
    params = RenderParameters()
    params.struct_size = ctypes.sizeof(RenderParameters)
    params.abi_version = abi_version
    params.style = int(settings.get("style"))
    params.intensity = float(settings.get("intensity"))
    params.tone = float(settings.get("local_tone"))
    params.structure = float(settings.get("local_structure"))
    params.skin = float(settings.get("skin_structure"))
    params.automask = int(bool(settings.get("auto_mask")))
    params.reset = int(bool(reset))
    params.color_strength = float(settings.get("color_strength"))
    params.tone_preservation = float(settings.get("tone_preservation"))
    params.face_skin_protection = float(settings.get("face_skin_protection"))
    params.grain_preservation = float(settings.get("grain_preservation"))
    params.nr_passes = int(settings.get("nr_passes"))
    params.shimmer_suppression = float(settings.get("shimmer_suppression", 0.0))
    params.prefer_nvof = int(bool(settings.get("prefer_nvof", False)))
    params.mask_memory_type = MEMORY_NONE
    if mask is not None:
        params.mask_memory_type = MEMORY_HOST
        params.mask_width = int(mask.shape[1])
        params.mask_height = int(mask.shape[0])
        params.mask_stride = int(mask.strides[0])
        params.mask_plane = int(mask.ctypes.data)
    return params


class DLSSStandaloneManager:
    def __init__(self, dll_dir: str):
        self._lock = threading.RLock()
        self._library = None
        self.dll_dir = dll_dir
        self._process_cuda = None
        self._cuda_supported_fn = None
        self._cuda_status_fn = None
        self._gpu_name_fn = None

    @staticmethod
    def find_engine_dll(dll_dir: str):
        """Locate the bridge/engine DLL in a set regardless of file naming.

        Per the owner's 'accept any filenames' rule: probe each .dll for the
        engine's export (dlss5nr_init). Prefer a name containing 'engine'
        first (cheap fast path), then brute-force probe. Non-Windows/test
        envs fall back to name matching only.
        """
        candidates = sorted(f for f in os.listdir(dll_dir) if f.lower().endswith(".dll"))
        named = [f for f in candidates if "engine" in f.lower()] or candidates
        probed_any = False
        for fname in named:
            path = os.path.join(dll_dir, fname)
            loader = getattr(ctypes, "WinDLL", None)
            if loader is None:
                return path  # non-Windows: name match is all we can do
            try:
                lib = loader(path)
                if hasattr(lib, "dlss5nr_init"):
                    return path
                probed_any = True
            except OSError:
                continue
        if probed_any or candidates:
            raise NeuralBridgeError(
                f"No DLL in '{dll_dir}' exports dlss5nr_init - this set does not "
                "contain the neuroframe bridge/engine helper. Place the helper DLLs "
                "(see ants/dlssnr/dll_README.md) next to nvngx_dlssnr.dll."
            )
        raise NeuralBridgeError(f"No .dll files found in '{dll_dir}'")

    def initialize(self, ordinal: int):
        with self._lock:
            if self._library is not None:
                return True

            if hasattr(os, 'add_dll_directory'):
                os.add_dll_directory(self.dll_dir)

            engine_path = self.find_engine_dll(self.dll_dir)

            loader = getattr(ctypes, "WinDLL", ctypes.CDLL)
            try:
                self._library = loader(engine_path)
            except OSError as exc:
                raise NeuralBridgeError(f"DLL load failed: {exc}")
                
            # init signature
            self._library.dlss5nr_init.argtypes = [
                ctypes.c_int, ctypes.c_wchar_p, ctypes.c_char_p, ctypes.c_int
            ]
            self._library.dlss5nr_init.restype = ctypes.c_int
            
            # HOST-render signature (process_v6 instead of process_cuda_v6)
            c_float_p = ctypes.POINTER(ctypes.c_float)
            self._library.dlss5nr_process_v6.argtypes = [
                c_float_p, c_float_p, ctypes.c_int, ctypes.c_int,
                ctypes.POINTER(RenderParameters), ctypes.c_char_p, ctypes.c_int
            ]
            self._library.dlss5nr_process_v6.restype = ctypes.c_int

            # CUDA device-pointer variant (GPU-resident processing). Both the
            # engine and PyTorch use the CUDA PRIMARY context, so torch CUDA
            # tensor pointers are directly valid inputs - no driver-API
            # plumbing, no extra copies. Optional: older engine builds may
            # lack these exports; capability is probed via cuda_available().
            c_u64 = ctypes.c_uint64
            if hasattr(self._library, "dlss5nr_process_cuda_v6"):
                self._library.dlss5nr_process_cuda_v6.argtypes = [
                    c_u64, c_u64, ctypes.c_int, ctypes.c_int,
                    c_u64, ctypes.POINTER(RenderParameters),
                    ctypes.c_char_p, ctypes.c_int,
                ]
                self._library.dlss5nr_process_cuda_v6.restype = ctypes.c_int
                self._process_cuda = self._library.dlss5nr_process_cuda_v6
            for name, attr in (
                ("dlss5nr_cuda_supported", "_cuda_supported_fn"),
                ("dlss5nr_cuda_status", "_cuda_status_fn"),
                ("dlss5nr_gpu_name", "_gpu_name_fn"),
            ):
                fn = getattr(self._library, name, None)
                if fn is not None:
                    if name == "dlss5nr_cuda_supported":
                        fn.argtypes, fn.restype = [], ctypes.c_int
                    elif name == "dlss5nr_cuda_status":
                        fn.argtypes, fn.restype = [ctypes.c_char_p, ctypes.c_int], ctypes.c_int
                    else:
                        fn.argtypes, fn.restype = [], ctypes.c_char_p
                    setattr(self, attr, fn)

            try:
                self._library.dlss5nr_frame_abi_version.argtypes = []
                self._library.dlss5nr_frame_abi_version.restype = ctypes.c_uint32
                self.actual_abi = self._library.dlss5nr_frame_abi_version()
            except Exception:
                self.actual_abi = BRIDGE_ABI_VERSION

            error = ctypes.create_string_buffer(4096)
            ok = self._library.dlss5nr_init(ordinal, self.dll_dir, error, len(error))
            
            if not ok:
                err_msg = error.value.decode('utf-8', errors='ignore')
                raise NeuralBridgeError(f"Bridge initialization failed: {err_msg}")
            
            return True

    def shutdown(self):
        """Graceful engine teardown (dlss5nr_shutdown, engine >= 1.4)."""
        with self._lock:
            if self._library is None:
                return
            try:
                fn = getattr(self._library, "dlss5nr_shutdown", None)
                if fn is not None:
                    fn.argtypes, fn.restype = [], None
                    fn()
            except Exception as exc:
                logger.debug(f"dlss5nr_shutdown raised (ignored): {exc}")
            self._library = None

    def cuda_available(self):
        """(ok, reason): engine-side CUDA interop readiness."""
        if self._process_cuda is None:
            return False, ("engine DLL has no dlss5nr_process_cuda_v6 export "
                           "(older neuroframe build - grab a fresh neuroframe_dlls.zip)")
        if self._cuda_supported_fn is not None and not self._cuda_supported_fn():
            status = ""
            if self._cuda_status_fn is not None:
                buf = ctypes.create_string_buffer(4096)
                try:
                    self._cuda_status_fn(buf, len(buf))
                    status = buf.value.decode("utf-8", errors="ignore")
                except Exception:
                    pass
            return False, (f"engine reports CUDA interoperability unavailable"
                           f"{': ' + status if status else ''}")
        return True, "engine CUDA interop ready"

    def gpu_name(self):
        try:
            if self._gpu_name_fn is not None:
                return (self._gpu_name_fn() or b"").decode("utf-8", errors="ignore")
        except Exception:
            pass
        return ""

    def process_cuda(self, source_device_ptr: int, destination_device_ptr: int,
                     width: int, height: int, settings: dict, reset: bool,
                     mask: np.ndarray = None):
        """GPU-resident render: source/destination are CUDA device pointers
        (e.g. ``torch CUDA tensor.data_ptr()``).

        Masks stay host-side (see ``_render_parameters``). The engine enqueues
        its D3D12/NGX work on the shared primary context - the caller
        synchronizes afterwards (``torch.cuda.synchronize()`` covers it).
        """
        with self._lock:
            if self._process_cuda is None:
                raise NeuralBridgeError(
                    "[ANTs] This engine DLL has no CUDA entry point "
                    "(dlss5nr_process_cuda_v6). Update neuroframe_dlls.zip or "
                    "switch GPU acceleration to 'CPU (host staging)'.")
            error = ctypes.create_string_buffer(4096)
            params = _render_parameters(settings, reset, mask,
                                        getattr(self, "actual_abi", BRIDGE_ABI_VERSION))
            ok = self._process_cuda(
                ctypes.c_uint64(int(source_device_ptr)),
                ctypes.c_uint64(int(destination_device_ptr)),
                int(width), int(height),
                ctypes.c_uint64(0),
                ctypes.byref(params),
                error, len(error),
            )
            if not ok:
                err_msg = error.value.decode('utf-8', errors='ignore')
                raise NeuralBridgeError(
                    f"DLSS-5 CUDA process failed: {err_msg} "
                    "- you can switch GPU acceleration to 'CPU (host staging)'.")

    def process_host(self, source: np.ndarray, destination: np.ndarray, settings: dict, reset: bool, mask: np.ndarray = None):
        with self._lock:
            # The dll reads interleaved [H,W,C] float32 through a raw
            # pointer. A strided (e.g. planar movedim-view) source would be
            # decoded as sheared 3x3-mosaic garbage, so normalize it; the
            # destination is the preallocated ping-pong buffer - it must
            # already be correct (replacing it would break caller identity).
            if source.dtype != np.float32 or not source.flags["C_CONTIGUOUS"]:
                source = np.ascontiguousarray(source, dtype=np.float32)
            if destination.dtype != np.float32 or not destination.flags["C_CONTIGUOUS"]:
                raise NeuralBridgeError(
                    "[ANTs] process_host: the destination buffer must be a "
                    "C-contiguous float32 array of shape [H,W,C].")
            error = ctypes.create_string_buffer(4096)

            params = _render_parameters(settings, reset, mask,
                                        getattr(self, "actual_abi", BRIDGE_ABI_VERSION))

            c_float_p = ctypes.POINTER(ctypes.c_float)
            
            # call the HOST function (the DLL talks to the GPU itself)
            ok = self._library.dlss5nr_process_v6(
                source.ctypes.data_as(c_float_p),
                destination.ctypes.data_as(c_float_p),
                source.shape[1],
                source.shape[0],
                ctypes.byref(params),
                error,
                len(error)
            )
            
            if not ok:
                err_msg = error.value.decode('utf-8', errors='ignore')
                raise NeuralBridgeError(f"DLSS-5 process failed: {err_msg}")

"""The NGX host: core/snippet loading, init, and the feature lifecycle.

One class serves both providers, because the API surface is identical:

- **driver core** (`_nvngx.dll` from the driver store) — used for SR: the
  core locates and owns `nvngx_dlss.dll` via the search-path list in
  ``NVSDK_NGX_FeatureCommonInfo``;
- **snippet direct** (`nvngx_dlssnr.dll` loaded as the module) — used for
  NR: that runtime exports the whole NVSDK_NGX_D3D12 API itself and is
  handed our own parameter object.

Every NGX call is routed through the generated ``nvngx.dll`` thunk shim
(``shim.py``): slot 0 is re-pointed at the real function immediately before
each call, and the thunk ``call``s it so the runtime's caller-module check
(a return address inside an on-disk ``nvngx.dll``) passes. One shim is NOT
thread-safe (shared slots) — each session owns one.

Init order per runtime (technique reference DLT/DVT, verified against the
NVIDIA SDK headers): ``Init_Ext(appId, appDataPath, device, sdkVersion=0x15,
featureInfo)`` -> (core only) ``AllocateParameters`` -> ``CreateFeature``
(cmdList, featureId, params, outHandle) -> ``EvaluateFeature`` per frame ->
``ReleaseFeature``. There is deliberately NO Shutdown1 call: the current
driver core faults after a feature exists and releases the D3D12 device
(DLT issue #75) — NGX is reclaimed at process exit, and we release only the
objects we own.
"""

import tempfile
import ctypes
import os

from .errors import DlssSrError
from .parameters import CoreParameterObject, OwnParameterObject

_CVOID = ctypes.c_void_p
_CI32 = ctypes.c_int32
from . import shim as shim_mod


def _log():
    from ..log import dlss_logger
    return dlss_logger
from . import win32

NGX_VERSION_API = 0x15
NGX_ENGINE_TYPE_CUSTOM = 0

FEATURE_SR = 1    # NVSDK_NGX_Feature_SuperSampling
FEATURE_NR = 18   # NVSDK_NGX_Feature_NeuralRendering ("CG2R")

APP_ID = 0x4E5254530001            # the customary 'NRTS' test-app id
NGX_MODELS_DIR = r"C:\ProgramData\NVIDIA\NGX\models"

_SYSTEM_CPARAMS_ARGS = None


def locate_ngx_core():
    """Newest driver-store (or System32) NGX core; mirrors the rig probe."""
    candidates = []
    windir = os.environ.get("WINDIR", r"C:\Windows")
    system32 = os.path.join(windir, "System32")
    core = os.path.join(system32, "_nvngx.dll")
    if os.path.isfile(core):
        candidates.append(core)
    import glob
    repo = os.path.join(system32, "DriverStore", "FileRepository")
    folders = sorted(glob.glob(os.path.join(repo, "nv*.inf_amd64_*")))
    hits = []
    for folder in folders:
        p = os.path.join(folder, "_nvngx.dll")
        if os.path.isfile(p):
            hits.append(p)
    hits.sort(key=os.path.getmtime, reverse=True)
    candidates.extend(hits)
    if not candidates:
        raise DlssSrError(
            "[ANTs] No NGX core (_nvngx.dll) found in System32 or the driver "
            "store - is the NVIDIA display driver installed? "
            "(tools/probe_dlss_rig.py reports the same scan.)")
    return candidates[0]


class FeatureCommonInfo:
    """NVSDK_NGX_FeatureCommonInfo (40 bytes): path list + zero logging."""

    def __init__(self, search_paths):
        self._wide = [win32.wide(p) for p in search_paths]
        count = len(self._wide)
        self._path_array = (ctypes.c_void_p * max(1, count))(
            *[ctypes.cast(w, ctypes.c_void_p) for w in self._wide])
        self._struct = ctypes.create_string_buffer(40)
        struct = self._struct
        ptr = ctypes.c_void_p(ctypes.addressof(self._path_array))
        ctypes.memmove(ctypes.addressof(struct) + 0, ctypes.byref(ptr), ctypes.sizeof(ptr))
        ctypes.memmove(ctypes.addressof(struct) + 8,
                       ctypes.byref(ctypes.c_uint32(count)), 4)
        # InternalData (16..24) and LoggingInfo (24..40) stay zero.

    @property
    def ptr(self):
        return ctypes.cast(self._struct, ctypes.c_void_p)


def writable_cache_dir(tag):
    """A directory we can write artifacts into (shim PE, logs).

    The NGX core usually lives in the DriverStore (admin-only), so anything
    we must create next to a *call* goes to %LOCALAPPDATA%\\ANTs\\<tag>,
    falling back to the package dir, then the temp dir.
    """
    roots = []
    base = os.environ.get("LOCALAPPDATA")
    if base:
        roots.append(os.path.join(base, "ANTs", tag))
    roots.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", tag))
    roots.append(os.path.join(tempfile.gettempdir(), "ANTs", tag))
    for root in roots:
        try:
            os.makedirs(root, exist_ok=True)
            probe = os.path.join(root, ".write_probe")
            with open(probe, "w", encoding="ascii"):
                pass
            os.remove(probe)
            return root
        except OSError:
            continue
    raise DlssSrError("no writable directory found for NGX runtime artifacts")


class NgxModule:
    """A loaded NGX provider (driver core or snippet) + shim-routed calls."""

    def __init__(self, module_path, use_shim=True, forwarder_dir=None):
        self.path = None
        self.handle = None
        self.forwarder = None
        self._fwd_stub = None
        # The shim MUST be loaded BEFORE the runtime: the Windows loader
        # matches modules by base name, and our shim is FILE-named
        # "nvngx.dll". Loaded first, every later nvngx.dll reference (the
        # runtime's own imports, other loaders) binds to the shim; loaded
        # after a real nvngx.dll exists in the process, LoadLibraryExW
        # returns THAT module and our exports are "missing".
        if use_shim:
            fwd_path = shim_mod.write_shim(
                forwarder_dir or writable_cache_dir("shim"))
            self._fwd_handle = win32.load_library(fwd_path)
            # Identity check: which FILE did Windows actually map under this
            # handle? (The loader matches modules by base name.)
            loaded_from = win32.get_module_filename(self._fwd_handle)
            if loaded_from and os.path.normcase(loaded_from) != os.path.normcase(
                    os.path.abspath(fwd_path)):
                win32.free_library(self._fwd_handle)
                self._fwd_handle = None
                raise DlssSrError(
                    "[ANTs] nvngx.dll module-name collision: our shim could not be "
                    f"loaded as a distinct module.\n    We loaded: {fwd_path}"
                    f"\n    Windows returned: {loaded_from}"
                    "\n    Another nvngx.dll is already loaded in this process - "
                    "restart ComfyUI so the ANTs host loads first.")
            _, exports = shim_mod.build_shim_dll()

            def resolve(name):
                try:
                    return win32.get_proc(self._fwd_handle, name)
                except DlssSrError:
                    # GetProcAddress refuses our hand-built export directory
                    # (the loader maps the image fine, it just won't parse
                    # it) - resolve via the build-time-known RVA instead.
                    return win32.export_address(self._fwd_handle, exports[name])

            set_slots = resolve("fwd_set_slots")
            fwd_create = resolve("fwd_create")
            self._set_slots = win32.callable_at(
                set_slots, [_CVOID, _CVOID, _CVOID], None)
            # Raw thunk ADDRESS (analysis credit: Claude Sonnet 5) -
            # storing a ctypes callable here made fn() build a
            # Python-callback trampoline that mangled every NGX call's
            # arguments (garbage returns, 'Exception ignored' spam).
            self._fwd_stub = int(fwd_create)
        else:
            self._fwd_handle = None
        self.handle = win32.load_library(module_path)
        self.path = module_path

    def address(self, name):
        return win32.get_proc(self.handle, name)

    def has_export(self, name):
        try:
            self.address(name)
            return True
        except DlssSrError:
            return False

    def fn(self, name, argtypes, restype=ctypes.c_int32):
        """Bindable NGX function, routed through the shim when enabled."""
        address = self.address(name)
        if self._fwd_stub is None:
            return win32.callable_at(address, argtypes, restype)
        # Bind the thunk ADDRESS with the caller's real prototype (a
        # CFUNCTYPE over a raw int is a native call; over a callable it
        # would be a lossy Python trampoline - see _fwd_stub above).
        stub = win32.callable_at(self._fwd_stub, argtypes, restype)
        assert ctypes.cast(stub, ctypes.c_void_p).value == self._fwd_stub

        def routed(*args):
            self._set_slots(ctypes.c_void_p(address), None, None)
            return stub(*args)

        return routed

    def close(self):
        if self._fwd_handle:
            win32.free_library(self._fwd_handle)
            self._fwd_handle = None
        if self.handle:
            win32.free_library(self.handle)
            self.handle = None


class NgxSession:
    """Init + feature lifecycle over one NGX module and one D3D12 context."""

    def __init__(self, gpu, module_path, app_id=APP_ID, search_paths=(),
                 use_own_parameters=False, app_data_path=None, use_shim=True):
        self.gpu = gpu
        self.module = NgxModule(module_path, use_shim=use_shim,
                                forwarder_dir=writable_cache_dir("shim"))
        self._own_parameters = None
        self.params = None
        self.handle = None
        self._create = None
        self._evaluate = None
        self._release = None

        app_data = app_data_path or os.path.join(writable_cache_dir("appdata"), "logs")
        os.makedirs(app_data, exist_ok=True)

        info = FeatureCommonInfo(list(search_paths) or [os.path.dirname(module_path)])
        init_ext = self.module.fn("NVSDK_NGX_D3D12_Init_Ext",
                                  [_CVOID, _CVOID, _CVOID, _CI32, _CVOID])
        init4 = self.module.fn("NVSDK_NGX_D3D12_Init",
                               [_CVOID, _CVOID, _CVOID, _CI32])

        _log().status(f"NGX init -> {os.path.basename(module_path)} "
                      f"({'classic Init' if use_own_parameters else 'Init_Ext-first'})")
        def try_init():
            if use_own_parameters:
                # Snippet-direct runtimes (ReShade/RenoDX builds) target the
                # classic 4-arg Init - DVT's rig-proven NR flow. Init_Ext is
                # only the fallback (its extra arg may be ignored or worse).
                hr4 = init4(ctypes.c_void_p(app_id), win32.wide(app_data),
                            gpu.device.ptr, ctypes.c_int32(NGX_VERSION_API))
                if hr4 == 1:
                    return 1
            hr = init_ext(ctypes.c_void_p(app_id), win32.wide(app_data),
                          gpu.device.ptr, ctypes.c_int32(NGX_VERSION_API), info.ptr)
            if hr == 1:
                return 1
            if not use_own_parameters:
                hr4 = init4(ctypes.c_void_p(app_id), win32.wide(app_data),
                            gpu.device.ptr, ctypes.c_int32(NGX_VERSION_API))
                if hr4 == 1:
                    return 1
            return hr  # last failure, for the error message

        hr = try_init()
        _log().status(f"NGX init <- hr=0x{hr & 0xFFFFFFFF:08X}")
        if hr != 1:
            raise DlssSrError(
                f"[ANTs] NGX Init failed (0x{hr & 0xFFFFFFFF:08X}) for "
                f"{module_path} - the runtime rejected the session. Check the "
                "driver version and the DLL set folder.")

        if use_own_parameters:
            self._own_parameters = OwnParameterObject()
            self.params = self._own_parameters
        else:
            alloc = self.module.fn("NVSDK_NGX_D3D12_AllocateParameters", [_CVOID])
            out = ctypes.c_void_p()
            hr = alloc(ctypes.byref(out))
            if hr != 1:
                raise DlssSrError(
                    f"[ANTs] NGX AllocateParameters failed (0x{hr & 0xFFFFFFFF:08X}).")
            self._core_params = CoreParameterObject(out.value)
            self.params = self._core_params

        self._create = self.module.fn(
            "NVSDK_NGX_D3D12_CreateFeature", [_CVOID, _CI32, _CVOID, _CVOID])
        self._evaluate = self.module.fn(
            "NVSDK_NGX_D3D12_EvaluateFeature", [_CVOID, _CVOID, _CVOID, _CVOID])
        self._release_feature = self.module.fn("NVSDK_NGX_D3D12_ReleaseFeature", [_CVOID])
        self._destroy_parameters = None
        if not use_own_parameters and self.module.has_export("NVSDK_NGX_D3D12_DestroyParameters"):
            self._destroy_parameters = self.module.fn("NVSDK_NGX_D3D12_DestroyParameters", [_CVOID])

    def create_feature(self, feature_id):
        _log().status(f"NGX CreateFeature(feature {feature_id}) ->")
        out = ctypes.c_void_p()
        hr = self._create(self.gpu.list.ptr, ctypes.c_int32(feature_id),
                          self.params.ptr, ctypes.byref(out))
        _log().status(f"NGX CreateFeature <- hr=0x{hr & 0xFFFFFFFF:08X}")
        if hr != 1:
            code = hr & 0xFFFFFFFF
            known = {0xBAD0000B: "FeatureNotSupported",
                     0xBAD00003: "InvalidParameter",
                     0xBAD00004: "FeatureNotFound",
                     0xBAD0000C: "PlatformNotSupported"}
            name = known.get(code, "NGX error")
            raise DlssSrError(
                f"[ANTs] NGX CreateFeature(feature {feature_id}) failed "
                f"(0x{code:08X} = {name}). The installed runtime may not "
                "support this feature or parameter set.")
        self.handle = out.value
        if not self.handle:
            raise DlssSrError(
                f"[ANTs] NGX CreateFeature(feature {feature_id}) reported "
                "success but returned a NULL feature handle.")
        self.gpu.submit_and_wait()
        return self.handle

    def evaluate(self):
        first = not getattr(self, "_eval_logged", False)
        if first:
            _log().status("NGX EvaluateFeature -> (first frame)")
        hr = self._evaluate(self.gpu.list.ptr, ctypes.c_void_p(self.handle),
                            self.params.ptr, None)
        if first:
            self._eval_logged = True
            _log().status(f"NGX EvaluateFeature <- hr=0x{hr & 0xFFFFFFFF:08X}")
        if hr != 1:
            raise DlssSrError(
                f"[ANTs] NGX EvaluateFeature failed (0x{hr & 0xFFFFFFFF:08X}).")

    def close(self):
        if self.handle:
            try:
                self._release_feature(ctypes.c_void_p(self.handle))
            except Exception:
                pass
            self.handle = None
        if self._destroy_parameters and getattr(self, "_core_params", None):
            try:
                self._destroy_parameters(self._core_params.ptr)
            except Exception:
                pass
            self._core_params = None
        if self._own_parameters:
            self._own_parameters.close()
            self._own_parameters = None
        self.params = None
        self.module.close()

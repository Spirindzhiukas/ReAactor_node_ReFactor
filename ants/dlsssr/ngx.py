"""The NGX host: core/snippet loading, init, and the feature lifecycle.

One class serves both providers, because the API surface is identical:

- **driver core** (`_nvngx.dll` from the driver store) — used for SR: the
  core locates and owns `nvngx_dlss.dll` via the search-path list in
  ``NVSDK_NGX_FeatureCommonInfo``;
- **snippet direct** (`nvngx_dlssnr.dll` loaded as the module) — used for
  NR: that runtime exports the whole NVSDK_NGX_D3D12 API itself and is
  handed our own parameter object. Run 21 (Merserk's proven C++ host,
  probed from his binaries): his engine hard-requires the driver core
  ``_nvngx.dll`` in-process, inits the snippet via ``Init_Ext`` across API
  versions ``0x13..0x20``, and snippet hosts wire the snippet's
  ``Set{RuntimeParams,OverrideStatus,TelemetryEvaluate}Callback`` exports.
  Our NR path therefore preloads the core (presence for the snippet's
  evaluate path; ``local _nvngx.dll`` override first, then the driver
  store, exactly like his ``runtime\_nvngx.dll`` override), tries
  ``Init_Ext`` across ``0x13..0x20`` (classic 4-arg last resort), and can
  register no-op callbacks when ``ANTS_NR_RUNTIME_CALLBACKS=1``.

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

import struct
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


# On a hard native crash (access violation inside the NGX runtime), Windows
# fatal-exception handling prints every thread's Python stack to the console
# - that trace names the exact frame the crash happened in. Cheap insurance;
# enabled once when this package is first imported.
try:
    import faulthandler
    import sys as _sys
    faulthandler.enable(file=_sys.__stderr__, all_threads=True)
except Exception:
    pass
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
    """A directory we can write artifacts into (shim PE, staging, logs).

    Owner directive: everything DLSS-related lives under models/DLSS - so
    the primary location is models/DLSS/staged/ANTs/<tag>, falling back to
    %LOCALAPPDATA%\\ANTs\\<tag> (models dir read-only), the package dir,
    then the temp dir.
    """
    roots = []
    try:
        from ..dlssnr.discovery import DLSS_ROOT as _dlss_root
        roots.append(os.path.join(_dlss_root, "staged", "ANTs", tag))
    except Exception:
        pass
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
        self._core_handle = None
        self._cb_keep = []

        # Run 21 (Merserk probe): the PROVEN host of this snippet - his C++
        # engine - hard-REQUIRES the NGX driver core (_nvngx.dll) loaded
        # in-process ("Could not load NVIDIA NGX core _nvngx.dll..."), inits
        # the snippet via Init_Ext across API versions 0x13..0x20, and the
        # snippet exports Set{RuntimeParams,OverrideStatus,TelemetryEvaluate}
        # Callback that a snippet host is expected to wire. Our NR path did
        # NONE of the three: no core in-process, classic-Init-first, no
        # callbacks - runs 14-20 died at the first EvaluateFeature.
        if use_own_parameters:
            core_path = None
            local_core = os.path.join(os.path.dirname(module_path), "_nvngx.dll")
            if os.path.isfile(local_core):
                core_path = local_core  # explicit override next to the dll
            else:
                try:
                    core_path = locate_ngx_core()
                except DlssSrError:
                    core_path = None
            if core_path and os.path.normcase(os.path.abspath(core_path)) != \
                    os.path.normcase(os.path.abspath(module_path)):
                try:
                    # PRESENCE, not use: if the snippet resolves the core
                    # (GetModuleHandle) on its evaluate path, it must already
                    # be in-process; his engine treats it as a hard dependency.
                    self._core_handle = win32.load_library(core_path)
                    _log().status(f"[ANTs] NGX core preloaded: {core_path}")
                except Exception as exc:
                    _log().status(f"[ANTs] NGX core preload skipped: {exc}")
            if os.environ.get("ANTS_NR_RUNTIME_CALLBACKS"):
                # EXPERIMENT (runs 22-24, env-gated): register callbacks on
                # the snippet the way a snippet host would. Run 22/23 PROVED
                # the hook: inside the first evaluate the snippet calls
                # RuntimeParamsCallback(18, 8, paramsStruct*, "DLSSNR: color
                # (%d,%d %dx%d) ..." fmt*, ...varargs) - a logging/report
                # hook. Run 23's access violation was OUR OWN dumper: it
                # probed memory with kernel32 IsBadReadPtr (deprecated, racy
                # on volatile pages) and THAT call access-violated inside
                # kernel32; the vectored handler resumed, the second-chance
                # killed the process. The dumper now probes with
                # VirtualQuery, which cannot fault, skips scalar args, and
                # decodes printf-style format strings.
                #   ANTS_NR_CALLBACK_RET     stub return value (default 0)
                #   ANTS_NR_CALLBACK_DUMP=1  inspect the callback arguments
                ret = int(os.environ.get("ANTS_NR_CALLBACK_RET", "0") or "0")
                dump = bool(os.environ.get("ANTS_NR_CALLBACK_DUMP"))
                k32 = ctypes.windll.kernel32

                class _MBI(ctypes.Structure):
                    _fields_ = [("BaseAddress", _CVOID),
                                ("AllocationBase", _CVOID),
                                ("AllocationProtect", _CI32),
                                ("_pad", _CI32),
                                ("RegionSize", ctypes.c_size_t),
                                ("State", _CI32),
                                ("Protect", _CI32),
                                ("Type", _CI32)]

                def _readable(p, n):
                    mbi = _MBI()
                    if not k32.VirtualQuery(ctypes.c_void_p(p),
                                            ctypes.byref(mbi),
                                            ctypes.sizeof(mbi)):
                        return False
                    if mbi.State != 0x1000:      # MEM_COMMIT
                        return False
                    if mbi.Protect in (0x01, 0x0100):  # NOACCESS, GUARD
                        return False
                    end = (mbi.BaseAddress or 0) + mbi.RegionSize
                    return p + n <= end          # stay inside one region

                # 8 slots: x64 passes args 1-4 in registers and 5+ on the
                # stack; the snippet's callback is printf-style (varargs on
                # the stack), so slots 5-8 capture the first stack integers.
                # Floats ride in XMM registers and are invisible here - the
                # format string tells us they exist.
                cb_type = ctypes.CFUNCTYPE(ctypes.c_int, *([_CVOID] * 8))
                for name in ("NVSDK_NGX_SetRuntimeParamsCallback",
                             "NVSDK_NGX_SetOverrideStatusCallback",
                             "NVSDK_NGX_SetTelemetryEvaluateCallback"):
                    if not self.module.has_export(name):
                        continue
                    def _nop(*args, _name=name, _ret=ret):
                        _log().status(f"[ANTs] snippet callback fired: {_name}")
                        if dump:
                            for i, arg in enumerate(args):
                                p = int(arg or 0)
                                if p < 0x10000:  # scalar (feature id 18...)
                                    if p:
                                        _log().status(
                                            f"[ANTs]   arg{i} = {p}")
                                    continue
                                if not _readable(p, 64):
                                    _log().status(
                                        f"[ANTs]   arg{i}=0x{p:X} <unreadable>")
                                    continue
                                raw = ctypes.string_at(p, 64)
                                text = raw.split(b"\x00", 1)[0]
                                head = text[:min(len(text), 12)]
                                if len(text) >= 6 and all(
                                        32 <= b < 127 for b in head):
                                    _log().status(
                                        f"[ANTs]   arg{i}=0x{p:X} "
                                        f"'{text.decode('ascii', 'replace')}'")
                                else:
                                    words = struct.unpack("<8Q", raw)
                                    _log().status(
                                        f"[ANTs]   arg{i}=0x{p:X} " +
                                        " ".join(f"{w:016X}" for w in words))
                        return _ret
                    cb = cb_type(_nop)
                    setter = self.module.fn(name, [_CVOID])
                    setter(ctypes.cast(cb, _CVOID))
                    self._cb_keep.append(cb)  # pin the trampoline
                    _log().status(f"[ANTs] {name} <- callback registered "
                                  f"(returns {ret})")

        app_data = app_data_path or os.path.join(writable_cache_dir("appdata"), "logs")
        os.makedirs(app_data, exist_ok=True)

        # The runtime RETAINS the appDataPath / FeatureCommonInfo pointers
        # past Init and reads them lazily (first evaluate, log writes, model
        # loads) - freed temporaries here become use-after-free crashes
        # inside the first EvaluateFeature (rig run 16; DVT keeps them alive
        # for exactly this reason). Keep every init buffer on the session.
        self._init_keep = []
        app_data_wide = win32.wide(app_data)
        info = FeatureCommonInfo(list(search_paths) or [os.path.dirname(module_path)])
        self._init_keep += [app_data_wide, info, info._wide]
        init_ext = self.module.fn("NVSDK_NGX_D3D12_Init_Ext",
                                  [_CVOID, _CVOID, _CVOID, _CI32, _CVOID])
        init4 = self.module.fn("NVSDK_NGX_D3D12_Init",
                               [_CVOID, _CVOID, _CVOID, _CI32])

        _log().status(f"NGX init -> {os.path.basename(module_path)} (Init_Ext-first)")
        def try_init():
            # Init_Ext first for BOTH providers: DVT's rig-proven NR flow,
            # and run 21 - Merserk's proven host inits the snippet via
            # Init_Ext too ("DLSSNR snippet Init_Ext via caller shim").
            # Classic 4-arg stays as the last-resort fallback.
            versions = [NGX_VERSION_API]
            if use_own_parameters:
                # Merserk's engine spans 0x13..0x20 ("NGX core initialization
                # failed for API versions 0x13..0x20") - sweep the window.
                versions += [v for v in range(0x13, 0x21)
                             if v != NGX_VERSION_API]
            last = 0
            for sdk in versions:
                hr = init_ext(ctypes.c_void_p(app_id), app_data_wide,
                              gpu.device.ptr, ctypes.c_int32(sdk), info.ptr)
                if hr == 1:
                    if sdk != NGX_VERSION_API:
                        _log().status(
                            f"[ANTs] Init_Ext accepted sdkVersion 0x{sdk:X}")
                    return 1
                last = hr
            hr4 = init4(ctypes.c_void_p(app_id), app_data_wide,
                        gpu.device.ptr, ctypes.c_int32(NGX_VERSION_API))
            if hr4 == 1:
                _log().status(
                    "[ANTs] classic 4-arg Init accepted (Init_Ext rejected)")
                return 1
            return last  # last failure, for the error message

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

    def _first_evaluate_watchdog(self):
        """A daemon that reports a stuck first evaluate every 15s (the hang
        signature: server stops answering while this call never returns)."""
        import threading
        import time

        def watch():
            start = time.monotonic()
            while not self._eval_done.is_set():
                time.sleep(15)
                if self._eval_done.is_set():
                    return
                _log().status(
                    f"NGX EvaluateFeature STILL RUNNING for {time.monotonic() - start:.0f}s "
                    "- the runtime is not returning (hang, not a Python error).")

        thread = threading.Thread(target=watch, daemon=True)
        thread.start()
        return thread

    def evaluate(self):
        first = not getattr(self, "_eval_logged", False)
        if first:
            self._eval_done = __import__("threading").Event()
            self._first_evaluate_watchdog()
            from . import crashlog
            crash_file = os.path.join(writable_cache_dir("appdata"), "logs",
                                      "native-crash.log")
            crashlog.arm(crash_file)
            _log().status("NGX EvaluateFeature -> (first frame; crash black "
                          f"box: {crash_file}; params: "
                          + ", ".join(sorted(getattr(self.params, "store", {}))) + ")")
        hr = self._evaluate(self.gpu.list.ptr, ctypes.c_void_p(self.handle),
                            self.params.ptr, None)
        if first:
            self._eval_done.set()
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
        # Reverse load order: snippet (module) first, THEN the preloaded
        # driver core, then drop the callback trampolines.
        if self._core_handle:
            win32.free_library(self._core_handle)
            self._core_handle = None
        self._cb_keep = []

"""The NGX host: core/snippet loading, init, and the feature lifecycle.

One class serves every layout, because the API surface is identical:

- **core-owned session with a snippet feature provider** — the layout the NR
  path uses since run 28, and the only one that exists in the wild: the
  driver core (``_nvngx.dll``) owns the session and hands out the
  **capability parameter map**, while ``nvngx_dlssnr.dll`` (a *snippet*, not
  a standalone runtime) receives Create/Evaluate/Release through the caller
  shim. The community host of this runtime states the contract plainly: the
  snippet "expects the capability map returned by the core, which carries its
  callbacks and feature metadata" — a plain ``AllocateParameters`` map lets
  CreateFeature succeed and then fails inside evaluate, which is exactly the
  run-14..27c signature;
- **the SR runtime as the app-facing module** (`nvngx_dlss.dll`) — the route
  every DLSS application uses, and the only one that creates feature 1: its
  ``NVSDK_NGX_D3D12_*`` exports are the PUBLIC API in the PUBLIC argument
  order (``Init_Ext(appId, path, device, sdkVersion, featureInfo)``) and the
  runtime loads the driver core itself. Handing the driver core the SR folder
  in the search-path list is NOT enough: on the rig the core answered
  ``0xBAD0000B`` (no provider module for feature 1), and calling the runtime
  in the snippet order below faults inside ``Init_Ext`` — the swap puts the
  version constant 0x15 into the feature-info pointer slot (rig 29);
- **driver core alone** (`_nvngx.dll`) — kept as the SR fallback for sets
  where the core does own the SR implementation;
- **snippet direct** (`use_own_parameters=True`) — the legacy fallback where
  the snippet itself is the provider and is handed our own parameter object
  (the SWAPPED ``Init_Ext`` order). Kept for NR diagnostics and for an opt-in
  SR route (``ANTS_SR_SNIPPET_DIRECT=1``), NOT a default anywhere.

Every NGX call is routed through the generated caller shim (``shim.py``):
the target address is parked in a slot immediately before each call and the
thunk ``call``s it, so the return address the runtime sees lives inside the
shim image (the runtime rejects callers it does not accept with
``0xBAD00002``). ``fwd_init_ext`` additionally swaps the last two arguments
of ``Init_Ext`` for snippet builds — see ``_init_feature_module``. One shim
is NOT thread-safe (shared slots) — each session owns one.

Init order (technique reference DLT/DVT; header-verified): core session first
(``Init_ProjectID`` -> ``Init_Ext(appId, appDataPath, device, sdkVersion=0x15,
featureInfo)`` -> classic ``Init``), then the parameter map, then the snippet
provider's ``Init_Ext`` in its OWN argument order -> ``CreateFeature``
(cmdList, featureId, params, outHandle) -> submit the list -> ``EvaluateFeature``
per frame -> ``ReleaseFeature``. There is deliberately NO Shutdown1 call: the
current driver core faults after a feature exists and the D3D12 device is
released (DLT issue #75) — NGX is reclaimed at process exit, and we release
only the objects we own.
"""

import struct
import tempfile
import ctypes
import os

from .errors import DlssSrError
from .parameters import (CoreParameterObject, OwnParameterObject,
                         build_flat_api)

_CVOID = ctypes.c_void_p
_CI32 = ctypes.c_int32
from . import crashlog
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

# Console deployment marker: bumped with every host-layout change so the
# owner's console unambiguously says WHICH build ran (run-28 attempt #1 was
# diagnosed from a stack trace because the old file was still deployed).
HOST_BUILD = "2026-09-21.5"

FEATURE_SR = 1    # NVSDK_NGX_Feature_SuperSampling
FEATURE_NR = 18   # NVSDK_NGX_Feature_NeuralRendering ("CG2R")

APP_ID = 0x4E5254530001            # the customary 'NRTS' test-app id
# The app/project identity the NR snippet was registered under (the RenoDX
# carrier's project). NGX applications normally receive their own ids and a
# snippet may reject an unrelated project with FAIL_OutOfDate/FAIL_Denied even
# though D3D12 itself is healthy - the community host drives feature 18 with
# exactly these values (credited: ComfyUI-DLSS5-NR[-Linux], MIT). Override
# with the env knobs below if a vendor build wants a different session.
NR_APP_ID = 141959980
NR_PROJECT_ID = "53f803cc-a12f-4d69-90d5-19b7599cad19"
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
    """NVSDK_NGX_FeatureCommonInfo (40 bytes).

    Layout (SDK 0x14+): ``PathListInfo{wchar_t** Path, uint32 Length}`` at 0,
    ``InternalData`` at 16, ``LoggingInfo`` at 24 with the callback FIRST,
    then the minimum level, then the "disable other sinks" flag. The order
    inside LoggingInfo matters: a runtime that reads a function pointer as a
    logging enum (and vice versa) rejects the session (the community host hit
    ``FAIL_OutOfDate`` that way).

    A host callback here is how the runtime's own log lines reach us - the
    NR snippet logs its gate decisions through it, so keeping it wired (and
    the level VERBOSE) is the cheapest source of truth we have on the rig.
    """

    def __init__(self, search_paths, log_callback=None, log_level=2):
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
        # InternalData (16..24) stays zero.
        self.log_callback = log_callback
        if log_callback is not None:
            cb_addr = ctypes.cast(log_callback, ctypes.c_void_p)
            ctypes.memmove(ctypes.addressof(struct) + 24,
                           ctypes.byref(cb_addr), ctypes.sizeof(cb_addr))
            ctypes.memmove(ctypes.addressof(struct) + 32,
                           ctypes.byref(ctypes.c_int32(int(log_level))), 4)
            # DisableOtherLoggingSinks (36) stays zero: keep the runtime's own
            # console/file sinks too - they are the only ones that survive a
            # hard process death.

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


_SHIM_LOGGED = set()   # shim paths already announced (console triage hygiene)


def _tagged_call(call, name):
    """Wrap one NGX export so the crash box knows what was in flight.

    The black box's TERMINATION / C++-exception lines print this label. It is
    the difference between "something killed the process" (which is all the
    rig's lines said so far: libffi -> _ctypes -> python frames, no engine
    frame) and "the process died inside EvaluateFeature".
    """
    short = name.replace("NVSDK_NGX_D3D12_", "").replace("NVSDK_NGX_", "")

    def wrapper(*args):
        previous = crashlog.phase()
        crashlog.set_phase(short)
        try:
            return call(*args)
        finally:
            crashlog.set_phase(previous)

    return wrapper


class NgxModule:
    """A loaded NGX provider (driver core or snippet) + shim-routed calls."""

    def __init__(self, module_path, use_shim=True, forwarder_dir=None,
                 route_through_shim=True):
        self.path = None
        self.handle = None
        self.forwarder = None
        self._route = route_through_shim
        self._fwd_stub = None
        # The caller shim must be a module of its own whose code issues the
        # real `call` into the runtime (the return address has to point back
        # into it - that is the whole point of the shim). It is loaded BEFORE
        # the runtime so a same-base-name collision would be detected here
        # (the loader matches modules by base name).
        if use_shim:
            fwd_path = shim_mod.write_shim(
                forwarder_dir or writable_cache_dir("shim"),
                dll_name=shim_mod.shim_name())
            self._fwd_handle = win32.load_library(fwd_path)
            # Identity check: which FILE did Windows actually map under this
            # handle? (The loader matches modules by base name.)
            loaded_from = win32.get_module_filename(self._fwd_handle)
            if loaded_from and os.path.normcase(loaded_from) != os.path.normcase(
                    os.path.abspath(fwd_path)):
                win32.free_library(self._fwd_handle)
                self._fwd_handle = None
                raise DlssSrError(
                    "[ANTs] caller-shim module-name collision: our shim could not "
                    "be loaded as a distinct module.\n"
                    f"    We loaded: {fwd_path}"
                    f"\n    Windows returned: {loaded_from}"
                    f"\n    Another module named {os.path.basename(fwd_path)} is "
                    "already loaded in this process - restart ComfyUI so the "
                    "ANTs host loads first (ANTS_NR_SHIM_NAME renames ours).")
            _, exports = shim_mod.build_shim_dll()
            loaded = os.path.normcase(os.path.abspath(fwd_path))
            if loaded not in _SHIM_LOGGED:      # one line per process, not per
                _SHIM_LOGGED.add(loaded)        # module that routes through it
                _log().status(
                    f"[ANTs] caller shim loaded as {os.path.basename(fwd_path)} "
                    "(set ANTS_NR_SHIM_NAME to change the module name)")

            def resolve(name):
                try:
                    return win32.get_proc(self._fwd_handle, name)
                except DlssSrError:
                    # GetProcAddress refuses our hand-built export directory
                    # (the loader maps the image fine, it just won't parse
                    # it) - resolve via the build-time-known RVA instead.
                    return win32.export_address(self._fwd_handle, exports[name])

            set_slots = resolve("fwd_set_slots")
            # Raw thunk ADDRESS (analysis credit: Claude Sonnet 5) -
            # storing a ctypes callable here made fn() build a
            # Python-callback trampoline that mangled every NGX call's
            # arguments (garbage returns, 'Exception ignored' spam).
            self._fwd_stub = int(resolve("fwd_create"))
            # fwd_init_ext is the same thunk plus the snippet's
            # (common_info, version) argument swap.
            self._fwd_stub_init_ext = int(resolve("fwd_init_ext"))
            self._set_slots = win32.callable_at(
                set_slots, [_CVOID, _CVOID, _CVOID], None)
        else:
            self._fwd_handle = None
        self.handle = win32.load_library(module_path)
        self.path = module_path

    @property
    def shim_enabled(self):
        return self._fwd_stub is not None

    def address(self, name):
        return win32.get_proc(self.handle, name)

    def has_export(self, name):
        try:
            self.address(name)
            return True
        except DlssSrError:
            return False

    def fn(self, name, argtypes, restype=ctypes.c_int32, thunk="call"):
        # (the bound callable is wrapped at the end of this method)
        """Bindable NGX function, routed through the shim when enabled.

        thunk="init_ext" routes through fwd_init_ext, whose prototype is the
        PUBLIC header order ``(..., sdkVersion, commonInfo)`` while the
        snippet build receives ``(..., commonInfo, sdkVersion)``.
        """
        address = self.address(name)
        if self._fwd_stub is None or not self._route:
            # Direct bind: the driver core is called straight from this
            # process, exactly like any ordinary NGX application (the
            # reference hosts only route the SNIPPET through their helper -
            # the core's own Init/params entry points are bound directly
            # there too). The rig's NGX log showed the core recording the
            # caller module ("called from module nvngx.dll_ants.dll"), which
            # is a geometry no working host exhibits.
            return _tagged_call(win32.callable_at(address, argtypes, restype),
                                name)
        # Bind the thunk ADDRESS with the caller's real prototype (a
        # CFUNCTYPE over a raw int is a native call; over a callable it
        # would be a lossy Python trampoline - see _fwd_stub above).
        stub_addr = (self._fwd_stub_init_ext if thunk == "init_ext"
                     else self._fwd_stub)
        stub = win32.callable_at(stub_addr, argtypes, restype)
        assert ctypes.cast(stub, ctypes.c_void_p).value == stub_addr

        def routed(*args):
            self._set_slots(ctypes.c_void_p(address), None, None)
            return stub(*args)

        return _tagged_call(routed, name)

    def fn_raw(self, name, argtypes, restype=ctypes.c_int32):
        """The real export, un-routed (no shim, no caller-check geometry).

        Only for calls that must NOT come from a shim: never used on the
        feature path in the default configuration.
        """
        return _tagged_call(
            win32.callable_at(self.address(name), argtypes, restype), name)

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
                 use_own_parameters=False, app_data_path=None, use_shim=True,
                 feature_module_path=None, project_id=None,
                 preload_core=False, ngx_log=None):
        """module_path: the session OWNER (the driver core for NR/SR; the
        snippet itself only in the legacy snippet-direct fallback).

        feature_module_path: an NGX snippet that hosts the feature while the
        owner keeps the session and the parameter map (the layout every
        working host of nvngx_dlssnr.dll uses: core inits, core hands out the
        capability parameters, the snippet receives Create/Evaluate/Release
        through the caller shim). Its Init_Ext is called with the snippet's
        own argument order - see ``_init_feature_module``.
        """
        self.gpu = gpu
        _log().status(f"[ANTs] NR/SR host build {HOST_BUILD} - core-owned "
                      "session, guarded diagnostics")
        # E1 gate (runs 14-27c: deliberate kernel-direct kill surviving ALL
        # user-mode nets). ANTS_NR_USE_SHIM=0 = direct bind: no helper module
        # in the process at all, i.e. Merserk's host geometry as far as the
        # module table is concerned (nothing extra loaded, the name probes
        # fall through to the real driver dlls). If the death disappears
        # without the shim, the shim itself (its presence or its name) is
        # implicated rather than our parameter/ABI contract.
        if os.environ.get("ANTS_NR_USE_SHIM", "1") == "0" and use_shim:
            use_shim = False
            _log().status("[ANTs] E1 EXPERIMENT: shim disabled (ANTS_NR_USE_SHIM=0) "
                          "- direct bind, no nvngx.dll module in this process")
        # Caller geometry (reference-host parity): the SESSION OWNER is bound
        # directly - only a snippet provider goes through the caller shim.
        # ANTS_NR_CORE_VIA_SHIM=1 restores routing the core through the shim
        # (the geometry every run up to 2026-09-20 used).
        core_direct = os.environ.get("ANTS_NR_CORE_VIA_SHIM") != "1"
        self._owner_is_snippet = bool(use_own_parameters)
        self.module = NgxModule(
            module_path, use_shim=use_shim,
            forwarder_dir=writable_cache_dir("shim"),
            route_through_shim=self._owner_is_snippet or not core_direct)
        self._own_parameters = None
        self.params = None
        self.handle = None
        self._create = None
        self._evaluate = None
        self._release = None
        self._core_handle = None
        self._nvapi_handle = None
        self._cb_keep = []
        self._ngx_log = ngx_log
        self.feature_module = None
        self._destroy_parameters = None
        self._core_params = None
        # Instrumentation targets: [(module handle, label)] - sniffed by the
        # crash black box. Filled below; ALWAYS armed on the NR path.
        self._instrumented = []

        # Run 21 (Merserk probe): the PROVEN host of this snippet hard-REQUIRES
        # the NGX driver core (_nvngx.dll) in-process ("Could not load NVIDIA
        # NGX core _nvngx.dll...") and inits the snippet through a caller
        # helper. Our NR path did none of that before run 21.
        #
        # LEGACY snippet-direct route (use_own_parameters=True) and the SR
        # route (preload_core=True): the module being inited is a FEATURE
        # provider, not the core, so the driver core is loaded for PRESENCE
        # only - the provider's own Init/evaluate path resolves the core
        # in-process, and the reference host loads it FIRST for that reason.
        if use_own_parameters or preload_core:
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
                    self._core_handle = win32.load_library(core_path)
                    _log().status(f"[ANTs] NGX core preloaded: {core_path}")
                except Exception as exc:
                    _log().status(f"[ANTs] NGX core preload skipped: {exc}")

        # Core-owned session (the layout every working host of this snippet
        # uses): the CORE is the session owner and the snippet is loaded as
        # the feature provider - core first, then snippet, exactly like the
        # reference hosts (loader order and the runtime's core discovery both
        # depend on it).
        if feature_module_path:
            self.feature_module = NgxModule(
                feature_module_path, use_shim=use_shim,
                forwarder_dir=writable_cache_dir("shim"))
            _log().status(
                "[ANTs] NGX feature provider loaded: "
                f"{os.path.basename(feature_module_path)} (snippet Init_Ext ABI, "
                "feature calls routed through the caller shim)")

        # --- crash instrumentation ---------------------------------------
        # ARMED ALWAYS on EVERY route (run 27b lesson: nested inside an
        # env-gated experiment, one env deletion stripped every net and the
        # run went out instrument-free; and the SR route is NR-grade now).
        # Only ANTS_NR_TERMINATION_TRAP=0 opts out. Patches the NGX modules'
        # import tables (static terminates) and detours
        # ntdll!NtTerminateProcess (dynamic GetProcAddress terminates - the
        # snippet imports LoadLibraryW/GetProcAddress, so IATs alone are
        # dodgeable), and converts kernel-direct __fastfail (int 29h) sites to
        # breakpoints so the VEH names the check that fires.
        self._instrumented = [
            (self.module.handle, self._module_label("session owner")),
        ]
        if self.feature_module is not None:
            self._instrumented.append(
                (self.feature_module.handle,
                 "snippet " + os.path.basename(str(feature_module_path))))
        if self._core_handle:
            self._instrumented.append((self._core_handle, "driver core"))
        if os.environ.get("ANTS_NR_TERMINATION_TRAP", "1") != "0":
            from . import crashlog
            crashlog.install_termination_trap(self._instrumented)
            crashlog.install_ntdll_terminate_detour()
            if os.environ.get("ANTS_NR_INT29_TRAP", "1") != "0":
                crashlog.install_int29_trap(self._instrumented)
        if os.environ.get("ANTS_NR_RUNTIME_CALLBACKS"):
            self._register_runtime_callbacks(
                self.feature_module if self.feature_module is not None
                else self.module)

        app_data = app_data_path or os.path.join(writable_cache_dir("appdata"), "logs")
        os.makedirs(app_data, exist_ok=True)

        # The runtime RETAINS the appDataPath / FeatureCommonInfo pointers
        # past Init and reads them lazily (first evaluate, log writes, model
        # loads) - freed temporaries here become use-after-free crashes
        # inside the first EvaluateFeature (rig run 16; DVT keeps them alive
        # for exactly this reason). Keep every init buffer on the session.
        self._init_keep = []
        app_data_wide = win32.wide(app_data)
        self._log_cb = None
        if ngx_log or os.environ.get("ANTS_NR_NGX_LOG", "1") != "0":
            self._log_cb = self._make_log_callback(ngx_log)
        info = FeatureCommonInfo(list(search_paths) or [os.path.dirname(module_path)],
                                 log_callback=self._log_cb)
        self._init_keep += [app_data_wide, info, info._wide]
        project_id = project_id or (NR_PROJECT_ID if feature_module_path else None)
        # Reference-host pre-step: NvAPI is initialized before the NGX core
        # (the core asks NvAPI for the D3D12 device LUID during Init; the
        # Wine/vkd3d-NVAPI hosts MUST do this because their NvAPI is lazy,
        # and on Windows it is the driver's own no-op). Best-effort only.
        if feature_module_path or use_own_parameters:
            self._preload_nvapi()
        self._init_owner(project_id, app_id, app_data_wide, info, use_own_parameters)

        # ---- parameter map -------------------------------------------------
        # The NR snippet is NOT self-contained: it is created/evaluated with
        # the capability map the CORE hands out (it carries the feature
        # metadata and callbacks the snippet validates against). A plain
        # AllocateParameters map still lets CreateFeature succeed and then
        # fails inside evaluate - exactly the run-14..27c signature.
        if use_own_parameters:
            self._own_parameters = OwnParameterObject()
            self.params = self._own_parameters
            _log().status("[ANTs] NGX parameters: own snippet-direct object "
                          f"(backend={self._own_parameters.__class__.__name__})")
        else:
            self.params = self._core_params = self._open_core_parameters()

        # ---- feature provider ---------------------------------------------
        if self.feature_module is not None:
            self._init_feature_module(app_id, app_data_wide, info)
        self._bind_feature_lifecycle()

    # ------------------------------------------------------------ helpers
    def _module_label(self, fallback):
        name = os.path.basename(str(self.module.path or ""))
        return f"{fallback} {name}" if name else fallback

    def _make_log_callback(self, ngx_log):
        """NGX logging callback: (const char* message, level, source).

        The runtime keeps the pointer for the session, so it lives on the
        session. Message volume is capped - this is diagnostics, not a log
        sink (ANTS_NR_NGX_LOG=0 turns it off).

        The message is a ``const char*``: ctypes hands a CFUNCTYPE callback a
        plain int for it (not a bytes object), so the first implementations
        printed ``<unreadable>`` for every line. It is read with the
        VirtualQuery guard instead - a diagnostic callback must never fault
        inside the runtime's logging path.
        """
        limit = 400
        seen = {"n": 0}
        echo = os.environ.get("ANTS_NR_NGX_ECHO") == "1"

        def _read(ptr):
            if not ptr:
                return ""
            try:
                from .crashlog import _Mem, _kernel32
                k32 = _kernel32()
                if k32 is not None:
                    raw = _Mem(k32).read_some(int(ptr), 1024)
                else:
                    raw = ctypes.string_at(int(ptr), 1024)
            except Exception:
                return ""
            return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")

        def _cb(message, level, source):
            if seen["n"] >= limit:
                return
            seen["n"] += 1
            text = _read(message).rstrip("\n")
            if ngx_log:
                ngx_log(text, int(level or 0), int(source or 0))
            elif echo:
                # Off by default: run 30's console was ~90% NGX chatter, and
                # the same lines are already in the core's own log file, which
                # is what we ask for after a failure.
                _log().status(f"[NGX] {text}")
            if seen["n"] == limit:
                _log().status(
                    f"[ANTs] NGX log callback: {limit} lines "
                    + ("echoed" if echo else "captured into the core log")
                    + "; set ANTS_NR_NGX_ECHO=1 to see them on the console.")

        cb = ctypes.CFUNCTYPE(None, _CVOID, _CI32, _CI32)(_cb)
        self._cb_keep.append(cb)
        return cb

    def _init_owner(self, project_id, app_id, app_data_wide, info, legacy):
        """Init the session owner (the core - or the snippet, legacy route).

        Order (every working host of this runtime does the core first):
        ``Init_ProjectID`` (the route the RenoDX carrier uses; the snippet may
        reject an unrelated project with FAIL_OutOfDate/FAIL_Denied) ->
        ``Init_Ext`` (public header order) -> classic 4-arg ``Init``.
        """
        owner = self.module
        routed = "via the caller shim" if getattr(owner, "_route", True) \
            else "bound directly"
        _log().status(f"NGX init -> {os.path.basename(str(owner.path or ''))} "
                      f"({routed})")
        last = 0
        if legacy:
            # Legacy snippet-direct route: the OWNER is the snippet build
            # itself, so its Init_Ext needs the snippet argument order too.
            hr = self._init_snippet_module(owner, app_id, app_data_wide, info,
                                           os.path.basename(str(owner.path or "")))
            _log().status(f"[ANTs] snippet Init_Ext <- hr=0x{hr & 0xFFFFFFFF:08X}")
            if hr == 1:
                return 1
            raise DlssSrError(
                f"[ANTs] snippet Init_Ext failed (0x{hr & 0xFFFFFFFF:08X}) for "
                f"{os.path.basename(str(owner.path or ''))}.")

        if project_id and owner.has_export("NVSDK_NGX_D3D12_Init_ProjectID"):
            init_project = owner.fn(
                "NVSDK_NGX_D3D12_Init_ProjectID",
                [_CVOID, _CI32, _CVOID, _CVOID, _CVOID, _CI32, _CVOID])
            project_buf = ctypes.create_string_buffer(
                project_id.encode("ascii") + b"\x00")
            engine_buf = ctypes.create_string_buffer(b"ANTs 1.1.0\x00")
            self._init_keep += [project_buf, engine_buf]
            hr = init_project(project_buf, 0, engine_buf, app_data_wide,
                              self.gpu.device.ptr, ctypes.c_int32(NGX_VERSION_API),
                              info.ptr)
            _log().status(
                f"[ANTs] Init_ProjectID({project_id}) <- hr=0x{hr & 0xFFFFFFFF:08X}")
            if hr == 1:
                return 1
            last = hr

        init_ext = owner.fn("NVSDK_NGX_D3D12_Init_Ext",
                            [_CVOID, _CVOID, _CVOID, _CI32, _CVOID])
        versions = [NGX_VERSION_API]
        if legacy:
            # Merserk's engine spans 0x13..0x20 ("NGX core initialization
            # failed for API versions 0x13..0x20") - sweep the window.
            versions += [v for v in range(0x13, 0x21) if v != NGX_VERSION_API]
        for sdk in versions:
            hr = init_ext(ctypes.c_void_p(app_id), app_data_wide,
                          self.gpu.device.ptr, ctypes.c_int32(sdk), info.ptr)
            if hr == 1:
                if sdk != NGX_VERSION_API:
                    _log().status(f"[ANTs] Init_Ext accepted sdkVersion 0x{sdk:X}")
                return 1
            last = hr
        init4 = owner.fn("NVSDK_NGX_D3D12_Init",
                         [_CVOID, _CVOID, _CVOID, _CI32])
        hr = init4(ctypes.c_void_p(app_id), app_data_wide,
                   self.gpu.device.ptr, ctypes.c_int32(NGX_VERSION_API))
        if hr == 1:
            _log().status("[ANTs] classic 4-arg Init accepted (Init_Ext rejected)")
            return 1
        _log().status(f"NGX init <- failed (last hr=0x{last & 0xFFFFFFFF:08X})")
        raise DlssSrError(
            f"[ANTs] NGX Init failed (0x{last & 0xFFFFFFFF:08X}) for "
            f"{owner.path} - the runtime rejected the session. Check the "
            "driver version and the DLL set folder.")

    def _preload_nvapi(self):
        """NvAPI_Initialize before the NGX core Init - the reference host's
        first step (ANTS_NR_NVAPI=0 opts out; a failure is logged, never
        fatal: nvapi64.dll is absent on machines without a driver)."""
        if os.environ.get("ANTS_NR_NVAPI", "1") == "0":
            return
        try:
            handle = win32.load_library("nvapi64.dll")
        except Exception as exc:
            _log().status(f"[ANTs] NvAPI pre-step skipped: {exc}")
            return
        try:
            address = win32.get_proc(handle, "NvAPI_Initialize")
        except Exception:
            address = None
        if not address:
            # Harmless on Windows: the core reaches NVAPI itself (its log
            # shows "Found matching adapter with NVAPI physical GPU handle").
            # The pre-step exists for the Wine/vkd3d-NVAPI hosts, where the
            # compatibility layer is lazy.
            _log().status(
                "[ANTs] NvAPI pre-step: nvapi64.dll exports no "
                "NvAPI_Initialize here (harmless on Windows - the core "
                "resolves NVAPI on its own)")
            win32.free_library(handle)
            return
        self._nvapi_handle = handle
        try:
            init = win32.callable_at(address, [], ctypes.c_int32)
            hr = init()
            _log().status(f"[ANTs] NvAPI_Initialize <- 0x{hr & 0xFFFFFFFF:08X}")
        except Exception as exc:
            _log().status(f"[ANTs] NvAPI pre-step: NvAPI_Initialize failed: {exc}")

    def _open_core_parameters(self):
        """The core's capability map - the object feature 18 expects."""
        flat = build_flat_api(self.module)
        if flat:
            _log().status("[ANTs] NGX parameter C API present: using the flat "
                          "NVSDK_NGX_Parameter_Set* exports (ABI-stable; no "
                          "vtable slot guessing)")
        out = ctypes.c_void_p()
        hr = 0
        if self.module.has_export("NVSDK_NGX_D3D12_GetCapabilityParameters"):
            get_caps = self.module.fn(
                "NVSDK_NGX_D3D12_GetCapabilityParameters", [_CVOID])
            hr = get_caps(ctypes.byref(out))
            _log().status(
                f"[ANTs] GetCapabilityParameters <- hr=0x{hr & 0xFFFFFFFF:08X} "
                f"({hex(out.value or 0)})")
            if hr == 1 and out.value:
                getter = CoreParameterObject(out.value, flat_api=flat)
                if not flat:
                    _log().status(
                        "[ANTs] NGX parameter backend: vtable slots "
                        f"resource={getter._slots[0]} pointer={getter._slots[1]} "
                        f"int={getter._slots[2]} float={getter._slots[3]} "
                        "(ANTS_NR_PARAM_ABI=header switches to the public-header "
                        "mapping)")
                return getter
        if self.module.has_export("NVSDK_NGX_D3D12_AllocateParameters"):
            alloc = self.module.fn("NVSDK_NGX_D3D12_AllocateParameters", [_CVOID])
            out = ctypes.c_void_p()
            hr = alloc(ctypes.byref(out))
            _log().status(
                "[ANTs] capability map unavailable - AllocateParameters "
                f"<- hr=0x{hr & 0xFFFFFFFF:08X} (feature 18 may reject this map "
                "inside evaluate)")
            if hr == 1 and out.value:
                return CoreParameterObject(out.value, flat_api=flat)
        raise DlssSrError(
            "[ANTs] NGX parameter map allocation failed "
            f"(0x{hr & 0xFFFFFFFF:08X}): neither GetCapabilityParameters nor "
            "AllocateParameters succeeded on "
            f"{os.path.basename(str(self.module.path or ''))}.")

    def _init_snippet_module(self, module, app_id, app_data_wide, info, label):
        """Call a snippet build's Init_Ext in ITS argument order.

        Routing depends on the shim: with the shim the swap happens inside
        ``fwd_init_ext`` (so the return address stays in the shim image and
        the caller check passes), without it we bind the real export with the
        snippet's own prototype.
        """
        if module.shim_enabled:
            call = module.fn("NVSDK_NGX_D3D12_Init_Ext",
                             [_CVOID, _CVOID, _CVOID, _CI32, _CVOID],
                             thunk="init_ext")
            _log().status(
                f"[ANTs] snippet Init_Ext via caller shim (arg swap) -> {label}")
            return call(ctypes.c_void_p(app_id), app_data_wide,
                        self.gpu.device.ptr, ctypes.c_int32(NGX_VERSION_API),
                        info.ptr)
        call = module.fn("NVSDK_NGX_D3D12_Init_Ext",
                         [_CVOID, _CVOID, _CVOID, _CVOID, _CI32])
        _log().status(f"[ANTs] snippet Init_Ext direct (snippet ABI) -> {label}")
        return call(ctypes.c_void_p(app_id), app_data_wide,
                    self.gpu.device.ptr, info.ptr,
                    ctypes.c_int32(NGX_VERSION_API))

    def _init_feature_module(self, app_id, app_data_wide, info):
        """Init the snippet that hosts the feature.

        ``nvngx_dlssnr.dll`` (snippet build) takes
        ``Init_Ext(appId, path, device, FeatureCommonInfo*, sdkVersion)`` -
        the last two arguments are SWAPPED against the public header order
        (the SDK SR runtime wants the PUBLIC order instead: calling it with
        the swap faults reading address 0x15 - rig 29, ``sr.py``);
        and calling it the public way hands the runtime a version number where
        it expects a pointer (which it dereferences later, on the evaluate
        path). The shim's ``fwd_init_ext`` thunk performs the swap in native
        code so the return address stays inside the caller-shim image and the
        runtime's caller check keeps passing.
        """
        fn = self.feature_module
        label = os.path.basename(str(fn.path or ""))
        hr = self._init_snippet_module(fn, app_id, app_data_wide, info, label)
        _log().status(f"[ANTs] snippet Init_Ext <- hr=0x{hr & 0xFFFFFFFF:08X}")
        if hr != 1:
            raise DlssSrError(
                f"[ANTs] snippet Init_Ext failed (0x{hr & 0xFFFFFFFF:08X}) for "
                f"{label}. 0xBAD00002 means the runtime rejected the caller "
                "(the caller shim is required); a driver/FEATURE-mismatch "
                "otherwise.")

    def _bind_feature_lifecycle(self):
        module = self.feature_module or self.module
        self._create = module.fn("NVSDK_NGX_D3D12_CreateFeature",
                                 [_CVOID, _CI32, _CVOID, _CVOID])
        self._evaluate = module.fn("NVSDK_NGX_D3D12_EvaluateFeature",
                                   [_CVOID, _CVOID, _CVOID, _CVOID])
        self._release_feature = module.fn("NVSDK_NGX_D3D12_ReleaseFeature", [_CVOID])
        if self.params is not self._own_parameters and module.has_export(
                "NVSDK_NGX_D3D12_DestroyParameters"):
            self._destroy_parameters = module.fn(
                "NVSDK_NGX_D3D12_DestroyParameters", [_CVOID])

    def _register_runtime_callbacks(self, module):
        """EXPERIMENT (runs 22-24, env-gated, kept for A/Bs): register the
        snippet's own host callbacks."""
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
            if not module.has_export(name):
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
            setter = module.fn(name, [_CVOID])
            setter(ctypes.cast(cb, _CVOID))
            self._cb_keep.append(cb)  # pin the trampoline
            _log().status(f"[ANTs] {name} <- callback registered "
                          f"(returns {ret})")

    def create_feature(self, feature_id):
        _log().status(f"NGX CreateFeature(feature {feature_id}) ->")
        out = ctypes.c_void_p()
        hr = self._create(self.gpu.command_list().ptr, ctypes.c_int32(feature_id),
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
            names = getattr(self.params, "store", None) or getattr(
                self.params, "written", [])
            _log().status("NGX EvaluateFeature -> (first frame; crash black "
                          f"box: {crash_file}; params[{len(names)}]: "
                          + ", ".join(sorted(set(names))) + ")")
        hr = self._evaluate(self.gpu.command_list().ptr, ctypes.c_void_p(self.handle),
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
        if self.feature_module is not None:
            self.feature_module.close()
            self.feature_module = None
        self.module.close()
        # Reverse load order: snippet (module) first, THEN the preloaded
        # driver core, then drop the callback trampolines.
        if self._core_handle:
            win32.free_library(self._core_handle)
            self._core_handle = None
        self._nvapi_handle = None
        self._cb_keep = []

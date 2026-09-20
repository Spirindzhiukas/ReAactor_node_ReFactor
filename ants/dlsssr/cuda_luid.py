"""The CUDA side of "which adapter is this?" - device ordinals as LUIDs.

The neuroframe engine matches the D3D12 device it is handed against the CUDA
device it advertises **by adapter LUID**. A host that creates its D3D12 device
on the wrong adapter therefore gets "Could not create a D3D12 device matching
CUDA ordinal N by LUID" - and the old "DXGI adapter index == CUDA ordinal"
assumption is wrong in exactly the two cases that matter:

* a machine with more than one GPU (DXGI enumeration order is a display
  order, CUDA ordinals are a performance order - they need not agree), and
* ComfyUI launched with ``--cuda-device N`` / ``CUDA_VISIBLE_DEVICES``, which
  renumbers the CUDA ordinals but leaves DXGI enumeration untouched.

Route (the same one the reference host uses): the CUDA **driver** API from
nvcuda.dll - cuInit + cuDeviceGetCount/GetName/GetLuid. The CUDA runtime
(cudart64_*.dll, next to torch) is the fallback.

Deliberate limits - this module is called from inside ComfyUI's process:

* only identity queries are made (cuInit, count, name, LUID for one device);
  nothing is allocated, registered, copied or given a context, and no device
  other than the requested ordinal is queried;
* every failure is a *reason string*, never an exception - the caller decides
  what to log and how loudly.
"""

import ctypes
import glob
import os
import struct

CUDA_SUCCESS = 0
_COUNT = {"n": None}
_CUCOUNT = {"devices": None, "reason": ""}
_LUID_BYTES = 8


def format_luid(luid):
    """Print a LUID the way Windows tools and aimdo do: ``hi:lo`` in hex."""
    if not luid:
        return "?"
    lo, hi = luid
    return f"{hi:08x}:{lo:08x}"


def reset_cache():
    """Forget the cached device list (tests, and rigs that hot-plug a GPU)."""
    _COUNT["n"] = None
    _CUCOUNT["devices"] = None
    _CUCOUNT["reason"] = ""


def _load(name):
    """(library, reason) for a Windows DLL - never raises."""
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        return None, "ctypes.WinDLL is Windows-only (this host is not Windows)"
    try:
        return loader(name), ""
    except OSError as exc:
        return None, f"{name} could not be loaded ({exc.__class__.__name__})"


def _bind(lib, name, argtypes, restype=ctypes.c_int):
    """Bind one export; None when it is missing (an old driver)."""
    try:
        fn = getattr(lib, name)
    except AttributeError:
        return None
    fn.argtypes = argtypes
    fn.restype = restype
    return fn


def _driver_luid(ordinal):
    """(luid, name, reason) straight from nvcuda.dll."""
    lib, why = _load("nvcuda.dll")
    if lib is None:
        return None, "", why
    try:
        init = _bind(lib, "cuInit", [ctypes.c_uint])
        get_luid = _bind(lib, "cuDeviceGetLuid",
                         [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint),
                          ctypes.c_int])
        if init is None or get_luid is None:
            return None, "", "nvcuda.dll has no cuDeviceGetLuid (driver too old)"
        rc = init(0)
        if rc != CUDA_SUCCESS:
            return None, "", f"cuInit failed (CUDA error {rc})"
        name = _driver_name(lib, ordinal)
        buf = ctypes.create_string_buffer(_LUID_BYTES)
        mask = ctypes.c_uint(0)
        rc = get_luid(ctypes.cast(buf, ctypes.c_void_p), ctypes.byref(mask),
                      int(ordinal))
        if rc != CUDA_SUCCESS:
            return None, name, f"cuDeviceGetLuid({ordinal}) failed (CUDA error {rc})"
        return struct.unpack("<II", buf.raw), name, ""
    except (AttributeError, OSError, ValueError) as exc:
        return None, "", f"CUDA driver API unusable ({exc.__class__.__name__}: {exc})"


def _driver_name(lib, ordinal):
    """Best-effort device name; empty string when the export is missing."""
    try:
        get_name = _bind(lib, "cuDeviceGetName",
                         [ctypes.c_void_p, ctypes.c_int, ctypes.c_int])
        if get_name is None:
            return ""
        buf = ctypes.create_string_buffer(128)
        if get_name(ctypes.cast(buf, ctypes.c_void_p), 128, int(ordinal)) \
                != CUDA_SUCCESS:
            return ""
        return buf.value.decode("ascii", "replace")
    except (AttributeError, OSError, ValueError):
        return ""


def _cudart():
    """(cudaDeviceGetLuid, reason) from the CUDA runtime, if we can find it."""
    import torch  # noqa: F401 - only to locate torch/lib next to it
    lib_dir = os.path.join(os.path.dirname(torch.__file__), "lib")
    candidates = sorted(glob.glob(os.path.join(lib_dir, "cudart64_*.dll")))
    cuda_path = os.environ.get("CUDA_PATH", "")
    if cuda_path:
        candidates += sorted(glob.glob(
            os.path.join(cuda_path, "bin", "cudart64_*.dll")))
    if not candidates:
        return None, "no cudart64_*.dll next to torch and none under CUDA_PATH"
    lib, why = _load(os.path.basename(candidates[-1]))
    if lib is None:
        lib, why = _load(candidates[-1])
    if lib is None:
        return None, why
    fn = _bind(lib, "cudaDeviceGetLuid",
               [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint), ctypes.c_int])
    if fn is None:
        return None, f"{os.path.basename(candidates[-1])} has no cudaDeviceGetLuid"
    return fn, ""


def _runtime_luid(ordinal):
    """(luid, reason) via the CUDA runtime - the fallback route."""
    try:
        fn, why = _cudart()
    except Exception as exc:                     # torch missing or broken
        return None, f"cudart route unusable ({exc.__class__.__name__}: {exc})"
    if fn is None:
        return None, why
    try:
        buf = ctypes.create_string_buffer(_LUID_BYTES)
        mask = ctypes.c_uint(0)
        rc = fn(ctypes.cast(buf, ctypes.c_void_p), ctypes.byref(mask),
                int(ordinal))
        if rc != 0:
            return None, f"cudaDeviceGetLuid({ordinal}) failed (CUDA error {rc})"
        return struct.unpack("<II", buf.raw), ""
    except (AttributeError, OSError, ValueError) as exc:
        return None, f"CUDA runtime query failed ({exc.__class__.__name__}: {exc})"


def count():
    """(n, reason): how many CUDA devices this process can see.

    The cheapest CUDA query there is (cuInit + cuDeviceGetCount) and the one
    ComfyUI core itself makes at startup; it is what decides whether the
    multi-GPU CUDA bug (ComfyUI issue #15255) can apply to this process.
    """
    if _COUNT["n"] is not None:
        return _COUNT["n"], ""
    lib, why = _load("nvcuda.dll")
    if lib is None:
        return None, why
    try:
        init = _bind(lib, "cuInit", [ctypes.c_uint])
        get_count = _bind(lib, "cuDeviceGetCount", [ctypes.POINTER(ctypes.c_int)])
        if init is None or get_count is None:
            return None, "nvcuda.dll has no cuInit/cuDeviceGetCount"
        rc = init(0)
        if rc != CUDA_SUCCESS:
            return None, f"cuInit failed (CUDA error {rc})"
        total = ctypes.c_int(0)
        if get_count(ctypes.byref(total)) != CUDA_SUCCESS:
            return None, "cuDeviceGetCount failed"
        _COUNT["n"] = total.value
        return total.value, ""
    except (AttributeError, OSError, ValueError) as exc:
        return None, f"CUDA driver API unusable ({exc.__class__.__name__}: {exc})"


def device_luid(ordinal=0):
    """(luid, name, reason) for one CUDA ordinal.

    ``luid`` is ``(LowPart, HighPart)`` - the same order as
    DXGI_ADAPTER_DESC1.AdapterLuid - or None with ``reason`` explaining why.
    """
    luid, _name, why = _driver_luid(ordinal)
    if luid is not None:
        return luid, _name, ""
    if "not Windows" in why:
        return None, "", why
    runtime_luid, runtime_why = _runtime_luid(ordinal)
    if runtime_luid is not None:
        return runtime_luid, "", ""
    return None, "", f"{why}; runtime fallback: {runtime_why}"


def view():
    """(devices, reason): every CUDA device as ``(ordinal, name, luid)``.

    Used by the read-only rig collector (its own process, so the full
    enumeration is safe there) and by the multi-GPU advisory. Inside the pack
    prefer :func:`device_luid`, which touches only the one device.
    """
    if _CUCOUNT["devices"] is not None:
        return _CUCOUNT["devices"], _CUCOUNT["reason"]
    lib, why = _load("nvcuda.dll")
    if lib is None:
        return [], why
    try:
        init = _bind(lib, "cuInit", [ctypes.c_uint])
        count = _bind(lib, "cuDeviceGetCount", [ctypes.POINTER(ctypes.c_int)])
        if init is None or count is None:
            return [], "nvcuda.dll has no cuInit/cuDeviceGetCount"
        rc = init(0)
        if rc != CUDA_SUCCESS:
            return [], f"cuInit failed (CUDA error {rc})"
        total = ctypes.c_int(0)
        if count(ctypes.byref(total)) != CUDA_SUCCESS:
            return [], "cuDeviceGetCount failed"
        devices = []
        for ordinal in range(total.value):
            luid, name, _why = _driver_luid(ordinal)
            devices.append((ordinal, name, luid))
        # Only a complete enumeration is worth caching - a failure is retried
        # (the driver may have been mid-restart when we asked).
        _CUCOUNT["devices"] = devices
        _CUCOUNT["reason"] = ""
        return devices, ""
    except (AttributeError, OSError, ValueError) as exc:
        return [], f"CUDA driver API unusable ({exc.__class__.__name__}: {exc})"


def describe(devices):
    """One line naming every device, for a log or a report."""
    parts = []
    for ordinal, name, luid in devices:
        label = name or f"CUDA device {ordinal}"
        parts.append(f"{ordinal}: {label} (LUID {format_luid(luid)})")
    return "; ".join(parts)

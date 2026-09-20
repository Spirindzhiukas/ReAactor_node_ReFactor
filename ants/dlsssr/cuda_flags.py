"""The CUDA context FLAGS the neuroframe engine demands for GPU interop.

Rig 21:47 (the engine's own words, quoted by the node):

    DLSS5 processing via host staging (CPU) - engine has no CUDA interop:
    engine reports CUDA interoperability unavailable: active CUDA primary
    context does not use FFmpeg blocking-sync flags

That is the engine handing over the diagnosis: it joins the process's CUDA
**primary context** (the one PyTorch created) and it refuses the zero-copy
device-pointer path unless that context carries ``CU_CTX_SCHED_BLOCKING_SYNC``
(0x04) - the flag FFmpeg sets when it builds a context for D3D<->CUDA interop.
PyTorch never sets it, so the node falls back to host staging; the owner's own
numbers, same machine, same DLL, are ~15 s per frame on the CPU path against
OreX's 2.65 s zero-copy run.

What can be done from inside a running ComfyUI process, in order:

1. ``cudaSetDeviceFlags(cudaDeviceScheduleBlockingSync)`` - legal only BEFORE
   the context exists. Once torch created it, the call answers
   ``cudaErrorSetOnActiveProcess`` (708) and nothing changes that way.
2. ``cuDevicePrimaryCtxSetFlags(device, 0x04)`` - carries the same
   restriction, and says so with CUDA_ERROR_PRIMARY_CONTEXT_ACTIVE.
3. ``cuCtxSetFlags(0x04)`` - present on current drivers; whether it accepts an
   ALREADY ACTIVE context is the open question, so its return code is
   reported instead of guessed.

The pack calls :func:`arm_early` at import (the earliest moment our code runs
inside ComfyUI) and prints the outcome; the probe reports the same thing
standalone. Nothing here allocates, registers or copies anything, and nothing
raises - every failure is a reason string, exactly like ``cuda_luid``.
"""

import ctypes

from . import cuda_luid

CUDA_SUCCESS = 0
CU_CTX_SCHED_AUTO = 0x00
CU_CTX_SCHED_SPIN = 0x01
CU_CTX_SCHED_YIELD = 0x02
CU_CTX_SCHED_BLOCKING_SYNC = 0x04
BLOCKING_SYNC = CU_CTX_SCHED_BLOCKING_SYNC
SCHED_FLAG_MASK = 0x0F

CUDA_ERROR_NAMES = {
    0: "CUDA_SUCCESS",
    1: "CUDA_ERROR_INVALID_VALUE",
    100: "CUDA_ERROR_NO_DEVICE",
    201: "CUDA_ERROR_INVALID_CONTEXT",
    400: "CUDA_ERROR_INVALID_HANDLE",
    500: "CUDA_ERROR_NOT_FOUND",
    708: "CUDA_ERROR_PRIMARY_CONTEXT_ACTIVE (cudaErrorSetOnActiveProcess)",
    801: "CUDA_ERROR_NOT_SUPPORTED",
}

_STATE = {"tried": False, "ok": False, "detail": ""}


def error_name(rc):
    """``CUDA_SUCCESS`` / ``CUDA_ERROR_...`` / the raw number."""
    return CUDA_ERROR_NAMES.get(int(rc), f"CUDA error {int(rc)}")


def describe_flags(flags):
    """``0x04 (blocking-sync)`` for a ``cuCtxGetFlags`` value."""
    if flags is None:
        return "unreadable"
    sched = {CU_CTX_SCHED_AUTO: "auto/spin",
             CU_CTX_SCHED_SPIN: "spin",
             CU_CTX_SCHED_YIELD: "yield",
             CU_CTX_SCHED_BLOCKING_SYNC: "blocking-sync"}.get(
                 int(flags) & SCHED_FLAG_MASK, "unknown scheduling")
    return f"0x{int(flags):02X} ({sched})"


def ctx_flags():
    """(flags, reason) for the calling thread's current CUDA context."""
    lib, why = cuda_luid._load("nvcuda.dll")
    if lib is None:
        return None, why
    fn = cuda_luid._bind(lib, "cuCtxGetFlags",
                         [ctypes.POINTER(ctypes.c_uint)], ctypes.c_int)
    if fn is None:
        return None, "nvcuda.dll exports no cuCtxGetFlags"
    out = ctypes.c_uint(0)
    rc = fn(ctypes.byref(out))
    if rc != CUDA_SUCCESS:
        return None, f"cuCtxGetFlags -> {error_name(rc)}"
    return int(out.value), ""


def _try_runtime_flag():
    """Route 1: ``cudaSetDeviceFlags(0x04)`` via the CUDA runtime."""
    lib, path, why = cuda_luid.cudart_library()
    if lib is None:
        return None, f"cudaSetDeviceFlags unavailable ({why})"
    fn = cuda_luid._bind(lib, "cudaSetDeviceFlags", [ctypes.c_uint],
                         ctypes.c_int)
    if fn is None:
        return None, ("cudaSetDeviceFlags not exported by "
                      f"{path.rsplit(chr(92), 1)[-1]}")
    rc = fn(ctypes.c_uint(BLOCKING_SYNC))
    return rc, f"cudaSetDeviceFlags(0x04) -> {error_name(rc)}"


def _try_primary_ctx_flag(device=0):
    """Route 2: ``cuDevicePrimaryCtxSetFlags(device, 0x04)``."""
    lib, why = cuda_luid._load("nvcuda.dll")
    if lib is None:
        return None, f"cuDevicePrimaryCtxSetFlags unavailable ({why})"
    fn = cuda_luid._bind(lib, "cuDevicePrimaryCtxSetFlags",
                         [ctypes.c_int, ctypes.c_uint], ctypes.c_int)
    if fn is None:
        return None, "nvcuda.dll exports no cuDevicePrimaryCtxSetFlags"
    rc = fn(ctypes.c_int(int(device)), ctypes.c_uint(BLOCKING_SYNC))
    return rc, (f"cuDevicePrimaryCtxSetFlags({int(device)}, 0x04) -> "
                f"{error_name(rc)}")


def _try_ctx_set_flags():
    """Route 3: ``cuCtxSetFlags(0x04)`` on the *current* context."""
    lib, why = cuda_luid._load("nvcuda.dll")
    if lib is None:
        return None, f"cuCtxSetFlags unavailable ({why})"
    fn = cuda_luid._bind(lib, "cuCtxSetFlags", [ctypes.c_uint], ctypes.c_int)
    if fn is None:
        return None, "nvcuda.dll exports no cuCtxSetFlags (older driver)"
    rc = fn(ctypes.c_uint(BLOCKING_SYNC))
    return rc, f"cuCtxSetFlags(0x04) -> {error_name(rc)}"


def set_blocking_sync(device=0):
    """Try every route to the blocking-sync flag.

    Returns ``(ok, lines)``: ``ok`` is True when ``cuCtxGetFlags`` reads the
    flag back set (or the runtime call succeeded before any context existed),
    and ``lines`` is the human-readable step-by-step log the pack prints.
    """
    lines = []
    before, why_before = ctx_flags()
    lines.append("CUDA context flags before: "
                 + (describe_flags(before) if before is not None
                    else f"unreadable ({why_before})"))
    if before is not None and before & BLOCKING_SYNC:
        lines.append("the engine's blocking-sync condition is ALREADY "
                     "satisfied - the CUDA path should be available")
        return True, lines

    routes = (("1", _try_runtime_flag), ("2", _try_primary_ctx_flag),
              ("3", _try_ctx_set_flags))
    codes = []
    for number, route in routes:
        try:
            rc, detail = route() if number != "2" else route(device)
        except Exception as exc:                  # never fatal
            rc, detail = None, f"{route.__name__} raised {exc.__class__.__name__}"
        lines.append(f"{number}. {detail}")
        if rc is not None:
            codes.append(int(rc))
        after, _why = ctx_flags()
        if rc == CUDA_SUCCESS and (after is None or after & BLOCKING_SYNC):
            lines.append(f"   context flags after: {describe_flags(after)}")
            return True, lines
    after, why_after = ctx_flags()
    lines.append("CUDA context flags after: "
                 + (describe_flags(after) if after is not None
                    else f"unreadable ({why_after})"))
    if 708 in codes:
        lines.append(
            "the flag could NOT be armed: the CUDA context already exists "
            "(cudaErrorSetOnActiveProcess / CUDA_ERROR_PRIMARY_CONTEXT_ACTIVE) "
            "and this driver refused to change its scheduling policy "
            "afterwards, so the engine keeps refusing the zero-copy path. "
            "Report this block - it is the whole answer to why GPU "
            "acceleration is off.")
    else:
        lines.append(
            "the flag could NOT be armed (see the return codes above); the "
            "engine keeps refusing the zero-copy path. Report this block.")
    return False, lines


def summary():
    """One line for the node log: the flags, and what the engine wants."""
    flags, why = ctx_flags()
    if flags is None:
        return (f"CUDA context flags unreadable ({why}); the engine wants "
                "blocking-sync (0x04)")
    if flags & BLOCKING_SYNC:
        return (f"CUDA context flags {describe_flags(flags)} - satisfies the "
                "engine's blocking-sync requirement")
    return (f"CUDA context flags {describe_flags(flags)} - the engine wants "
            "blocking-sync (0x04); see the 'CUDA context flags before' block "
            "in this log for what each arming route answered")


def arm_early(force=False):
    """(ok, detail) - called once at pack import; never raises."""
    if _STATE["tried"] and not force:
        return _STATE["ok"], _STATE["detail"]
    _STATE["tried"] = True
    try:
        ok, lines = set_blocking_sync()
    except Exception as exc:                      # never break the import
        ok, lines = False, [f"CUDA flag arming failed ({exc.__class__.__name__}: {exc})"]
    detail = "\n".join(lines)
    _STATE.update(ok=ok, detail=detail)
    return ok, detail


def log_early(logger=None):
    """Arm (once) and put the result in the node log. Never raises."""
    try:
        ok, detail = arm_early()
        if logger is None:
            from ..log import dlss_logger as logger
        if ok:
            logger.status("[ANTs] CUDA interop: %s", detail.splitlines()[-1])
        else:
            logger.warning("[ANTs] CUDA interop is OFF for this process - %s",
                           detail)
    except Exception:
        pass

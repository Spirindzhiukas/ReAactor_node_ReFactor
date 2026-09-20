"""Why does this process refuse a UAV-capable D3D12 texture?

Rig history (2026-09-20), same pack, same DLLs, same machine:

  * 21:52 native run, native node ONLY in the process:
      'nr output' RGBA16F **with ALLOW_UNORDERED_ACCESS was created fine**,
      NGX CreateFeature(18) succeeded, EvaluateFeature ran.
  * 23:32 / 23:46 / 23:48 native runs, where the LEGACY engine (its CUDA
      zero-copy path) had run in the SAME ComfyUI process a few seconds
      earlier:
      CreateCommittedResource('nr output', ALLOW_UNORDERED_ACCESS) answered
      E_INVALIDARG, GetDeviceRemovedReason said "device present and healthy",
      and a plain (no-UAV) texture in the same process was created fine.

So the open question is not "is our resource description valid" (it demonstrably
is - it worked in a clean process) but **what a process-global state does to the
driver's UAV allocations**. This probe answers it in four short phases, using the
pack's own D3D12 code path (no duplicated bindings, no guessing):

  A. fresh device, feature level 11_0 (what the pack asks for today)
  B. fresh device, feature level 12_0 (what the proven reference host asks for)
  C. fresh device AFTER plain CUDA work in this process
     (cuInit + cuCtxCreate + 512 MiB cuMemAlloc + memset + free - the cheapest
     reproduction of "the legacy engine ran here")
  D. fresh device AFTER the already-staged legacy engine was initialized
     (only when a staged runtime with the helper pair is already on disk - this
     probe never stages or writes anything)

Verdict lines are printed for the owner; the exit code is:

  0  UAV creation worked in every phase that ran
  10 REPRODUCED: works fresh, fails after CUDA work in the same process
  11 UAV creation failed even on a FRESH device (not a CUDA-in-process issue)
  12 only one feature level fails - a feature-level finding
  2  not Windows / no usable D3D12 device

READ-ONLY: this tool creates GPU resources, releases them and exits. It writes
nothing to the repo, to models/DLSS or to the staging folder, and it does not
touch ComfyUI.
"""

import ctypes
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

# The staged-runtime scan below is read-only; declare it so nobody mistakes the
# imports for a staging action.
_STAGED_HINT = "models/DLSS/staged"

CUDA_ALLOC_BYTES = 512 * 1024 * 1024
PROBE_W = 256
PROBE_H = 256


def _line(prefix, text):
    print(f"{prefix} {text}", flush=True)


def _cuda_work():
    """(ok, detail): the cheapest reproduction of 'CUDA did real work here'."""
    from ants.dlsssr import cuda_luid
    lib, why = cuda_luid._load("nvcuda.dll")
    if lib is None:
        return False, f"nvcuda.dll unavailable ({why})"
    cu_init = cuda_luid._bind(lib, "cuInit", [ctypes.c_uint])
    cu_ctx_create = cuda_luid._bind(
        lib, "cuCtxCreate_v2",
        [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint, ctypes.c_int])
    cu_mem_alloc = cuda_luid._bind(
        lib, "cuMemAlloc_v2",
        [ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t])
    cu_memset = cuda_luid._bind(lib, "cuMemsetD8_v2",
                                [ctypes.c_uint64, ctypes.c_ubyte,
                                 ctypes.c_size_t])
    cu_mem_free = cuda_luid._bind(lib, "cuMemFree_v2", [ctypes.c_uint64])
    missing = [name for name, fn in (("cuInit", cu_init),
                                     ("cuCtxCreate_v2", cu_ctx_create),
                                     ("cuMemAlloc_v2", cu_mem_alloc))
               if fn is None]
    if missing:
        return False, "nvcuda.dll lacks " + ", ".join(missing)
    rc = cu_init(ctypes.c_uint(0))
    if rc != 0:
        return False, f"cuInit -> CUDA error {rc}"
    ctx = ctypes.c_void_p()
    rc = cu_ctx_create(ctypes.byref(ctx), ctypes.c_uint(0), ctypes.c_int(0))
    if rc != 0:
        return False, f"cuCtxCreate_v2 -> CUDA error {rc}"
    ptr = ctypes.c_uint64(0)
    rc = cu_mem_alloc(ctypes.byref(ptr), ctypes.c_size_t(CUDA_ALLOC_BYTES))
    if rc != 0:
        return False, f"cuMemAlloc_v2({CUDA_ALLOC_BYTES}) -> CUDA error {rc}"
    if cu_memset is not None:
        cu_memset(ptr, ctypes.c_ubyte(0),
                  ctypes.c_size_t(CUDA_ALLOC_BYTES))
    if cu_mem_free is not None:
        cu_mem_free(ptr)
    return True, (f"cuInit + cuCtxCreate + cuMemAlloc/memset/free "
                  f"{CUDA_ALLOC_BYTES // (1024 * 1024)} MiB OK "
                  f"(context left alive, as the legacy engine does)")


def _staged_legacy_init():
    """(ok, detail): initialize the ALREADY-staged legacy engine, read-only."""
    models = os.environ.get("ANTS_DLSS_MODELS", "")
    staged_root = os.path.join(models, "staged") if models else ""
    dirs = []
    if staged_root and os.path.isdir(staged_root):
        for name in sorted(os.listdir(staged_root)):
            path = os.path.join(staged_root, name)
            if (os.path.isfile(os.path.join(path, "nvngx_dlssnr.dll"))
                    and os.path.isfile(os.path.join(path,
                                                    "neuroframe_engine.dll"))):
                dirs.append(path)
    if not dirs:
        return False, (f"no staged runtime with the helper pair under "
                       f"{staged_root or 'models/DLSS/staged'} - skipped "
                       "(this probe never stages anything itself)")
    from ants.dlssnr.core import DLSSStandaloneManager
    manager = DLSSStandaloneManager(dirs[-1])
    try:
        manager.initialize(0)
    except Exception as exc:
        return False, f"legacy engine initialize failed ({exc})"
    return True, f"legacy engine initialized from {dirs[-1]}"


def _uav_probe(tag, feature_level):
    """Create a FRESH device (at ``feature_level``) and try a UAV texture."""
    from ants.dlsssr import d3d12
    result = {"tag": tag, "ok": None, "why": "", "status": "",
              "device": None}
    try:
        _factory, adapters = d3d12.enumerate_adapters()
        info, reason = d3d12.pick_adapter(adapters, 0)
        device = d3d12.D3D12Device.create(info.ptr if info else None,
                                          feature_level)
        ctx = d3d12.GpuContext(device, adapter_index=info.index if info else 0,
                               adapter=info, ordinal=0, pick_reason=reason)
    except Exception as exc:
        result["why"] = f"device creation failed ({exc})"
        return result
    result["status"] = ctx.device_status()[2]
    try:
        texture = device.create_texture2d(
            PROBE_W, PROBE_H, d3d12.DXGI_FORMAT_R16G16B16A16_FLOAT,
            label=f"probe uav ({tag})")
        result["ok"] = True
        result["why"] = (f"UAV texture {PROBE_W}x{PROBE_H} RGBA16F created "
                         f"(flags ALLOW_UNORDERED_ACCESS)")
        texture.release()
    except Exception as exc:
        result["ok"] = False
        result["why"] = str(exc).strip()
    if not ctx.dead:
        ctx.close()
    return result


def _report(result, feature_level):
    flag = {True: "[OK]  ", False: "[FAIL]", None: "[SKIP]"}.get(result["ok"])
    _line(flag, f"{result['tag']} (feature level 0x{feature_level:04X}): "
               f"{result['why']}")
    if result["status"]:
        _line("      ", f"device status right after: {result['status']}")


def main():
    print("=== ANTs D3D12 UAV probe - why is a UAV texture refused? ===")
    print(f"python   : {sys.version.split()[0]}")
    if os.name != "nt":
        _line("[SKIP]", "this host is not Windows - the probe needs D3D12.")
        return 2
    from ants.dlsssr import d3d12, cuda_flags, cuda_luid
    _factory, adapters = d3d12.enumerate_adapters()
    print("adapter  : " + (adapters[0].describe() if adapters else "none"))
    total, why = cuda_luid.count()
    print(f"CUDA     : {total} device(s) visible ({why or 'ok'})")
    print(f"ctx flags: {cuda_flags.summary()}")
    print()

    results = []
    for tag, level in (("phase A - fresh device", 0xB000),
                       ("phase B - fresh device", 0xC000)):
        result = _uav_probe(tag, level)
        results.append(result)
        _report(result, level)

    cuda_ok, cuda_detail = _cuda_work()
    _line("[INFO]" if cuda_ok else "[SKIP]", f"phase C setup: {cuda_detail}")
    if cuda_ok:
        result = _uav_probe("phase C - after plain CUDA work", 0xB000)
        results.append(result)
        _report(result, 0xB000)

    legacy_ok, legacy_detail = _staged_legacy_init()
    _line("[INFO]" if legacy_ok else "[SKIP]", f"phase D setup: {legacy_detail}")
    if legacy_ok:
        result = _uav_probe("phase D - after the legacy engine ran", 0xB000)
        results.append(result)
        _report(result, 0xB000)

    print()
    fresh = [r for r in results if r["tag"].startswith("phase A")
             or r["tag"].startswith("phase B")]
    after = [r for r in results if r["tag"].startswith(("phase C", "phase D"))]
    fresh_ok = [r for r in fresh if r["ok"]]
    fresh_bad = [r for r in fresh if r["ok"] is False]
    after_bad = [r for r in after if r["ok"] is False]

    if fresh_bad and not fresh_ok:
        print("VERDICT: a FRESH device already refuses UAV textures in this "
              "process, so the legacy engine is NOT the trigger. Send this "
              "whole report.")
        return 11
    if fresh_bad and fresh_ok:
        print("VERDICT: the feature level decides it - one of A/B works and "
              "the other does not. Send this whole report (the pack can "
              "retry the device at the working level).")
        return 12
    if fresh_ok and after_bad:
        print("VERDICT: REPRODUCED. UAV textures work on a fresh device and "
              "FAIL after CUDA work in the same process, so the legacy "
              "engine's CUDA path is what breaks the native host. Until this "
              "is fixed: run the native node in a FRESH ComfyUI process and "
              "do not run the legacy node in it. Send this whole report.")
        return 10
    if fresh_ok and not after:
        print("VERDICT: no reproduction here - UAV textures work in this "
              "process. If the native node still refuses one, send this "
              "report plus the console: the difference is then something the "
              "probe does not do yet (a second NGX session, the staged "
              "runtime, or the frame size).")
        return 0
    print("VERDICT: no reproduction - every phase created its UAV texture.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:                      # never lose the report
        print(f"[FAIL] the probe itself failed: {exc.__class__.__name__}: {exc}")
        raise SystemExit(2)

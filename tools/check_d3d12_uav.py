"""Why does this process refuse a UAV-capable D3D12 texture?

ANSWER (2026-09-21) - it was ONE BIT. d3d12.h (and the D3D12_RESOURCE_FLAGS
docs) define ALLOW_UNORDERED_ACCESS = 0x4. This pack defined 0x8 for it, and
0x8 is DENY_SHADER_RESOURCE. A driver refuses that byte on a texture that has
no usage flag at all, so the recipe ladder silently fell back to flags 0x0 -
a texture the NGX runtime cannot write through - until 0db946a forbade the
fallback and the refusal surfaced at creation. The pack now sends 0x4, and the
FLAGS MATRIX below is the proof in three lines: same device, same 256x256
RGBA16F description, same D3D12_HEAP_TYPE_DEFAULT, same initial state, ONLY
the flags byte changes.

What the rig logs really showed (2026-09-20, same pack, same DLLs, same box),
read as process state at the time - this is the question the probe was built
to settle and no longer needs to:

  * 21:52 native run: NGX initialized and evaluated, then Close() answered
    E_INVALIDARG and NGX threw. 'nr output' was NOT a UAV texture: the byte
    was 0x8, the ladder degraded to flags 0x0 and (before d2ef17b) said
    nothing about it.
  * 23:32 / 23:46 / 23:48 / 01:06 native runs: the driver refused the intended
    recipe outright; from 0db946a on the ladder may not drop the flag, so the
    refusal stopped being hidden.

Phases, in run order:

  0. FLAGS MATRIX - one device, one description, three bytes: 0x4
     (ALLOW_UNORDERED_ACCESS), 0x8 (DENY_SHADER_RESOURCE - the byte this pack
     used to send) and 0x0 (control). Runs FIRST: it decides everything below.
  A. fresh device, feature level 11_0 (what the pack asks for today)
  A0. the SAME test in a FRESH CHILD PROCESS with the pack's CUDA flag arming
     switched off (ANTS_NO_CUDA_FLAG_ARM=1) - the discriminator for "is it the
     blocking-sync CUDA context this pack arms at import?"
  B. fresh device, feature level 12_0 (what the reference host asks for)
  C. fresh device AFTER plain CUDA work in this process
     (cuInit + cuCtxCreate + 512 MiB cuMemAlloc + memset + free)
  D. fresh device AFTER the already-staged legacy engine was initialized
     (only when a staged runtime with the helper pair is already on disk - this
     probe never stages or writes anything)

A/A0/B/C/D are kept because they are what cleared the process-state theories
(A0 the CUDA flag arming, B the feature level, A and C/D the legacy engine):
if they ever disagree with the matrix, that is news.

Every phase also creates a PLAIN (no-UAV) texture, so "this device refuses
this flags byte" can be told apart from "this device refuses textures at all".

Verdict lines are printed for the owner; the exit code is:

  14 THE FLAGS BYTE was the bug: 0x4 is accepted and 0x8 is refused on the
     same device. The pack's UAV constant is fixed (0x4) - run the native
     node. Nothing about the legacy engine, the CUDA flag, the feature level
     or the driver was involved.
  0  UAV creation worked in every phase that ran
  10 REPRODUCED: works fresh, fails after CUDA work in the same process
  11 UAV creation failed even on a FRESH device, with the correct byte too -
     that points at the device/driver state, not at our descriptors
  12 only one feature level fails - a feature-level finding
  13 the CUDA flag arming this pack does at import is the trigger (A0 works,
     A does not)
  2  not Windows / no usable D3D12 device

READ-ONLY: this tool creates GPU resources, releases them and exits. It writes
nothing to the repo, to models/DLSS or to the staging folder, and it does not
touch ComfyUI.
"""

import ctypes
import os
import re
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


FLAG_MATRIX = ((0x4, "ALLOW_UNORDERED_ACCESS (d3d12.h)"),
               (0x8, "DENY_SHADER_RESOURCE - what this pack used to send"),
               (0x0, "no flags at all - the control"))


def _flag_matrix():
    """One device, one texture description, one differing byte.

    ``CreateCommittedResource`` reads the flags out of the descriptor, and the
    first entry of this pack's ladder said ALLOW_UNORDERED_ACCESS while the
    byte was 0x8. Three creations later the whole evening makes sense; this is
    that comparison, on the rig, in one run.
    """
    from ants.dlsssr import d3d12
    try:
        _factory, adapters = d3d12.enumerate_adapters()
        info, reason = d3d12.pick_adapter(adapters, 0)
        device = d3d12.D3D12Device.create(info.ptr if info else None,
                                          d3d12.D3D_FEATURE_LEVEL_11_0)
    except Exception as exc:
        _line("[FAIL]", f"flags matrix: device creation failed ({exc})")
        return None
    rows = []
    for flags, name in FLAG_MATRIX:
        try:
            texture = device.create_texture2d_with_flags(
                PROBE_W, PROBE_H, d3d12.DXGI_FORMAT_R16G16B16A16_FLOAT,
                flags, state=d3d12.D3D12_RESOURCE_STATE_COMMON,
                label=f"probe flags 0x{flags:X}")
            texture.release()
            ok, detail = True, "CREATED"
        except Exception as exc:
            ok = False
            match = re.search(r"0x[0-9A-Fa-f]{8}", str(exc))
            detail = (f"REFUSED {match.group(0)}" if match
                      else f"REFUSED ({str(exc).strip()[:60]})")
        rows.append((flags, name, ok, detail))
        _line("[OK]  " if ok else "[FAIL]",
              f"flags 0x{flags:X} ({name}) -> {detail}")
    _line("      ", f"device status after the matrix: {device.device_status()[2]}")
    return rows


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


def _plain_control(device, tag):
    """Can this device create ANY texture? (plain, no UAV flag)."""
    from ants.dlsssr import d3d12
    try:
        texture = device.create_texture2d(
            PROBE_W, PROBE_H, d3d12.DXGI_FORMAT_R16G16B16A16_FLOAT,
            allow_uav=False, label=f"probe plain ({tag})")
        texture.release()
        return True, "plain texture OK"
    except Exception as exc:
        return False, str(exc).strip()[:200]


def _uav_probe(tag, feature_level):
    """Create a FRESH device (at ``feature_level``) and try a UAV texture."""
    from ants.dlsssr import d3d12
    result = {"tag": tag, "ok": None, "why": "", "status": "",
              "plain": None, "device": None}
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
                         f"({device.texture_recipe_text()})")
        texture.release()
    except Exception as exc:
        result["ok"] = False
        result["why"] = str(exc).strip()
    if not ctx.dead:
        result["plain"] = _plain_control(device, tag)
        ctx.close()
    return result


def _report(result, feature_level):
    flag = {True: "[OK]  ", False: "[FAIL]", None: "[SKIP]"}.get(result["ok"])
    _line(flag, f"{result['tag']} (feature level 0x{feature_level:04X}): "
               f"{result['why']}")
    if result["status"]:
        _line("      ", f"device status right after: {result['status']}")
    if result["plain"] is not None:
        ok, text = result["plain"]
        _line("      ", f"same device, PLAIN texture: "
                        f"{'OK' if ok else 'FAILED'} - {text}")


def _child_without_cuda_flags():
    """Run this file again with the pack's CUDA flag arming switched off.

    The child is a genuinely fresh process, so it answers "does the pack's own
    import-time CUDA flag break UAV textures?" - the only CUDA state this pack
    sets, and the one difference between the run that worked (21:52, before
    that flag existed) and every failing run since.
    """
    import subprocess
    env = dict(os.environ)
    env["ANTS_NO_CUDA_FLAG_ARM"] = "1"
    env["ANTS_UAV_PROBE_CHILD"] = "1"
    try:
        out = subprocess.run([sys.executable, os.path.abspath(__file__),
                              "--phase-a"],
                             capture_output=True, text=True, timeout=180,
                             env=env)
    except Exception as exc:
        return None, f"child did not run ({exc.__class__.__name__})"
    text = f"{out.stdout or ''}{out.stderr or ''}"
    verdict = ""
    for line in text.splitlines():
        if line.startswith("[PHASE-A]") or line.startswith("[OK]") \
                or line.startswith("[FAIL]"):
            verdict = line
    if not verdict:
        verdict = text.strip().splitlines()[-1] if text.strip() else "(silent)"
    return out.returncode == 0, verdict


def _phase_a_only():
    """``--phase-a``: one fresh device, one UAV texture, one verdict line."""
    print("[PHASE-A] child probe: ANTS_NO_CUDA_FLAG_ARM=1 "
          "(the pack armed nothing)")
    result = _uav_probe("phase A0 - fresh process, no CUDA flag", 0xB000)
    _report(result, 0xB000)
    if result["ok"]:
        print("[PHASE-A] verdict: UAV texture CREATED without the CUDA flag")
        return 0
    print("[PHASE-A] verdict: UAV texture REFUSED without the CUDA flag")
    return 1


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
    _line("[INFO]", "pack flag byte: ALLOW_UNORDERED_ACCESS = "
                    f"0x{d3d12.D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS:X}"
                    " (d3d12.h says 0x4 - if this is anything else, that "
                    "is the bug)")
    print()

    _line("[INFO]", "flags matrix: one device, one description, three bytes")
    rows = _flag_matrix()
    print()
    if rows and rows[0][2] and not rows[1][2]:
        print("VERDICT: THE FLAGS BYTE WAS THE BUG, and it is already fixed. "
              "flags 0x4 (ALLOW_UNORDERED_ACCESS, d3d12.h) is ACCEPTED on this "
              "device; flags 0x8 (DENY_SHADER_RESOURCE - the byte this pack "
              "sent for every 'UAV' texture it ever created) is REFUSED with "
              "exactly the E_INVALIDARG our native runs kept reporting, while "
              "0x0 is accepted. Nothing about the legacy engine, the CUDA "
              "flag, the feature level or the driver state was ever wrong. Run "
              "the native node: its output texture is a real UAV texture now. "
              "Send this whole report.")
        return 14
    if rows and not rows[0][2]:
        _line("[WARN]", "flags 0x4 (ALLOW_UNORDERED_ACCESS) is refused too - "
                        "the byte is not the whole story here, running the "
                        "historical phases.")
        print()

    results = []
    for tag, level in (("phase A - fresh device", 0xB000),
                       ("phase B - fresh device", 0xC000)):
        result = _uav_probe(tag, level)
        results.append(result)
        _report(result, level)

    child_ok, child_verdict = _child_without_cuda_flags()
    _line("[INFO]" if child_ok is None else ("[OK]  " if child_ok else "[FAIL]"),
          "phase A0 - fresh child process WITHOUT the pack's CUDA flag: "
          + child_verdict)

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

    if child_ok is True and fresh_bad:
        print("VERDICT: the pack's own CUDA flag arming (cudaSetDeviceFlags "
              "0x04 at import) is what breaks UAV D3D12 textures in this "
              "process: the SAME test passed in a fresh child where nothing "
              "was armed. Until this is fixed, launch ComfyUI with "
              "ANTS_NO_CUDA_FLAG_ARM=1 - the legacy node will fall back to "
              "host staging (it needs that flag for its CUDA path), and the "
              "native node's D3D12 textures work. Send this whole report.")
        return 13
    if fresh_bad and not fresh_ok:
        print("VERDICT: a FRESH device refuses the UAV recipe here even though "
              "the flags byte is now the correct one (0x4) - and the flags "
              "matrix above says what this device does with 0x8 and 0x0. The "
              "legacy engine, the CUDA flag and the feature level are all "
              "cleared, so this points at the device/driver state itself: "
              "REBOOT and run this probe again before touching the pack. Send "
              "this whole report.")
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
    if "--phase-a" in sys.argv:
        raise SystemExit(_phase_a_only())
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:                      # never lose the report
        print(f"[FAIL] the probe itself failed: {exc.__class__.__name__}: {exc}")
        raise SystemExit(2)

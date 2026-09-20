"""Is THIS machine affected by the Windows multi-GPU CUDA bug (CORE-398)?

Why: ComfyUI issue #15255 (CORE-398) - on Windows, once a process has touched
more than one GPU, host->device copies can start failing with
CUDA_ERROR_OUT_OF_MEMORY even with plenty of VRAM free, and the CUDA context
never recovers. ComfyUI core enumerates every visible GPU at startup, so on a
multi-GPU machine the whole process is already in that shape before our node
runs. ComfyUI PR #15451 stops core from doing that by default; until it lands,
"launch with a single GPU" (--cuda-device 0 / CUDA_VISIBLE_DEVICES) and/or
--disable-pinned-memory is the workaround.

WHAT THIS SCRIPT DOES (no file is written, your install is not touched):
  phase A - a CHILD process with CUDA_VISIBLE_DEVICES=0: register a pinned
            host buffer, copy it to the GPU and back. One GPU only.
  phase B - THIS process, all GPUs visible: touch every GPU (name + LUID +
            primary context retained) and then repeat the very same copy.
  verdict - A passed and B failed  => the rig reproduces the bug (exit 10).
            both passed             => not reproduced this way (exit 0).

Both phases are separate processes on purpose: the bug can leave a CUDA
context unrecoverable, which is exactly why rattus128's own diagnostic (the
canonical version of this test - windows_cuda_host_memory_diagnostic.py,
posted in issue #15255) runs every device/probe combination in a fresh
process. Use that script as a second opinion if the result here surprises you.

Usage: run check_cuda_multigpu.bat (it finds ComfyUI's python), or
       python check_cuda_multigpu.py
"""

import ctypes
import os
import subprocess
import sys

CUDA_SUCCESS = 0
COPY_BYTES = 64 * 1024 * 1024        # 64 MiB - small, and freed again
ERROR_NAMES = {
    1: "CUDA_ERROR_INVALID_VALUE",
    2: "CUDA_ERROR_OUT_OF_MEMORY",
    3: "CUDA_ERROR_NOT_INITIALIZED",
    4: "CUDA_ERROR_DEINITIALIZED",
    100: "CUDA_ERROR_NO_DEVICE",
    101: "CUDA_ERROR_INVALID_DEVICE",
    201: "CUDA_ERROR_INVALID_CONTEXT",
    400: "CUDA_ERROR_INVALID_HANDLE",
    700: "CUDA_ERROR_ILLEGAL_ADDRESS",
    719: "CUDA_ERROR_LAUNCH_FAILED",
    999: "CUDA_ERROR_UNKNOWN",
}


def err(code):
    return f"{ERROR_NAMES.get(code, 'CUDA error')} ({code})"


def say(line=""):
    print(line, flush=True)


class Cuda:
    """Just enough of the CUDA driver API (nvcuda.dll) for this test."""

    def __init__(self):
        loader = getattr(ctypes, "WinDLL", None)
        if loader is None:
            raise RuntimeError("this is a Windows-only diagnostic")
        self.lib = loader("nvcuda.dll")
        self.bind("cuInit", [ctypes.c_uint])
        self.bind("cuDeviceGetCount", [ctypes.POINTER(ctypes.c_int)])
        self.bind("cuDeviceGetName", [ctypes.c_void_p, ctypes.c_int, ctypes.c_int])
        self.bind("cuDeviceGetLuid",
                  [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint), ctypes.c_int])
        self.bind("cuDevicePrimaryCtxRetain",
                  [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int])
        self.bind("cuCtxSetCurrent", [ctypes.c_void_p])
        self.bind_any(["cuMemAlloc_v2", "cuMemAlloc"],
                      [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t])
        self.bind_any(["cuMemFree_v2", "cuMemFree"], [ctypes.c_void_p])
        self.bind("cuMemcpyHtoD_v2",
                  [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t])
        self.bind("cuMemcpyDtoH_v2",
                  [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t])
        self.bind("cuMemHostRegister_v2",
                  [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint])
        self.bind("cuMemHostUnregister", [ctypes.c_void_p])
        code = self.lib.cuInit(0)
        if code != CUDA_SUCCESS:
            raise RuntimeError(f"cuInit failed: {err(code)}")

    def bind(self, name, argtypes):
        fn = getattr(self.lib, name, None)
        if fn is None:
            raise RuntimeError(f"nvcuda.dll has no {name} (driver too old)")
        fn.argtypes = argtypes
        fn.restype = ctypes.c_int
        setattr(self, name, fn)

    def bind_any(self, names, argtypes):
        for name in names:
            if getattr(self.lib, name, None) is not None:
                self.bind(name, argtypes)
                return name
        raise RuntimeError(f"nvcuda.dll has none of {names}")

    def devices(self):
        total = ctypes.c_int(0)
        if self.cuDeviceGetCount(ctypes.byref(total)) != CUDA_SUCCESS:
            raise RuntimeError("cuDeviceGetCount failed")
        rows = []
        for ordinal in range(total.value):
            name_buf = ctypes.create_string_buffer(128)
            self.cuDeviceGetName(ctypes.cast(name_buf, ctypes.c_void_p), 128,
                                 ordinal)
            luid_buf = ctypes.create_string_buffer(8)
            mask = ctypes.c_uint(0)
            code = self.cuDeviceGetLuid(ctypes.cast(luid_buf, ctypes.c_void_p),
                                        ctypes.byref(mask), ordinal)
            luid = luid_buf.raw if code == CUDA_SUCCESS else None
            rows.append((ordinal, name_buf.value.decode("ascii", "replace"),
                         luid))
        return rows

    def luid_text(self, luid):
        if luid is None:
            return "?"
        lo = int.from_bytes(luid[:4], "little")
        hi = int.from_bytes(luid[4:], "little")
        return f"{hi:08x}:{lo:08x}"


def host_buffer(size):
    """A page-aligned host buffer CUDA can register."""
    raw = ctypes.create_string_buffer(size + 0x10000)
    base = ctypes.addressof(raw)
    aligned = (base + 0xFFF) & ~0xFFF
    return raw, aligned


def copy_probe(cuda, ordinal):
    """Register a pinned host buffer and round-trip it through one GPU.

    Returns (ok, notes): every CUDA call is reported, because WHICH call fails
    is the whole point (the bug first hits host registration, then every copy).
    """
    notes = []
    raw, host = host_buffer(COPY_BYTES)
    ctx = ctypes.c_void_p()
    code = cuda.cuDevicePrimaryCtxRetain(ctypes.byref(ctx), ordinal)
    if code != CUDA_SUCCESS:
        return False, [f"cuDevicePrimaryCtxRetain({ordinal}): {err(code)}"]
    code = cuda.cuCtxSetCurrent(ctx)
    if code != CUDA_SUCCESS:
        return False, [f"cuCtxSetCurrent({ordinal}): {err(code)}"]
    dev_ptr = ctypes.c_void_p()
    code = cuda.cuMemAlloc_v2(ctypes.byref(dev_ptr), COPY_BYTES)
    if code != CUDA_SUCCESS:
        return False, [f"cuMemAlloc_v2({COPY_BYTES}): {err(code)}"]
    try:
        code = cuda.cuMemHostRegister_v2(ctypes.c_void_p(host), COPY_BYTES, 0)
        notes.append(f"cuMemHostRegister_v2 {COPY_BYTES // (1024 * 1024)} MiB: "
                     + ("ok" if code == CUDA_SUCCESS else err(code)))
        if code != CUDA_SUCCESS:
            return False, notes
        ctypes.memset(host, 0x5A, COPY_BYTES)
        code = cuda.cuMemcpyHtoD_v2(dev_ptr, ctypes.c_void_p(host), COPY_BYTES)
        notes.append("cuMemcpyHtoD_v2: "
                     + ("ok" if code == CUDA_SUCCESS else err(code)))
        if code != CUDA_SUCCESS:
            return False, notes
        _back_raw, back = host_buffer(COPY_BYTES)
        ctypes.memset(back, 0x00, COPY_BYTES)
        code = cuda.cuMemcpyDtoH_v2(ctypes.c_void_p(back), dev_ptr, COPY_BYTES)
        notes.append("cuMemcpyDtoH_v2: "
                     + ("ok" if code == CUDA_SUCCESS else err(code)))
        if code != CUDA_SUCCESS:
            return False, notes
        same = ctypes.string_at(back, 16) == ctypes.string_at(host, 16)
        notes.append("round-trip bytes match: " + ("yes" if same else "NO"))
        return same, notes
    finally:
        cuda.cuMemHostUnregister(ctypes.c_void_p(host))
        cuda.cuMemFree_v2(dev_ptr)


def phase_single():
    """Child process: one GPU visible, host-pinned copy round-trip."""
    cuda = Cuda()
    rows = cuda.devices()
    say(f"  visible devices: {len(rows)} "
        f"({', '.join(f'{n} (LUID {cuda.luid_text(l)})' for _, n, l in rows)})")
    ok, notes = copy_probe(cuda, 0)
    for note in notes:
        say(f"    {note}")
    say("  PHASE A (single GPU, pinned host copy): "
        + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


def phase_all():
    """This process: touch every GPU, then the same copy on device 0."""
    cuda = Cuda()
    rows = cuda.devices()
    say(f"  visible devices: {len(rows)}")
    for ordinal, name, luid in rows:
        say(f"    {ordinal}: {name} (LUID {cuda.luid_text(luid)})")
    if len(rows) < 2:
        say("")
        say("  Only one CUDA device is visible in this process, so the "
            "multi-GPU")
        say("  trigger cannot be reproduced here. If ComfyUI still fails, it "
            "was")
        say("  not launched with the same visibility - check the launch line.")
        return 0, True, True
    say("  touching every GPU (primary context retained, like ComfyUI does at "
        "startup)...")
    contexts = []
    for ordinal, _name, _luid in rows:
        ctx = ctypes.c_void_p()
        code = cuda.cuDevicePrimaryCtxRetain(ctypes.byref(ctx), ordinal)
        say(f"    cuDevicePrimaryCtxRetain({ordinal}): "
            + ("ok" if code == CUDA_SUCCESS else err(code)))
        if code == CUDA_SUCCESS:
            contexts.append(ctx)
    ok, notes = copy_probe(cuda, 0)
    for note in notes:
        say(f"    {note}")
    say("  PHASE B (all GPUs touched, same copy): "
        + ("PASSED" if ok else "FAILED"))
    return (0 if ok else 10), ok, False


def main():
    say("=" * 74)
    say("[ANTs] CUDA multi-GPU check - ComfyUI issue #15255 / CORE-398")
    say("=" * 74)
    say(f"python: {sys.version.split()[0]}  ({sys.executable})")
    say(f"CUDA_VISIBLE_DEVICES (this process): "
        f"{os.environ.get('CUDA_VISIBLE_DEVICES', '<unset>')}")
    say("")
    say("--- PHASE A: one GPU only (a child process with "
        "CUDA_VISIBLE_DEVICES=0)")
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="0")
    child = subprocess.run([sys.executable, os.path.abspath(__file__),
                            "--phase", "single"],
                           env=env, capture_output=True, text=True)
    say(child.stdout.rstrip() or f"  (child produced no output; exit "
                                 f"{child.returncode})")
    if child.stderr.strip():
        say("  child stderr: " + child.stderr.strip().splitlines()[-1])
    single_ok = child.returncode == 0
    say("")
    say("--- PHASE B: every GPU visible, all of them touched")
    code, both_ok, single_only = phase_all()
    say("")
    say("=" * 74)
    if single_only:
        say("VERDICT: this machine has ONE CUDA device (exit 0).")
        say("  The multi-GPU CUDA bug (ComfyUI issue #15255 / CORE-398) needs "
            "more")
        say("  than one GPU visible to a process, so it does not apply here: "
            "look")
        say("  elsewhere for the failure you are chasing.")
        exit_code = 0
    elif single_ok and not both_ok:
        say("VERDICT: BUG REPRODUCED on this machine (ComfyUI issue #15255).")
        say("  One GPU is fine; as soon as every GPU is touched, the CUDA "
            "calls above")
        say("  start failing and the context does not recover. That is the "
            "shape")
        say("  ComfyUI is in when it enumerates all GPUs at startup - and the")
        say("  `host staging (CPU)` / DEVICE_REMOVED / 'cannot match CUDA "
            "ordinal")
        say("  by LUID' failures in this project are the same family.")
        say("  FIX/TRY: launch ComfyUI with --cuda-device <one id> (a single "
            "GPU)")
        say("  and/or --disable-pinned-memory, then run the frame again.")
        exit_code = 10
    elif single_ok and both_ok:
        say("VERDICT: not reproduced by this test (exit 0).")
        say("  This does NOT clear the machine: the trigger may need the "
            "streaming")
        say("  pattern (many registrations/copies over time) rather than this "
            "small")
        say("  one-shot copy. If ComfyUI keeps failing, run rattus128's "
            "script from")
        say("  issue #15255 (windows_cuda_host_memory_diagnostic.py) for a "
            "second")
        say("  opinion, and send both outputs.")
        exit_code = 0
    else:
        say("VERDICT: the single-GPU phase FAILED TOO (exit 2).")
        say("  That is not the multi-GPU trigger - the driver/machine is "
            "already in")
        say("  a bad state, or the pinned-host path is broken on its own.")
        say("  Reboot, close everything that uses the GPU, and run this again.")
        exit_code = 2
    say("  Copy everything above into the report.")
    say("  NOTE: this process may leave a poisoned CUDA context - it exits now")
    say("  on purpose, WITHOUT calling CUDA cleanup APIs.")
    say("=" * 74)
    return exit_code


def guarded(fn):
    """Run a phase, reporting any failure as one line - never a traceback."""
    try:
        return fn()
    except Exception as exc:                    # noqa: BLE001 - report, never traceback
        say(f"[X] {exc.__class__.__name__}: {exc}")
        say("    If this says 'no nvcuda.dll' / 'Windows-only', run it with "
            "ComfyUI's")
        say("    own python (python_embeded\\python.exe) on the rig.")
        return 2


if __name__ == "__main__":
    if "--phase" in sys.argv:
        sys.exit(guarded(phase_single))
    sys.exit(guarded(main))

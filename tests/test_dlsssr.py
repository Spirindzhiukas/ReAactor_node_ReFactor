"""Tests for the pure-Python DLSS host (sandbox-safe parts only).

The D3D12/NGX call paths need Windows + a GPU; everything else is verified
here: shim PE structure (pefile), our parameter object semantics, NGX
constants, discovery, and the frame converters.

Usage: python tests/test_dlsssr.py
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

from smoke_import import install_stubs  # noqa: E402

PASS = 0
FAIL = 0


def raw_exec_lines(bat_bytes):
    """Executable (non-rem) lines of a CRLF bat, for paren-block checks."""
    for ln in bat_bytes.split(b"\r\n"):
        s = ln.strip()
        if s and not s.lower().startswith(b"rem"):
            yield s


def _iat_tracer_fixture_check():
    """Build a flat (identity-mapped) PE whose only import is KERNEL32!
    {ExitProcess, abort}, run crashlog._patch_iat over it with a fake
    kernel32, and verify the walker finds both thunks, records them as
    seen, and rewrites each IAT slot to a fresh pinned trampoline."""
    import ctypes
    import struct

    from ants.dlsssr import crashlog

    buf = bytearray(0x2000)

    def put(off, data):
        buf[off:off + len(data)] = data

    def w32(off, v):
        put(off, struct.pack("<I", v))

    def w64(off, v):
        put(off, struct.pack("<Q", v))

    w32(0x3C, 0x80)
    put(0x80, b"PE\x00\x00")
    w32(0x80 + 144, 0x400)          # import directory RVA (dir[1])
    w32(0x400 + 0, 0x500)           # OriginalFirstThunk
    w32(0x400 + 12, 0x4C0)          # dll name
    w32(0x400 + 16, 0x5A0)          # FirstThunk (IAT)
    put(0x4C0, b"KERNEL32.dll\x00")
    names = ["ExitProcess", "abort"]
    slot = 0x540
    for k, n in enumerate(names):
        w64(0x500 + k * 8, slot)    # INT entry -> hint/name
        w64(0x5A0 + k * 8, 0xDEADBEEF + k)  # loader-'resolved' original
        put(slot, struct.pack("<H", 0) + n.encode() + b"\x00")
        slot += 2 + len(n) + 1
    w64(0x500 + len(names) * 8, 0)  # terminator
    w64(0x5A0 + len(names) * 8, 0)

    image = ctypes.create_string_buffer(bytes(buf), len(buf))
    base = ctypes.addressof(image)
    keep = []
    fake_k32 = type("K", (), {"VirtualProtect": staticmethod(lambda *a: 1)})()
    patched, wanted = crashlog._patch_iat(fake_k32, base, keep)
    slots = [ctypes.c_uint64.from_address(
        base + 0x5A0 + i * 8).value for i in range(len(names))]
    return (patched == [f"KERNEL32.dll!{n}" for n in names]
            and wanted == set(names)
            and all(slots[i] != 0xDEADBEEF + i for i in range(len(names)))
            and len(keep) >= 2 * len(names))


def _resolver_tolerance_check():
    """Build a minimal PE (one export at RVA 0x1020) and run the resolver
    over it with a junk token plus a real offset; the junk must be skipped
    and the offset resolved."""
    import struct
    import subprocess
    import tempfile

    tool = REPO / "tools" / "resolve_crash_offset.py"
    buf = bytearray(0x2000)

    def put(off, data):
        buf[off:off + len(data)] = data

    put(0, b"MZ")
    put(0x3C, struct.pack("<I", 0x80))
    put(0x80, b"PE\x00\x00")
    put(0x84, struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, 240, 0x2022))
    opt = 0x98
    put(opt, struct.pack("<HBBIIIIIQII", 0x20B, 14, 0, 0x200, 0, 0, 0x1000,
                         0x1000, 0x400000, 0x1000, 0x200))
    put(opt + 112, struct.pack("<II", 0x1000, 0x100))
    put(opt + 120, struct.pack("<II", 0x1200, 0x100))
    put(opt + 240, b".rdata\x00\x00" + struct.pack("<IIII", 0x1000, 0x1000,
                                                   0x1C00, 0x400) + b"\x00" * 20)

    def rva(r):
        return 0x400 + (r - 0x1000)

    # export dir: funcs@0x1180 (clear of the name strings), names@0x1080,
    # ordinals@0x10A0 (0-based indices into AddressOfFunctions)
    put(rva(0x1000), struct.pack("<IIHHIIIIIII", 0, 0, 1, 0, 0x1040, 1, 2, 2,
                                 0x1180, 0x1080, 0x10A0))
    put(rva(0x1040), b"probe.dll\x00")
    put(rva(0x1080), struct.pack("<II", 0x10C0, 0x10E0))
    put(rva(0x10A0), struct.pack("<HH", 0, 1))
    put(rva(0x1180), struct.pack("<II", 0x1000, 0x1020))
    put(rva(0x10C0), b"NVSDK_NGX_D3D12_Init\x00")
    put(rva(0x10E0), b"NVSDK_NGX_D3D12_EvaluateFeature_C\x00")

    with tempfile.NamedTemporaryFile(suffix=".dll", delete=False) as fh:
        fh.write(bytes(buf))
        path = fh.name
    out = subprocess.run(
        [sys.executable, str(tool), path, "resolve_offsets.bat", "0x1025"],
        capture_output=True, text=True).stdout
    return ("[skip] 'resolve_offsets.bat'" in out
            and "0x1025 -> NVSDK_NGX_D3D12_EvaluateFeature_C" in out)


def _imports_probe_check():
    """Build a PE importing KERNEL32!{ExitProcess, NVSDK_NGX_CUDA_Evaluate}
    and verify tools/list_imports.py flags the termination API statically."""
    import struct
    import subprocess
    import tempfile

    tool = REPO / "tools" / "list_imports.py"
    buf = bytearray(0x2000)

    def put(off, data):
        buf[off:off + len(data)] = data

    def w32(off, v):
        put(off, struct.pack("<I", v))

    def w64(off, v):
        put(off, struct.pack("<Q", v))

    put(0, b"MZ")
    put(0x3C, struct.pack("<I", 0x80))
    put(0x80, b"PE\x00\x00")
    put(0x84, struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, 240, 0x2022))
    opt = 0x98
    put(opt, struct.pack("<HBBIIIIIQII", 0x20B, 14, 0, 0x200, 0, 0, 0x1000,
                         0x1000, 0x400000, 0x1000, 0x200))
    put(opt + 120, struct.pack("<II", 0x1300, 0x100))
    put(opt + 240, b".rdata\x00\x00" + struct.pack("<IIII", 0x1000, 0x1000,
                                                    0x1C00, 0x400) + b"\x00" * 20)

    def rva(r):
        return 0x400 + (r - 0x1000)

    w32(rva(0x1300), 0x1400)
    w32(rva(0x130C), 0x13C0)
    w32(rva(0x1310), 0x1500)
    put(rva(0x13C0), b"KERNEL32.dll\x00")
    put(rva(0x1400), struct.pack("<QQQ", 0x1440, 0x1460, 0))
    put(rva(0x1500), struct.pack("<QQQ", 0xDEADBEEF, 0xDEADBEF0, 0))
    put(rva(0x1440), struct.pack("<H", 0) + b"ExitProcess\x00")
    put(rva(0x1460), struct.pack("<H", 0) + b"NVSDK_NGX_CUDA_EvaluateFeature_C\x00")

    with tempfile.NamedTemporaryFile(suffix=".dll", delete=False) as fh:
        fh.write(bytes(buf))
        path = fh.name
    out = subprocess.run([sys.executable, str(tool), path],
                         capture_output=True, text=True).stdout
    return ("TERMINATION API" in out
            and "NVSDK_NGX_CUDA_EvaluateFeature_C" in out
            and "termination APIs imported: ExitProcess" in out)


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name}")


def main():
    install_stubs()
    sys.path.insert(0, str(REPO))

    import numpy as np

    # ---- shim PE structure ----
    from ants.dlsssr.shim import build_shim_dll
    payload, exports = build_shim_dll()
    check("shim: MZ + PE signatures", payload[:2] == b"MZ" and payload[0x40:0x44] == b"PE\x00\x00")
    check("shim: size is file-aligned multiple", len(payload) % 0x200 == 0)
    try:
        import pefile
        pe = pefile.PE(data=payload)
        names = sorted(e.name.decode() for e in pe.DIRECTORY_ENTRY_EXPORT.symbols)
        check("shim: pefile parses + 4 exports",
              names == ["fwd_create", "fwd_evaluate", "fwd_release", "fwd_set_slots"])
        check("shim: entry point + DYNAMIC_BASE|NX_COMPAT",
              pe.OPTIONAL_HEADER.DllCharacteristics & 0x140 == 0x140)
        # unwind metadata for the 3 non-leaf thunks (rig run 14: an exception
        # escaping through an unwindable-less frame killed the process)
        import struct as _st
        raw = pe.get_memory_mapped_image()
        exc = pe.OPTIONAL_HEADER.DATA_DIRECTORY[3]
        ok = exc.Size == 36
        for i in range(exc.Size // 12):
            b, e, u = _st.unpack_from("<III", raw, exc.VirtualAddress + i * 12)
            ver, prolog, count = raw[u], raw[u + 1], raw[u + 2]
            ok = ok and e - b == 63 and ver == 2 and prolog == 4 and count == 1 \
                and raw[u + 4:u + 6] == b"\x04\x16"  # UWOP_ALLOC_SMALL 56 @4
        check("shim: 3 RUNTIME_FUNCTIONs + shared UNWIND_INFO (thunks unwindable)", ok)
    except ImportError:
        check("shim: pefile parses (pefile missing in env)", False)

    # ---- our parameter object: store semantics via its Python side ----
    from ants.dlsssr.parameters import NGX_RESULT_FEATURE_NOT_FOUND, OwnParameterObject
    p = OwnParameterObject()
    obj_vptr = ctypes.cast(p._object, ctypes.POINTER(ctypes.c_void_p)).contents.value
    check("params: object's first field points at the vtable",
          obj_vptr == ctypes_addr_of(p._vtable) and p.ptr.value != 0)
    p.set_u32("DLSSNR.Width", 1920)
    p.set_f32("DLSSNR.Intensity", 1.5)
    p.set_resource("DLSSNR.Color", 0xDEADBEEF)
    check("params: u32/f32/resource stored",
          p.store["DLSSNR.Width"] == 1920 and p.store["DLSSNR.Intensity"] == 1.5
          and p.store["DLSSNR.Color"] == 0xDEADBEEF)

    # getters through the real ctypes callbacks (the runtime's view)
    proto = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    get_u32 = proto(p._vtable[12])
    out = ctypes.c_uint32(0)
    name = ctypes.create_string_buffer(b"DLSSNR.Width\x00")
    hr = get_u32(p.ptr, name, ctypes.byref(out))
    check("params: getter returns SUCCESS + value via the real vtable",
          hr == 1 and out.value == 1920)
    name2 = ctypes.create_string_buffer(b"DLSSNR.NotSet\x00")
    hr2 = get_u32(p.ptr, name2, ctypes.byref(out))
    check("params: unset name answers FeatureNotFound",
          hr2 == (NGX_RESULT_FEATURE_NOT_FOUND & 0xFFFFFFFF) or hr2 == ctypes.c_int32(NGX_RESULT_FEATURE_NOT_FOUND).value)

    # ---- constants ----
    from ants.dlsssr import ngx
    check("ngx: API version 0x15 + feature ids 1/18",
          ngx.NGX_VERSION_API == 0x15 and ngx.FEATURE_SR == 1 and ngx.FEATURE_NR == 18)
    ngx_src = (REPO / "ants" / "dlsssr" / "ngx.py").read_text()
    crashlog_src = (REPO / "ants" / "dlsssr" / "crashlog.py").read_text()
    # ---- run 21 countermeasures (Merserk host contract) ----
    check("ngx: NR preloads the driver core into the process (run 21)",
          "NGX core preloaded" in ngx_src and "locate_ngx_core()" in ngx_src)
    check("ngx: explicit _nvngx.dll override next to the snippet wins",
          '"_nvngx.dll"' in ngx_src and "local_core" in ngx_src)
    check("ngx: Init_Ext-first with the 0x13..0x20 version sweep on NR",
          "Init_Ext-first" in ngx_src and "range(0x13, 0x21)" in ngx_src
          and "classic 4-arg Init accepted" in ngx_src)
    check("ngx: snippet callbacks are an env-gated experiment, pinned",
          "ANTS_NR_RUNTIME_CALLBACKS" in ngx_src and "_cb_keep" in ngx_src
          and "SetRuntimeParamsCallback" in ngx_src
          and "ANTS_NR_CALLBACK_RET" in ngx_src
          and "ANTS_NR_CALLBACK_DUMP" in ngx_src)
    check("ngx: the callback dumper cannot fault (VirtualQuery, never "
          "IsBadReadPtr - run 23's AV was the dumper itself)",
          "VirtualQuery" in ngx_src and ".IsBadReadPtr(" not in ngx_src
          and "*([_CVOID] * 8)" in ngx_src)
    check("crashlog: silent-kill tracer ships (run 24 = death with no "
          "exception: deliberate terminate, so the trap patches NGX import "
          "tables and logs the killer's call chain before forwarding)",
          "install_termination_trap" in crashlog_src
          and "ExitProcess" in crashlog_src
          and "RaiseFailFastException" in crashlog_src
          and "VirtualProtect" in crashlog_src
          and "RtlCaptureStackBackTrace" in crashlog_src
          and "install_termination_trap" in ngx_src
          and "ANTS_NR_TERMINATION_TRAP" in ngx_src
          # run 27b: the trap install must sit OUTSIDE the callbacks env
          # block - nesting it there let an env deletion strip the nets.
          and ngx_src.index("install_termination_trap")
          < ngx_src.index('os.environ.get("ANTS_NR_RUNTIME_CALLBACKS")'))
    check("crashlog: ntdll!NtTerminateProcess detour ships (run 26: the "
          "death avoided both patched IATs, so the final gate itself is "
          "detoured - stolen syscall stub re-executed after logging)",
          "install_ntdll_terminate_detour" in crashlog_src
          and "install_ntdll_terminate_detour" in ngx_src
          and "\\x4c\\x8b\\xd1" in crashlog_src
          and "\\x0f\\x05" in crashlog_src
          and "no syscall instruction" in crashlog_src
          and "jmp rax" in crashlog_src)
    check("crashlog: IAT termination tracer walks imports and patches the "
          "termination APIs (flat-PE fixture, fake kernel32)",
          _iat_tracer_fixture_check())
    check("tools: crash-offset resolver ships (names MODULE+0xRVAs)",
          (REPO / "tools" / "resolve_crash_offset.py").is_file()
          and "bisect" in (REPO / "tools" / "resolve_crash_offset.py").read_text())
    check("tools: resolver tolerates junk tokens (rig: the bat's own name "
          "reached int() and tracebacked - now skipped with [skip])",
          _resolver_tolerance_check())
    check("tools: import lister flags termination APIs + NGX backend "
          "bindings statically (engine-dll discriminator, no execution)",
          (REPO / "tools" / "list_imports.py").is_file()
          and (REPO / "tools" / "list_imports.bat").is_file()
          and _imports_probe_check())


    bat = (REPO / "tools" / "resolve_offsets.bat")
    bat_raw = bat.read_bytes() if bat.is_file() else b""
    check("tools: rig diagnostics ship as ready-to-run bat files (owner "
          "imperative) - resolver bat is CRLF, clipboard-returning, with a "
          "single marked owner-editable block and zero parens on executable "
          "lines (rem comments may)",
          bat_raw.startswith(b"@echo off\r\n")
          and b"clip <" in bat_raw
          and b"THE ONLY BLOCK YOU MAY EDIT" in bat_raw
          and b'if "' in bat_raw
          and not any(b" (" in ln for ln in raw_exec_lines(bat_raw)))
    check("ngx: session close frees the core AFTER the snippet (reverse order)",
          ngx_src.find("self.module.close()") < ngx_src.find(
              "Reverse load order"))
    from ants.dlsssr.sr import PERF_RATIO, DLSS_RENDER_PRESETS
    check("sr: mode ratios fixed (DLAA 1.0 .. UP 3.0)",
          PERF_RATIO[5] == 1.0 and PERF_RATIO[3] == 3.0 and PERF_RATIO[1] > 1.72)
    check("sr: preset letters include J/K/L/M",
          {"J": 10, "K": 11, "L": 12, "M": 13}.items() <= DLSS_RENDER_PRESETS.items())
    from ants.dlsssr.d3d12 import (_DEVICE_CREATE_COMMITTED_RESOURCE, _LIST_RESOURCE_BARRIER,
                                   _QUEUE_SIGNAL, D3D_FEATURE_LEVEL_11_0)
    check("d3d12: vtable slots match the public headers",
          _DEVICE_CREATE_COMMITTED_RESOURCE == 27 and _LIST_RESOURCE_BARRIER == 26
          and _QUEUE_SIGNAL == 14 and D3D_FEATURE_LEVEL_11_0 == 0xB000)

    # ---- discovery: flat SR dlls are their own sets (the owner's test case) ----
    import os, tempfile
    from ants.dlsssr import discovery as sr_disc
    from ants.dlssnr import discovery as nr_disc
    with tempfile.TemporaryDirectory() as td:
        sr_root = os.path.join(td, "SR")
        os.makedirs(sr_root)
        # real SR runtimes are tens of MB; the guard rejects <1MB stubs
        open(os.path.join(sr_root, "nvngx_dlss.dll"), "wb").write(b"x" * 1_100_000)
        open(os.path.join(sr_root, "nvngx_dlss_310.9.1.dll"), "wb").write(b"x" * 1_200_000)
        saved = nr_disc.DLSS_ROOT
        nr_disc.DLSS_ROOT = td
        try:
            sets = sr_disc.discover_sr_sets()
            check("sr discovery: two flat dlls = two named sets",
                  {s["name"] for s in sets} == {"nvngx_dlss", "nvngx_dlss_310.9.1"}
                  and all(s["kind"] == "dll" for s in sets))
            resolved = sr_disc.resolve_sr_dll("nvngx_dlss_310.9.1.dll".rsplit(".", 1)[0])
            check("sr discovery: resolve by set name hits the right dll",
                  resolved.endswith("nvngx_dlss_310.9.1.dll"))
            try:
                nr_disc.DLSS_ROOT = os.path.join(td, "empty")
                sr_disc.resolve_sr_dll("auto")
                check("sr discovery: loud error when SR folder empty", False)
            except RuntimeError as exc:
                check("sr discovery: loud error when SR folder empty",
                      "[ANTs]" in str(exc) and "SR" in str(exc)
                      and "redistribution" in str(exc))
        finally:
            nr_disc.DLSS_ROOT = saved

        # ---- masquerader guard: a small "nvngx_dlss.dll" is a stub ----
        nr_disc.DLSS_ROOT = td
        os.makedirs(os.path.join(td, "SR"), exist_ok=True)
        open(os.path.join(td, "SR", "nvngx_dlss.dll"), "wb").write(b"stub")
        try:
            sr_disc.resolve_sr_dll("nvngx_dlss")
            check("sr: masquerader guard rejects a small stub", False)
        except RuntimeError as exc:
            check("sr: masquerader guard rejects a small stub",
                  "under 1 MB" in str(exc) and "helper/caller stub" in str(exc))
        open(os.path.join(td, "SR", "nvngx_dlss_310.9.1.dll"), "wb").write(b"x" * 1_100_000)
        check("sr: auto skips stubs and finds the real runtime",
              sr_disc.resolve_sr_dll("auto").endswith("nvngx_dlss_310.9.1.dll"))

        # NR runtime locator
        nr_dir = os.path.join(td, "NR", "v1")
        os.makedirs(nr_dir)
        open(os.path.join(nr_dir, "nvngx_dlssnr_RenoDX_friendly.dll"), "wb").write(b"x")
        found = sr_disc.find_nr_runtime_dll(nr_dir)
        check("nr locator: any nvngx_dlssnr* filename accepted",
              found.endswith("nvngx_dlssnr_RenoDX_friendly.dll"))

    # ---- frame converter math (numpy twin of the node's torch converters;
    # ---- the sandbox has no torch, so the exact node methods run only on rig)
    rng = np.random.RandomState(3)
    frame = rng.rand(48, 32, 3).astype(np.float32)
    rgb8 = (np.clip(frame, 0.0, 1.0) * 255.0).round().astype(np.uint8)
    rgba = np.empty((48, 32, 4), dtype=np.uint8)
    rgba[:, :, :3] = rgb8
    rgba[:, :, 3] = 255
    payload = rgba.tobytes()
    arr = np.frombuffer(payload, dtype=np.uint8).reshape(48, 32, 4)
    back = arr[:, :, :3].astype(np.float32) / 255.0
    check("converters: rgba8 packing math round-trips within 8-bit error",
          float(np.max(np.abs(back - frame))) < 1.0 / 255.0 + 1e-4
          and (arr[:, :, 3] == 255).all())

    # ---- engine widget surface ----
    import importlib
    sys.path.insert(0, str(REPO.parent))
    pkg = importlib.import_module(REPO.name)
    types_def = pkg.NODE_CLASS_MAPPINGS["ANTsDLSS5Enhancer"].INPUT_TYPES()
    check("dlss5: engine widget present, native default",
          types_def["required"]["engine"][0][0] == "ANTs native NGX")
    check("dlss5: 20 nodes registered (SR upscaler added)",
          len(pkg.NODE_CLASS_MAPPINGS) == 20
          and "ANTsDLSSSRUpscaler" in pkg.NODE_CLASS_MAPPINGS)

    # ---- IID wire bytes (canonical GUID layout; reference bytes are
    # ---- hand-written literals, NOT derived from guid() - the rig hit
    # ---- E_NOINTERFACE because guid() once reversed the field order) ----
    from ants.dlsssr.com import guid as _guid
    check("iid: ID3D12Device wire bytes match the canonical literal",
          bytes(_guid("{189819f1-1db6-4b57-be54-1821339b85f7}"))
          == bytes([0xf1, 0x19, 0x98, 0x18, 0xb6, 0x1d, 0x57, 0x4b,
                    0xbe, 0x54, 0x18, 0x21, 0x33, 0x9b, 0x85, 0xf7]))
    from ants.dlsssr import d3d12 as _d3d12
    check("iid: ID3D12Fence wire bytes (base fence, rig-verified - 4a689c71 is Fence1)",
          bytes(_d3d12.IID_ID3D12Fence)
          == bytes([0xcf, 0x3d, 0x75, 0x0a, 0xd8, 0xc4, 0x91, 0x4b,
                    0xad, 0xf6, 0xbe, 0x5a, 0x60, 0xd9, 0x5a, 0x76]))
    check("iid: ID3D12Resource wire bytes (...0fad, not ...02ad)",
          bytes(_d3d12.IID_ID3D12Resource)
          == bytes([0xbe, 0x42, 0x64, 0x69, 0x2e, 0xa7, 0x59, 0x40,
                    0xbc, 0x79, 0x5b, 0x5c, 0x98, 0x04, 0x0f, 0xad]))
    check("iid: IDXGIFactory1 wire bytes match the canonical literal",
          bytes(_guid("{770aae78-f26f-4dba-a829-253c83d1b387}"))
          == bytes([0x78, 0xae, 0x0a, 0x77, 0x6f, 0xf2, 0xba, 0x4d,
                    0xa8, 0x29, 0x25, 0x3c, 0x83, 0xd1, 0xb3, 0x87]))

    # ---- win32 calling conventions (rig run 7: CFUNCTYPE rejected the
    # ---- c_void_p wrap; GetProcAddress had no restype = 64-bit truncation)
    from ants.dlsssr import win32 as _w
    proto0 = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)
    tgt = proto0(lambda v: v * 2)
    addr = ctypes.cast(tgt, ctypes.c_void_p).value
    check("win32: callable_at accepts int and c_void_p addresses",
          _w.callable_at(addr, [ctypes.c_int], ctypes.c_int)(21) == 42
          and _w.callable_at(ctypes.c_void_p(addr), [ctypes.c_int], ctypes.c_int)(21) == 42)
    check("win32: export_address handles int and c_void_p handles (no int() parse trap)",
          _w.export_address(0x1000, 0x40) == 0x1040
          and _w.export_address(ctypes.c_void_p(0x1000), 0x40) == 0x1040)
    import pathlib as _pl
    src = _pl.Path(REPO / "ants" / "dlsssr" / "win32.py").read_text()
    check("win32: pointer-returning kernel32 fns have c_void_p restypes",
          "_k32.GetProcAddress.restype = ctypes.c_void_p" in src
          and "_k32.LoadLibraryExW.restype = ctypes.c_void_p" in src)

    # ---- d3d12 struct packs (sizes/fields per d3d12.h, PE32+) ----
    from ants.dlsssr import d3d12 as d12
    tex = d12._resource_desc_texture(64, 48, d12.DXGI_FORMAT_R8G8B8A8_UNORM,
                                     d12.D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS)
    buf = d12._resource_desc_buffer(4096)
    check("d3d12: RESOURCE_DESC is 56 bytes, TEXTURE2D(3) + zero alignment",
          len(tex) == 56 and len(buf) == 56
          and tex[0:4] == (3).to_bytes(4, "little") and tex[4:12] == b"\x00" * 8)
    check("d3d12: HEAP_TYPE_DEFAULT is 1 (0 = UNKNOWN -> E_INVALIDARG, rig run 11)",
          _d3d12.D3D12_HEAP_TYPE_DEFAULT == 1 and _d3d12.D3D12_HEAP_TYPE_UNKNOWN == 0
          and _d3d12.D3D12_HEAP_TYPE_UPLOAD == 2 and _d3d12.D3D12_HEAP_TYPE_READBACK == 3)
    check("d3d12: buffer desc is BUFFER(1) + ROW_MAJOR layout (driver rule)",
          buf[0:4] == (1).to_bytes(4, "little")
          and buf[44:48] == (1).to_bytes(4, "little"))
    check("d3d12: UAV resource flag is 0x8 (0x4 = render target)",
          d12.D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS == 0x8
          and tex[-8:] == (0x8).to_bytes(8, "little"))
    check("d3d12: heap props 20B, barrier 32B",
          len(d12._heap_properties(0)) == 20
          and len(d12._transition_barrier(0x1234, 0, 2)) == 32)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def ctypes_ptr(cvoid):
    return cvoid.value or 0


def ctypes_addr_of(vtable):
    return ctypes.cast(vtable, ctypes.c_void_p).value or 0


if __name__ == "__main__":
    import ctypes
    sys.exit(main())

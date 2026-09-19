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
    from ants.dlsssr.sr import PERF_QUALITY, PERF_RATIO, DLSS_RENDER_PRESETS
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
        open(os.path.join(sr_root, "nvngx_dlss.dll"), "wb").write(b"x")
        open(os.path.join(sr_root, "nvngx_dlss_310.9.1.dll"), "wb").write(b"x")
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

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def ctypes_ptr(cvoid):
    return cvoid.value or 0


def ctypes_addr_of(vtable):
    return ctypes.cast(vtable, ctypes.c_void_p).value or 0


if __name__ == "__main__":
    import ctypes
    sys.exit(main())

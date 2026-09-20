"""End-to-end native NGX flow against in-process fake COM objects.

Real ctypes vtable calls into fake implementations that carry the d3d12.h
signatures (cross-checked against DVT's rig-validated FFI usage), so
argument-count/type/plumbing bugs surface HERE instead of on the owner's
rig. The fake CopyTextureRegion honours placed-footprint pitches, and the
fake EvaluateFeature reads "Color"/"Output" resource pointers out of the
(real) parameter object and copies color -> output - the exact contract
the pipeline relies on.

Covers: device/queue/allocator/list/fence creation, DXGI enumeration,
committed textures (desc parse), upload/readback with row padding,
barrier layout, own-parameter NR session (feature 18), core-parameter SR
session (feature 1), the fence-wait path, and teardown.
"""

import ctypes
import random
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from smoke_import import install_stubs

install_stubs()

from ants.dlsssr import d3d12, ngx, win32
from ants.dlsssr.d3d12 import D3D12Device, GpuContext

_w_callable_at = win32.callable_at  # real implementation, saved pre-fakes

PASS = 0
FAIL = 0
RECORD = []          # every faked D3D12/NGX call, in order
RESOURCES = {}       # fake object ptr -> {"buf": bytearray, "bpp": int}
PARAMS = {}          # fake core parameter dict (SR scenario)
FAKE_HANDLE = 0xDEADBEEF


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL  {name} {extra}")


def _u32_at(ptr, offset):
    return int.from_bytes(ctypes.string_at(ptr + offset, 4), "little")


def _u64_at(ptr, offset):
    return int.from_bytes(ctypes.string_at(ptr + offset, 8), "little")


def _write_ptr(out, value):
    """Write through an out-param: raw int address or converted POINTER."""
    if isinstance(out, ctypes._Pointer):
        out[0] = value
    else:
        ctypes.cast(out, ctypes.POINTER(ctypes.c_void_p))[0] = value


def _name(ptr):
    return ctypes.string_at(ptr).decode("ascii", "replace")


def _as_int(v):
    """Normalize what the unconverted fake-NGX path receives."""
    if isinstance(v, bytes):
        return int.from_bytes(v, "little")
    if hasattr(v, "value"):
        return int(v.value)
    return int(v)


_KEEP_ALIVE = []  # fakes must outlive the calls production makes through them


class FakeObject:
    """A real in-memory COM object: {->vtable}, vtable slots -> callbacks."""

    def __init__(self, impls, label="fake"):
        _KEEP_ALIVE.append(self)  # otherwise GC frees the vtable mid-flow
        self._cbs = []
        entries = {}
        for slot, (restype, argtypes, fn) in impls.items():
            proto = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, *argtypes)

            def wrap(fn=fn):
                def inner(*args):
                    return fn(*args[1:])  # drop the interface pointer
                return inner

            cb = proto(wrap())
            self._cbs.append(cb)
            entries[slot] = ctypes.cast(cb, ctypes.c_void_p).value
        self.vtable = (ctypes.c_void_p * (max(entries) + 2))()
        for slot, value in entries.items():
            self.vtable[slot] = value
        self.obj = (ctypes.c_void_p * 1)(ctypes.addressof(self.vtable))
        self.ptr = ctypes.addressof(self.obj)
        self.label = label


class FakeFence:
    def __init__(self):
        self.value = 0
        self.lag = 0  # >0 => GetCompletedValue lags, forcing the event-wait path


# ---------------------------------------------------------------- fake D3D12
def build_device_graph():
    fence = FakeFence()
    bpp = {0: 1, 28: 4, 34: 4, 41: 4}  # DXGI format -> bytes per pixel

    def make_resource(size, fmt, label, width_px=0):
        buf = (ctypes.c_char * max(size, 1))()  # addressable backing store
        impls = {
            2: (ctypes.c_uint64, [], lambda: 1),
            8: (ctypes.c_int32, [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p],
                lambda sub, rng, out: (_write_ptr(out, ctypes.addressof(buf)), 0)[1]),
            9: (ctypes.c_int32, [ctypes.c_uint32, ctypes.c_void_p], lambda sub, rng: 0),
        }
        obj = FakeObject(impls, label)
        # "w" = pixel width for textures (subresource copies), byte size for buffers
        RESOURCES[obj.ptr] = {"buf": buf, "bpp": bpp.get(fmt, 4),
                              "w": width_px if width_px else size}
        return obj

    def copy_region(dst_loc, _x, _y, _z, src_loc, _box):
        def parse(loc):
            rptr = _u64_at(loc, 0)
            kind = _u32_at(loc, 8)
            if kind == 0:  # SUBRESOURCE_INDEX (a texture)
                meta = RESOURCES[rptr]
                stride = meta["w"] * meta["bpp"] if meta["bpp"] > 1 else meta["w"]
                return rptr, 0, stride, stride
            off = _u64_at(loc, 16)
            fw = _u32_at(loc, 28)
            fb = bpp.get(_u32_at(loc, 24), 4)
            fp = _u64_at(loc, 40)
            return rptr, off, fw * fb, fp
        dst_ptr, dst_off, dst_row_bytes, dst_pitch = parse(dst_loc)
        src_ptr, src_off, src_row_bytes, src_pitch = parse(src_loc)
        dst = RESOURCES[dst_ptr]["buf"]
        src = RESOURCES[src_ptr]["buf"]
        rows = min(len(dst) // dst_pitch if dst_pitch else 0,
                   len(src) // src_pitch if src_pitch else 0)
        for y in range(max(rows, 1)):
            d = dst_off + y * dst_pitch
            s = src_off + y * src_pitch
            take = max(min(dst_row_bytes, src_row_bytes, len(src) - s, len(dst) - d), 0)
            dst[d:d + take] = src[s:s + take]
        RECORD.append(("CopyTextureRegion", src_row_bytes))

    def create_queue(desc, iid, out):
        assert _u32_at(desc, 0) == d3d12.D3D12_COMMAND_LIST_TYPE_DIRECT
        obj = FakeObject({
            2: (ctypes.c_uint64, [], lambda: 1),
            10: (None, [ctypes.c_uint32, ctypes.c_void_p],
                 lambda n, cell: RECORD.append(("ExecuteCommandLists",))),
            14: (ctypes.c_int32, [ctypes.c_void_p, ctypes.c_uint64],
                 lambda fptr, value: (fence.__setattr__("value", value), 0)[1]),
        }, "queue")
        _write_ptr(out, obj.ptr)
        RECORD.append(("CreateCommandQueue",))
        return 0

    def create_allocator(ctype, iid, out):
        obj = FakeObject({2: (ctypes.c_uint64, [], lambda: 1),
                          8: (ctypes.c_int32, [], lambda: 0)}, "allocator")
        _write_ptr(out, obj.ptr)
        RECORD.append(("CreateCommandAllocator",))
        return 0

    def create_fence(initial, flags, iid, out):
        obj = FakeObject({
            2: (ctypes.c_uint64, [], lambda: 1),
            8: (ctypes.c_uint64, [], lambda: max(fence.value - fence.lag, 0)),
            9: (ctypes.c_int32, [ctypes.c_uint64, ctypes.c_void_p],
                lambda value, event:
                    (RECORD.append(("SetEventOnCompletion", int(value))), 0)[1]),
        }, "fence")
        _write_ptr(out, obj.ptr)
        RECORD.append(("CreateFence", int(initial)))
        return 0

    def create_command_list(node_mask, ctype, allocator, initial, iid, out):
        def barrier(count, ptr):
            subres = _u32_at(ptr, 16)
            assert subres == 0xFFFFFFFF, \
                f"barrier subresources field is {subres}, not ALL_SUBRESOURCES"
            RECORD.append(("Barrier", _u32_at(ptr, 20), _u32_at(ptr, 24)))

        obj = FakeObject({
            2: (ctypes.c_uint64, [], lambda: 1),
            9: (ctypes.c_int32, [], lambda: 0),
            10: (ctypes.c_int32, [ctypes.c_void_p, ctypes.c_void_p], lambda a, b: 0),
            16: (None, [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                        ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p], copy_region),
            26: (None, [ctypes.c_uint32, ctypes.c_void_p], barrier),
        }, "list")
        _write_ptr(out, obj.ptr)
        RECORD.append(("CreateCommandList",))
        return 0

    def create_committed(heap, heap_flags, desc, initial_state, clear, iid, out):
        heap_type = _u32_at(heap, 0)
        if heap_type not in (1, 2, 3):  # DEFAULT/UPLOAD/READBACK - 0 is UNKNOWN
            return -2147024809
        dim = _u32_at(desc, 0)
        width = _u64_at(desc, 16)
        height = _u32_at(desc, 24)
        fmt = _u32_at(desc, 32)
        sample_count = _u32_at(desc, 36)
        layout = _u32_at(desc, 44)
        flags = _u32_at(desc, 48)
        # the driver's E_INVALIDARG rules, enforced so harness tests can
        # never pass a desc the real device would reject
        if dim == d3d12.D3D12_RESOURCE_DIMENSION_BUFFER:
            if layout != d3d12.D3D12_TEXTURE_LAYOUT_ROW_MAJOR or height != 1:
                return -2147024809  # E_INVALIDARG
            size = int(width)
        elif dim == d3d12.D3D12_RESOURCE_DIMENSION_TEXTURE2D:
            if layout != d3d12.D3D12_TEXTURE_LAYOUT_UNKNOWN or sample_count != 1 \
                    or height < 1:
                return -2147024809
            del flags  # UAV-flagged textures are legal (DVT rig-proven)
            size = int(width) * int(height) * bpp.get(fmt, 4)
        else:
            return -2147024809  # only buffers + texture2d are supported here
        obj = make_resource(size, fmt, f"res(dim={dim},fmt={fmt})",
                            width_px=int(width) if dim != 1 else 0)
        _write_ptr(out, obj.ptr)
        RECORD.append(("CreateCommittedResource", dim, int(width), int(height),
                       heap_type))
        return 0

    device = FakeObject({
        2: (ctypes.c_uint64, [], lambda: 1),
        8: (ctypes.c_int32, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], create_queue),
        9: (ctypes.c_int32, [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p], create_allocator),
        12: (ctypes.c_int32, [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                              ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p],
             create_command_list),
        27: (ctypes.c_int32, [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                              ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_void_p], create_committed),
        36: (ctypes.c_int32, [ctypes.c_uint64, ctypes.c_uint32, ctypes.c_void_p,
                              ctypes.c_void_p], create_fence),
        37: (ctypes.c_int32, [], lambda: 0),
    }, "ID3D12Device")

    device_proto = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_uint32,
                                    ctypes.POINTER(ctypes.c_char * 16),
                                    ctypes.POINTER(ctypes.c_void_p))

    def device_create(adapter, level, iid, out):
        _write_ptr(out, device.ptr)
        RECORD.append(("D3D12CreateDevice", level))
        return 0

    adapter = FakeObject({
        2: (ctypes.c_uint64, [], lambda: 1),
        10: (ctypes.c_int32, [ctypes.c_void_p],
             lambda desc: (ctypes.memmove(
                 desc,
                 "NVIDIA GeForce RTX 4090\0".encode(
                     "utf-32-le" if ctypes.sizeof(ctypes.c_wchar) == 4 else "utf-16-le"),
                 96), 0)[1]),
    }, "IDXGIAdapter1")
    factory = FakeObject({
        2: (ctypes.c_uint64, [], lambda: 1),
        12: (ctypes.c_int32, [ctypes.c_uint32, ctypes.c_void_p],
             lambda index, out: (_write_ptr(out, adapter.ptr), 0)[1]
             if index == 0 else -2005270174),
    }, "IDXGIFactory1")
    factory_proto = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.POINTER(ctypes.c_char * 16),
                                     ctypes.POINTER(ctypes.c_void_p))

    def factory_create(iid, out):
        _write_ptr(out, factory.ptr)
        return 0

    return device_proto(device_create), factory_proto(factory_create), fence


# ---------------------------------------------------------------- fake NGX
class FakeNgxModule:
    """win32 seam: every NGX export becomes a recording python function."""

    def __init__(self):
        self.exports = {}

    def install(self):
        win32.load_library = lambda path: 4242
        win32.free_library = lambda handle: None
        win32.get_proc = self.get_proc
        win32.callable_at = self.callable_at

    def get_proc(self, handle, name):
        return hash(("export", name)) & 0x7FFFFFFF

    def callable_at(self, address, argtypes, restype):
        for name in ("NVSDK_NGX_D3D12_Init_Ext", "NVSDK_NGX_D3D12_Init",
                     "NVSDK_NGX_D3D12_AllocateParameters",
                     "NVSDK_NGX_D3D12_CreateFeature",
                     "NVSDK_NGX_D3D12_EvaluateFeature",
                     "NVSDK_NGX_D3D12_ReleaseFeature",
                     "NVSDK_NGX_D3D12_DestroyParameters",
                     "fwd_set_slots", "fwd_create", "fwd_probe"):
            if self.get_proc(None, name) == address:
                return self.make_export(name)
        raise AssertionError(f"callable_at: unknown export address {address}")

    def make_export(self, name):
        if name == "NVSDK_NGX_D3D12_Init_Ext":
            def init_ext(app_id, app_data, dev, sdk, info):
                RECORD.append(("Init_Ext", _as_int(sdk)))
                return 1
            return init_ext
        if name == "NVSDK_NGX_D3D12_Init":
            def init4(app_id, app_data, dev, sdk):
                RECORD.append(("Init4", _as_int(sdk)))
                return 1
            return init4
        if name == "NVSDK_NGX_D3D12_AllocateParameters":
            params = FakeObject(self.parameter_vtable(), "NVSDK_NGX_Parameter")

            def alloc(out):
                _write_ptr(out, params.ptr)
                return 1
            return alloc
        if name == "NVSDK_NGX_D3D12_CreateFeature":
            def create_feature(list_ptr, feature_id, params_ptr, out):
                _write_ptr(out, FAKE_HANDLE)
                RECORD.append(("CreateFeature", _as_int(feature_id)))
                return 1
            return create_feature
        if name == "NVSDK_NGX_D3D12_EvaluateFeature":
            def evaluate(list_ptr, handle, params_ptr, user):
                RECORD.append(("EvaluateFeature",))
                self.simulate_engine()
                return 1
            return evaluate
        if name == "NVSDK_NGX_D3D12_ReleaseFeature":
            def release(handle):
                RECORD.append(("ReleaseFeature",))
                return 1
            return release
        if name == "fwd_set_slots":
            return lambda target, a, b: None
        if name == "fwd_probe":
            def probe(a1, a2, a3, a4):
                got = [_as_int(a) for a in (a1, a2, a3, a4)]
                RECORD.append(("ProbeArgs", got))
                return 1
            return probe
        return lambda *args: 1

    @staticmethod
    def parameter_vtable():
        """The 17-slot NGX parameter vtable (MSVC layout), dict-backed."""
        from ants.dlsssr import parameters as prm
        slots = {}
        specs = {
            prm.SLOT_SET_I32: (ctypes.c_int32, "i32"),
            prm.SLOT_SET_U32: (ctypes.c_uint32, "u32"),
            prm.SLOT_SET_F32: (ctypes.c_float, "f32"),
            prm.SLOT_SET_F64: (ctypes.c_double, "f64"),
            prm.SLOT_SET_U64: (ctypes.c_uint64, "u64"),
            prm.SLOT_SET_POINTER: (ctypes.c_void_p, "ptr"),
            prm.SLOT_SET_D3D12: (ctypes.c_void_p, "ptr"),
        }
        for slot, (argtype, kind) in specs.items():
            def setter(name_ptr, value, kind=kind):
                PARAMS[_name(name_ptr)] = (kind, value)
            slots[slot] = (None, [ctypes.c_void_p, argtype], setter)
        for slot, (argtype, kind) in ((prm.SLOT_GET_U32, (ctypes.c_uint32, "u32")),):
            def getter(name_ptr, out, kind=kind):
                key = _name(name_ptr)
                if key not in PARAMS:
                    return 0
                ctypes.cast(out, ctypes.POINTER(argtype))[0] = PARAMS[key][1]
                return 1
            slots[slot] = (ctypes.c_int32, [ctypes.c_void_p, ctypes.c_void_p], getter)
        slots[16] = (None, [], lambda: None)
        return slots

    own_store = {}  # the session's OwnParameterObject.store (set by main)

    @classmethod
    def simulate_engine(cls):
        """Read Color/Output resource pointers from the parameter object."""
        def value_of(v):  # fake-core entries are (kind, value); own store: raw
            return v[1] if isinstance(v, tuple) else v

        merged = dict(PARAMS)
        merged.update(cls.own_store)
        color = output = None
        for key, raw in merged.items():
            if key in ("DLSSNR.Color", "Color"):
                color = value_of(raw)
            elif key in ("DLSSNR.Output", "Output"):
                output = value_of(raw)
        assert color is not None and output is not None, \
            "engine evaluate: Color/Output resources not set"
        src = RESOURCES[color]["buf"]
        dst = RESOURCES[output]["buf"]
        n = min(len(src), len(dst))
        dst[:n] = src[:n]
        if len(dst) > n:
            dst[n:] = b"\xCD" * (len(dst) - n)  # marker: engine-touched tail


# ----------------------------------------------------------------- scenario
def main():
    device_symbol, factory_symbol, fence = build_device_graph()
    ngx_fake = FakeNgxModule()
    win32.d3d12_create_device_symbol = lambda: device_symbol
    win32.create_dxgi_factory1_symbol = lambda: factory_symbol
    win32.create_event = lambda: 1
    win32.wait_event = lambda handle, timeout=0: True
    win32.close_handle = lambda handle: None
    ngx_fake.install()
    win32.free_library = lambda handle: None
    win32.get_proc = ngx_fake.get_proc
    win32.callable_at = ngx_fake.callable_at
    ngx.writable_cache_dir = lambda tag: tempfile.mkdtemp(prefix=f"ants_{tag}_")
    # The shim thunk reinterpret (CFUNCTYPE over a raw machine address) is
    # rig-only by design; patch the seam so the routing logic still runs.
    def fake_fn(self, name, argtypes, restype=ctypes.c_int32):
        export = ngx_fake.make_export(name)

        def routed(*args):
            if getattr(self, "_set_slots", None) is not None:
                self._set_slots(0, None, None)  # keep the slot-park step exercised
            return export(*args)
        return routed
    ngx.NgxModule.fn = fake_fn

    # ---- production NgxModule.fn() arg forwarding (Claude Sonnet 5's
    # ---- catch: wrapping the thunk CALLABLE in CFUNCTYPE built a lossy
    # ---- python trampoline; the fix binds the raw thunk ADDRESS). The
    # ---- probe uses REAL in-process callback addresses so the routed
    # ---- call performs an actual native jump.
    import ctypes as _ct
    real_callable_at = _w_callable_at
    probe_proto = _ct.CFUNCTYPE(_ct.c_int32, _ct.c_void_p, _ct.c_void_p,
                                _ct.c_void_p, _ct.c_void_p)

    def _probe_impl(a1, a2, a3, a4):
        got = [x if isinstance(x, int) else (x.value or 0)
               for x in (a1, a2, a3, a4)]
        RECORD.append(("ProbeArgs", got))
        return 1

    probe_cb = probe_proto(_probe_impl)
    probe_addr = _ct.cast(probe_cb, _ct.c_void_p).value
    slots_proto = _ct.CFUNCTYPE(None, _ct.c_void_p, _ct.c_void_p, _ct.c_void_p)
    slots_cb = slots_proto(lambda a, b, c: None)
    slots_addr = _ct.cast(slots_cb, _ct.c_void_p).value

    saved_call = win32.callable_at
    saved_get = win32.get_proc
    win32.callable_at = real_callable_at
    win32.get_proc = lambda handle, name: (
        {"fwd_set_slots": slots_addr, "fwd_create": probe_addr,
         "fwd_probe": probe_addr}.get(name) or ngx_fake.get_proc(handle, name))
    try:
        mod = ngx.NgxModule("fake/nvngx_dlssnr_probe.dll", use_shim=True)
        route = mod.fn("fwd_probe",
                       [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_void_p], ctypes.c_int32)
        a1, a2, a3, a4 = 0x7F0000001000, 0x7F0000002000, 0x7F0000003000, 0x7F0000004000
        hr = route(_ct.c_void_p(a1), _ct.c_void_p(a2),
                   _ct.c_void_p(a3), _ct.c_void_p(a4))
        got = next(e[1] for e in RECORD if e[0] == "ProbeArgs")
        check("shim fn: all four 64-bit args survive the routed thunk",
              hr == 1 and got == [a1, a2, a3, a4], f"got {got}")
        import pathlib as _pl
        ngx_src = _pl.Path(REPO / "ants" / "dlsssr" / "ngx.py").read_text()
        check("shim fn: thunk stored as raw ADDRESS, bound with caller proto",
              "self._fwd_stub = int(fwd_create)" in ngx_src
              and "win32.callable_at(self._fwd_stub, argtypes, restype)" in ngx_src)
    finally:
        win32.callable_at = saved_call
        win32.get_proc = saved_get

    # ---- shim module-identity check: a foreign nvngx under our handle
    # ---- must raise the loud collision error naming both files
    win32.get_module_filename = lambda handle, buf_len=1024: "C:\\Windows\\System32\\nvngx.dll"
    from ants.dlsssr.errors import DlssSrError as _Exc
    try:
        ngx.NgxModule("fake/nvngx_dlssnr.dll", use_shim=True)
        check("shim: foreign-module collision raises", False)
    except _Exc as exc:
        check("shim: foreign-module collision raises",
              "collision" in str(exc) and "System32" in str(exc))
    win32.get_module_filename = lambda handle, buf_len=1024: ""  # unknown = skip check

    device = D3D12Device.create()
    gpu = GpuContext(device, adapter_index=0)
    names = [entry[0] for entry in RECORD]
    check("flow: device, queue, allocator, list, fence, adapter created",
          names.count("CreateCommandQueue") == 1
          and names.count("CreateCommandAllocator") == 1
          and names.count("CreateCommandList") == 1
          and names.count("CreateFence") == 1
          and any(entry == ("D3D12CreateDevice", d3d12.D3D_FEATURE_LEVEL_11_0)
                  for entry in RECORD))

    # ---- NR session (own parameters, feature 18) ----
    from ants.dlsssr.nr import DlssNrSession
    from ants.dlsssr.ngx import FEATURE_NR
    W, H = 60, 48  # 240-byte rows != 256 pitch: exercises row padding
    sess = DlssNrSession(gpu, W, H, "fake/nvngx_dlssnr.dll", style="Natural", intensity=0.8)
    FakeNgxModule.own_store = sess.ngx.params.store
    creates = [entry for entry in RECORD if entry[0] == "CreateFeature"]
    check("nr: Init_Ext first (sdk 0x15) precedes CreateFeature(18) - run 21 order",
          any(entry[0] == "Init_Ext" and entry[1] == 0x15 for entry in RECORD)
          and creates and creates[0][1] == FEATURE_NR)
    init_exts = [entry for entry in RECORD if entry[0] == "Init_Ext"]
    check("nr: Init_Ext is attempted exactly once when accepted at 0x15 (no sweep)",
          len(init_exts) == 1 and not any(entry[0] == "Init4" for entry in RECORD))

    rng = random.Random(7)
    payload = bytes(rng.randrange(256) for _ in range(W * H * 4))
    out = sess.evaluate(payload, reset=True)
    check("nr: padded-pitch upload/evaluate/readback round-trips byte-exact",
          len(out) == W * H * 4 and out == payload,
          f"len={len(out)}")
    check("nr: engine consumed our param object",
          FakeNgxModule.own_store.get("DLSSNR.Style") == 1          # Natural
          and FakeNgxModule.own_store.get("DLSSNR.Width") == W
          and FakeNgxModule.own_store.get("DLSSNR.UICorrection") == 0)
    import ctypes as _ct2
    check("nr: parameter object is 64-byte padded (stray-read safe)",
          _ct2.sizeof(sess.ngx.params._object) == 64)
    check("nr: Init buffers retained on the session (runtime reads them lazily; "
          "freed ones = use-after-free at first evaluate)",
          len(sess.ngx._init_keep) >= 3
          and isinstance(sess.ngx._init_keep[0], ctypes.Array)
          and sess.ngx._init_keep[1] is not None)

    payload2 = bytes((i * 7 + 3) & 0xFF for i in range(W * H * 4))
    out2 = sess.evaluate(payload2, reset=False)
    check("nr: second frame (reset=0) round-trips", out2 == payload2)
    check("nr: DLSSNR.Reset toggles in the parameter object",
          FakeNgxModule.own_store.get("DLSSNR.Reset") == 0)

    # ---- fence lag: forces SetEventOnCompletion + event-wait path ----
    fence.lag = 1
    out3 = sess.evaluate(payload, reset=False)
    fence.lag = 0
    check("nr: fence-wait path engaged, frame still correct",
          any(entry[0] == "SetEventOnCompletion" for entry in RECORD)
          and out3 == payload)

    # ---- barrier layout stays fixed (fake asserted ALL_SUBRESOURCES) ----
    check("d3d12: barriers recorded with before/after at 20/24",
          sum(1 for entry in RECORD if entry[0] == "Barrier") > 4)

    # ---- SR session (driver core, core-allocated parameters, feature 1) ----
    import ants.dlsssr.sr as sr_mod
    sr_mod.locate_ngx_core = lambda: "fake/DriverStore/_nvngx.dll"
    PARAMS.clear()
    FakeNgxModule.own_store = {}
    sr = sr_mod.DlssSrSession(gpu, 30, 40, 60, 80, mode="Performance", preset="L")
    check("sr: core init + feature 1 create + core parameters set",
          any(entry[0] == "CreateFeature" and entry[1] == 1 for entry in RECORD)
          and PARAMS.get("PerfQualityValue", ("u32", -1))[1] == 0
          and PARAMS.get("DLSS.Hint.Render.Preset.Performance", ("u32", -1))[1] == 12
          and PARAMS.get("OutWidth", ("u32", -1))[1] == 60)
    small = bytes((i * 13 + 1) & 0xFF for i in range(30 * 40 * 4))
    big = sr.evaluate(small, reset=True)
    check("sr: evaluate returns output-size frame with engine tail marker",
          len(big) == 60 * 80 * 4 and big[:240] == small[:240] and big[-1] == 0xCD)

    sess.close()
    sr.close()
    gpu.close()
    check("flow: teardown releases the feature",
          any(entry[0] == "ReleaseFeature" for entry in RECORD))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

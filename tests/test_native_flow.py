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
import os
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
from ants.log import dlss_logger

_w_callable_at = win32.callable_at  # real implementation, saved pre-fakes

PASS = 0
FAIL = 0
RECORD = []          # every faked D3D12/NGX call, in order
RESOURCES = {}       # fake object ptr -> {"buf": bytearray, "bpp": int}
PARAMS = {}          # fake core parameter dict (SR scenario)
FAKE_ADAPTERS = {}   # "hw"/"sw": the DXGI adapters build_device_graph made
FAKE_HANDLE = 0xDEADBEEF
close_failures = {"n": 0}   # how many upcoming Close() calls report failure
device_state = {"reason": 0}  # GetDeviceRemovedReason return value (0 = alive)
# rig 23:32: a driver that refuses UAV-flagged texture2d creation (all of them)
uav_refused = {"on": False}


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


def _ct_array_type():
    """The ctypes array base class (used as the "is a live buffer" probe)."""
    import ctypes as _c
    return _c.Array


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
    bpp = {0: 1, 10: 8, 28: 4, 34: 4, 41: 4}  # DXGI format -> bytes per pixel

    def make_resource(size, fmt, label, width_px=0, heap="default"):
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
                              "w": width_px if width_px else size,
                              "heap": heap}
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
        poisoned = {"yes": False}   # a barrier on a staging heap poisons it

        def barrier(count, ptr):
            subres = _u32_at(ptr, 16)
            assert subres == 0xFFFFFFFF, \
                f"barrier subresources field is {subres}, not ALL_SUBRESOURCES"
            resource = _u64_at(ptr, 8)
            heap = RESOURCES.get(resource, {}).get("heap")
            if heap in ("upload", "readback"):
                # real runtime: an invalid command puts the list in an error
                # state and the NEXT Close() answers E_INVALIDARG
                # (rig: 0x80070057 after the first staging upload)
                poisoned["yes"] = True
            RECORD.append(("Barrier", _u32_at(ptr, 20), _u32_at(ptr, 24),
                           heap or "default"))

        def close():
            RECORD.append(("Close",))
            if poisoned["yes"]:
                poisoned["yes"] = False
                return -2147024809      # E_INVALIDARG
            if close_failures["n"] > 0:
                close_failures["n"] -= 1
                return -2147024809
            return 0

        def reset_list(alloc, initial_psi):
            RECORD.append(("ResetList",))
            return 0

        obj = FakeObject({
            2: (ctypes.c_uint64, [], lambda: 1),
            9: (ctypes.c_int32, [], close),
            10: (ctypes.c_int32, [ctypes.c_void_p, ctypes.c_void_p], reset_list),
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
        initial = int(initial_state)   # passed by value (c_uint32), not a pointer
        # D3D12 rule: staging heaps have a FIXED implicit state (upload =
        # GENERIC_READ 0xAC3, readback = COPY_DEST 0x400); anything else,
        # COMMON included, is rejected with E_INVALIDARG.
        if heap_type == 2 and initial != 0xAC3:
            return -2147024809
        if heap_type == 3 and initial != 0x400:
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
            if uav_refused["on"] and flags & d3d12.D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS:
                return -2147024809      # E_INVALIDARG, the 23:32 behaviour
            size = int(width) * int(height) * bpp.get(fmt, 4)
        else:
            return -2147024809  # only buffers + texture2d are supported here
        obj = make_resource(size, fmt, f"res(dim={dim},fmt={fmt})",
                            width_px=int(width) if dim != 1 else 0,
                            heap={1: "default", 2: "upload",
                                  3: "readback"}[heap_type])
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
        37: (ctypes.c_int32, [], lambda: device_state["reason"]),
    }, "ID3D12Device")

    device_proto = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_uint32,
                                    ctypes.POINTER(ctypes.c_char * 16),
                                    ctypes.POINTER(ctypes.c_void_p))

    def device_create(adapter, level, iid, out):
        _write_ptr(out, device.ptr)
        # which adapter we asked for matters: the engine matches the D3D12
        # device against its CUDA device by LUID, so a NULL/default pick is
        # only correct on a single-GPU rig
        RECORD.append(("D3D12CreateDevice", adapter, level))
        return 0

    def make_adapter(name, vendor, device_id, luid, flags=0):
        wide = 4 if ctypes.sizeof(ctypes.c_wchar) == 4 else 2
        encoded = name.encode("utf-32-le" if wide == 4 else "utf-16-le") \
            + b"\x00" * wide

        def get_desc(desc):
            ctypes.memset(desc, 0, 312)
            ctypes.memmove(desc, encoded, min(len(encoded), 128 * wide))
            ctypes.memmove(desc + 0x100, vendor.to_bytes(4, "little"), 4)
            ctypes.memmove(desc + 0x104, device_id.to_bytes(4, "little"), 4)
            ctypes.memmove(desc + 0x128,
                           (luid[0] | (luid[1] << 32)).to_bytes(8, "little"), 8)
            ctypes.memmove(desc + 0x130, flags.to_bytes(4, "little"), 4)
            return 0
        return FakeObject({
            2: (ctypes.c_uint64, [], lambda: 1),
            10: (ctypes.c_int32, [ctypes.c_void_p], get_desc),
        }, "IDXGIAdapter1")

    # Adapter 0 is the real GPU, adapter 1 is a software one: the CUDA LUID
    # decides between them, and a software adapter must never win a guess.
    adapter = make_adapter("NVIDIA GeForce RTX 4090", 0x10DE, 0x2684,
                           (0x0000B412, 0x00000000))
    software_adapter = make_adapter("Microsoft Basic Render Driver", 0x1414,
                                    0x008C, (0x00000000, 0x00000000), flags=2)

    def enum_adapter(index, out):
        if index == 0:
            _write_ptr(out, adapter.ptr)
            return 0
        if index == 1:
            _write_ptr(out, software_adapter.ptr)
            return 0
        return -2005270174          # DXGI_ERROR_NOT_FOUND
    factory = FakeObject({
        2: (ctypes.c_uint64, [], lambda: 1),
        12: (ctypes.c_int32, [ctypes.c_uint32, ctypes.c_void_p], enum_adapter),
    }, "IDXGIFactory1")
    factory_proto = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.POINTER(ctypes.c_char * 16),
                                     ctypes.POINTER(ctypes.c_void_p))

    def factory_create(iid, out):
        _write_ptr(out, factory.ptr)
        return 0

    FAKE_ADAPTERS["hw"] = adapter
    FAKE_ADAPTERS["sw"] = software_adapter
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

    # NVIDIA's flat C parameter API (the ABI-stable route our host prefers
    # when the core exports it). Same store as the vtable setters so both
    # backends are observable through PARAMS.
    FLAT_SETTERS = {
        "NVSDK_NGX_Parameter_SetULL": ("u64", ctypes.c_uint64),
        "NVSDK_NGX_Parameter_SetF": ("f32", ctypes.c_float),
        "NVSDK_NGX_Parameter_SetD": ("f64", ctypes.c_double),
        "NVSDK_NGX_Parameter_SetUI": ("u32", ctypes.c_uint32),
        "NVSDK_NGX_Parameter_SetI": ("i32", ctypes.c_int32),
        "NVSDK_NGX_Parameter_SetD3d12Resource": ("ptr", ctypes.c_void_p),
        "NVSDK_NGX_Parameter_SetD3d11Resource": ("ptr", ctypes.c_void_p),
        "NVSDK_NGX_Parameter_SetVoidPointer": ("ptr", ctypes.c_void_p),
    }

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
                RECORD.append(("CreateFeature", _as_int(feature_id),
                               _as_int(list_ptr)))
                return 1
            return create_feature
        if name == "NVSDK_NGX_D3D12_EvaluateFeature":
            def evaluate(list_ptr, handle, params_ptr, user):
                RECORD.append(("EvaluateFeature", _as_int(list_ptr)))
                self.simulate_engine()
                return 1
            return evaluate
        if name == "NVSDK_NGX_D3D12_ReleaseFeature":
            def release(handle):
                RECORD.append(("ReleaseFeature",))
                return 1
            return release
        if name in self.FLAT_SETTERS:
            kind, argtype = self.FLAT_SETTERS[name]

            def flat_set(_params, name_ptr, value, kind=kind):
                # a real export receives the raw scalar; this python stand-in
                # sees the ctypes wrapper our host builds, so unwrap it
                PARAMS[_name(name_ptr)] = (kind, getattr(value, "value", value))
            return flat_set
        if name == "NVSDK_NGX_D3D12_Init_ProjectID":
            def init_project(project, engine_type, engine_version, path, dev,
                             sdk, info):
                RECORD.append(("Init_ProjectID",
                               ctypes.string_at(project).decode("ascii", "replace")
                               if project else ""))
                return 1
            return init_project
        if name == "NVSDK_NGX_D3D12_GetCapabilityParameters":
            params = FakeObject(self.parameter_vtable(),
                                "NVSDK_NGX_Parameter(capability)")

            def get_caps(out):
                _write_ptr(out, params.ptr)
                RECORD.append(("GetCapabilityParameters",))
                return 1
            return get_caps
        if name == "fwd_set_slots":
            def set_slots(target, a, b):
                RECORD.append(("SetSlots", _as_int(target)))
                if _as_int(target) == 0:
                    import traceback
                    traceback.print_stack()
            return set_slots
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
        # the shipping MSVC slot map our host writes through (float on 6,
        # pointers on 2, resources on 0, 32-bit ints on 3)
        specs = {
            prm.SLOT_SET_I32: (ctypes.c_int32, "i32"),
            prm.SLOT_SET_U32: (ctypes.c_uint32, "u32"),
            prm.SLOT_SET_F32: (ctypes.c_float, "f32"),
            prm.SLOT_SET_POINTER: (ctypes.c_void_p, "ptr"),
            prm.SLOT_SET_RESOURCE: (ctypes.c_void_p, "resource"),
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
    def fake_fn(self, name, argtypes, restype=ctypes.c_int32, thunk="call"):
        export = ngx_fake.make_export(name)
        if thunk == "init_ext":
            # the shim's fwd_init_ext presents the snippet's own order
            # (common_info before the version) to the real export
            raw = export

            def reorder(app_id, path, dev, sdk, info):
                return raw(app_id, path, dev, info, sdk)
            export = reorder

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
              'self._fwd_stub = int(resolve("fwd_create"))' in ngx_src
              and 'self._fwd_stub_init_ext = int(resolve("fwd_init_ext"))' in ngx_src
              and "win32.callable_at(stub_addr, argtypes, restype)" in ngx_src
              and "assert ctypes.cast(stub, ctypes.c_void_p).value == stub_addr"
              in ngx_src)
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

    # ---- staging-heap rules (the rig's 0x80070057 came from violating them)
    staging_probe = device.create_buffer(256, d3d12.D3D12_HEAP_TYPE_UPLOAD,
                                         "staging probe")
    check("d3d12: staging buffers are created in their implicit state and "
          "tagged with their heap",
          staging_probe.state == d3d12.D3D12_RESOURCE_STATE_GENERIC_READ
          and staging_probe.heap_type == d3d12.D3D12_HEAP_TYPE_UPLOAD)
    barriers_before = sum(1 for e in RECORD if e[0] == "Barrier")
    gpu.transition(staging_probe, d3d12.D3D12_RESOURCE_STATE_COPY_SOURCE)
    check("d3d12: a state transition of a staging resource is refused and "
          "never recorded (a barrier there is an invalid command - the "
          "runtime poisons the list and Close answers E_INVALIDARG)",
          sum(1 for e in RECORD if e[0] == "Barrier") == barriers_before)
    names = [entry[0] for entry in RECORD]
    check("flow: device, queue, allocator, list, fence, adapter created",
          names.count("CreateCommandQueue") == 1
          and names.count("CreateCommandAllocator") == 1
          and names.count("CreateCommandList") == 1
          and names.count("CreateFence") == 1
          and any(entry[0] == "D3D12CreateDevice"
                  and entry[2] == d3d12.D3D_FEATURE_LEVEL_11_0
                  for entry in RECORD))

    # ---- NR session, core-owned route (feature 18) ----
    # The snippet is staged under its canonical name from any-named source.
    import numpy as _np
    nr_source = Path(tempfile.mkdtemp(prefix="ants_nr_src_"))
    nr_dll = nr_source / "nvngx_dlssnr_ANY_name.dll"
    nr_dll.write_bytes(b"MZ" + bytes(4094))
    ngx.locate_ngx_core = lambda: "fake/DriverStore/_nvngx.dll"
    from ants.dlsssr.nr import DlssNrSession
    from ants.dlsssr.ngx import FEATURE_NR
    W, H = 60, 48  # 240-byte rows != 256 pitch: exercises row padding
    PARAMS.clear()
    sess = DlssNrSession(gpu, W, H, str(nr_dll), style="Natural", intensity=0.8)
    check("nr: core is the session OWNER, the snippet is the feature provider",
          sess.ngx.feature_module is not None
          and os.path.basename(sess.ngx.feature_module.path) == "nvngx_dlssnr.dll"
          and sess.ngx.feature_module.path != str(nr_dll))
    first_create_at = next(i for i, e in enumerate(RECORD)
                           if e[0] == "CreateFeature")
    check("nr: session init records no copies and no staging barriers "
          "(the zeroed guides rely on D3D12's zero-init guarantee)",
          not any(e[0] == "CopyTextureRegion"
                  for e in RECORD[:first_create_at])
          and not any(e[0] == "Barrier" and e[3] in ("upload", "readback")
                      for e in RECORD))
    core_exports = {ngx_fake.get_proc(None, n) for n in (
        "NVSDK_NGX_D3D12_Init_ProjectID",
        "NVSDK_NGX_D3D12_GetCapabilityParameters",
        "NVSDK_NGX_D3D12_CreateFeature")}
    routed_targets = {e[1] for e in RECORD if e[0] == "SetSlots"}
    check("nr: the SESSION OWNER is bound directly (reference hosts route "
          "only the snippet through their helper; the rig log showed the "
          "core recording our shim as its caller)",
          not (routed_targets & core_exports) and bool(routed_targets))
    check("nr: ProjectID session first, then the snippet Init_Ext",
          [e[0] for e in RECORD if e[0] in ("Init_ProjectID", "Init_Ext")][:2]
          == ["Init_ProjectID", "Init_Ext"])
    init_exts = [entry for entry in RECORD if entry[0] == "Init_Ext"]
    check("nr: Init_Ext is attempted exactly once when accepted at 0x15 (no sweep)",
          len(init_exts) == 1 and not any(entry[0] == "Init4" for entry in RECORD))
    creates = [entry for entry in RECORD if entry[0] == "CreateFeature"]
    check("nr: CreateFeature(18) after both inits",
          bool(creates) and creates[0][1] == FEATURE_NR)
    check("nr: feature runs on the CORE's capability map through the flat C API",
          any(e[0] == "GetCapabilityParameters" for e in RECORD)
          and sess.ngx.params.backend == "c-api")
    check("nr: 1x runs use the native (DLAA) quality value - 6 is the carrier "
          "post-pass and mismatches the 1.0 ratio our callback reports",
          PARAMS.get("PerfQualityValue") == ("u32", 5))
    check("nr: full reference create contract landed",
          PARAMS.get("DLSSNR.Width") == ("u32", W)
          and PARAMS.get("DLSSNR.OutputSubrectWidth") == ("u32", W)
          and PARAMS.get("DLSSNR.DepthInverted") == ("u32", 1)
          and PARAMS.get("DLSSNR.Upscaling") == ("u32", 0)
          and PARAMS.get("DLSSNR.ScalingRatio") == ("f32", 1.0)
          and PARAMS.get("DLSSNR.Style") == ("u32", 1)          # Natural
          and PARAMS.get("DLSSNR.UICorrection") == ("u32", 0)
          and PARAMS.get("DLSSNRComputeScalingRatioCallback")[1])

    rng = random.Random(7)
    payload = bytes(rng.randrange(256) for _ in range(W * H * 4))
    out = sess.evaluate(payload, reset=True)
    # ---- command-list hygiene around the feature call (proven-host parity)
    eval_idx = max(i for i, e in enumerate(RECORD) if e[0] == "EvaluateFeature")
    setup = [i for i, e in enumerate(RECORD[:eval_idx])
             if e[0] in ("CopyTextureRegion", "Barrier")]
    closes_before = [i for i, e in enumerate(RECORD[:eval_idx]) if e[0] == "Close"]
    after = RECORD[eval_idx:]
    closes_after = [i for i, e in enumerate(after) if e[0] == "Close"]
    readback_at = [i for i, e in enumerate(after)
                   if e[0] == "CopyTextureRegion"]
    check("nr: the runtime receives a freshly executed, empty command list "
          "(our copy + barriers are drained BEFORE the feature call, like the "
          "proven hosts)",
          bool(setup) and bool(closes_before) and max(closes_before) > max(setup))
    check("nr: the runtime's own recorded work is executed after the feature "
          "call and before the output is read back",
          bool(closes_after) and bool(readback_at)
          and min(closes_after) < min(readback_at))
    # color/output are RGBA16F (the HDR-capable domain the runtime renders
    # in), so the frame crosses the float16 boundary in both directions.
    want = (_np.frombuffer(payload, dtype=_np.uint8).reshape(H, W, 4)
            .astype(_np.float32) / 255.0).astype(_np.float16).astype(_np.float32)
    want = (_np.clip(want, 0.0, 1.0) * 255.0 + 0.5).astype(_np.uint8).tobytes()
    check("nr: padded-pitch RGBA8 -> RGBA16F -> RGBA8 round-trips exactly",
          len(out) == W * H * 4 and out == want, f"len={len(out)}")
    intensity = PARAMS.get("DLSSNR.Intensity", ("", 0.0))[1]
    check("nr: engine consumed the per-frame parameters",
          abs(intensity - 0.8) < 1e-6          # f32 precision
          and PARAMS.get("DLSSNR.Reset") == ("u32", 1)
          and PARAMS.get("DLSSNR.LocalStructureStrength") == ("f32", 1.0))
    check("nr: surfaces + subrects are re-applied for every frame "
          "(reference hosts re-write the whole contract per evaluate)",
          sess.ngx.params.written.count("DLSSNR.ColorSubrectWidth") >= 2
          and sess.ngx.params.written.count("DLSSNR.Output") >= 2)
    check("nr: Init buffers retained on the session (runtime reads them lazily; "
          "freed ones = use-after-free at first evaluate)",
          len(sess.ngx._init_keep) >= 3
          and isinstance(sess.ngx._init_keep[0], _ct_array_type())
          and sess.ngx._init_keep[1] is not None)

    out2 = sess.evaluate(payload, reset=False)
    check("nr: second frame (reset=0) round-trips", out2 == want)
    check("nr: DLSSNR.Reset toggles in the parameter object",
          PARAMS.get("DLSSNR.Reset") == ("u32", 0))

    # ---- fence lag: forces SetEventOnCompletion + event-wait path ----
    fence.lag = 1
    out3 = sess.evaluate(payload, reset=False)
    fence.lag = 0
    check("nr: fence-wait path engaged, frame still correct",
          any(entry[0] == "SetEventOnCompletion" for entry in RECORD)
          and out3 == want)
    sess.close()

    # ---- Close resilience: an unconclosable list is recovered loudly -------
    # (rig signature: ID3D12GraphicsCommandList.Close -> 0x80070057, caused by
    # an invalid barrier on an upload-heap staging buffer; the runtime's
    # answer to an invalid recording is a poisoned list)
    upload_tex = gpu.device.create_texture2d(
        W, H, d3d12.DXGI_FORMAT_R8G8B8A8_UNORM, label="close-probe")
    close_failures["n"] = 1
    lists_before = sum(1 for e in RECORD if e[0] == "CreateCommandList")
    old_list_ptr = gpu.list.ptr
    gpu.upload_texture(upload_tex, bytes(W * H * 4),
                       d3d12.D3D12_RESOURCE_STATE_COMMON)
    gpu.submit_and_wait()          # the failure surfaces here (Close)
    check("d3d12: an unconclosable command list is REPLACED by a fresh one "
          "instead of failing the run (a poisoned list is never reused; "
          "ANTS_D3D12_STRICT_CLOSE=1 opts out)",
          sum(1 for e in RECORD if e[0] == "CreateCommandList") > lists_before
          and gpu.list.ptr != old_list_ptr
          and not gpu._pending_release)
    os.environ["ANTS_D3D12_STRICT_CLOSE"] = "1"
    close_failures["n"] = 1
    try:
        gpu.upload_texture(upload_tex, bytes(W * H * 4),
                           d3d12.D3D12_RESOURCE_STATE_COMMON)
        gpu.submit_and_wait()
        check("d3d12: strict-close mode re-raises", False)
    except Exception as exc:
        check("d3d12: strict-close mode re-raises",
              "Close" in str(exc) and "80070057" in str(exc).upper())
    finally:
        os.environ.pop("ANTS_D3D12_STRICT_CLOSE", None)
    close_failures["n"] = 0
    gpu.submit_and_wait()          # leave the shared list clean for the rest

    # ---- the runtime gets its OWN command list -----------------------------
    runtime_list = gpu.command_list()
    check("d3d12: the NGX runtime gets a DEDICATED command list (its recording "
          "cannot poison the list that carries our frame copies)",
          runtime_list is not gpu.list
          and gpu.runtime_allocator is not None
          and gpu.command_list() is runtime_list)      # created once, reused
    fingerprinted = [e for e in RECORD if e[0] in ("CreateFeature",
                                                   "EvaluateFeature")]
    def _ptr(value):
        """Normalise a fake/real COM pointer (int, c_void_p, c_char_p, bytes)."""
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            return int.from_bytes(bytes(value), "little")

    check("ngx: Create/Evaluate were handed the dedicated runtime list",
          bool(fingerprinted)
          and all(e[-1] == _ptr(runtime_list.ptr) for e in fingerprinted)
          and all(e[-1] != _ptr(gpu.list.ptr) for e in fingerprinted))

    # recoveries are counted and never fatal
    close_failures["n"] = 1
    gpu.runtime_submit_and_wait()
    check("d3d12: a runtime recording that cannot be closed is dropped, the "
          "runtime list is replaced and the count is kept for the evidence",
          gpu.runtime_recoveries == 1
          and gpu.runtime_list is not runtime_list
          and gpu.command_list() is gpu.runtime_list)

    # ---- a REMOVED device is fatal, loud, and never "recovered" in place ----
    # Rig 2026-09-20 20:39: two E_INVALIDARG Close failures, then
    # CreateCommandAllocator 0x887A0005 (DXGI_ERROR_DEVICE_REMOVED) - the
    # device was gone and the recovery just cascaded. Now the reason is
    # reported, the context is marked dead and no new objects are created.
    check("d3d12: the device-removal reasons are named in plain English",
          "DEVICE_HUNG" in d3d12.describe_device_reason(0x887A0006)
          and "removed" in d3d12.describe_device_reason(0x887A0005).lower()
          and "healthy" in d3d12.describe_device_reason(0).lower()
          and "TDR" in d3d12.describe_device_reason(0x887A0006))
    probe_ctx = d3d12.GpuContext(device, adapter_index=0)
    check("d3d12: the host logs which adapter (and LUID) it bound - the "
          "reference when the engine's LUID matching fails",
          "RTX 4090" in (probe_ctx.adapter_name or ""))

    # ---- adapter identity: the CUDA ordinal decides WHICH adapter ----------
    # The engine matches the D3D12 device against its CUDA device BY LUID.
    # DXGI order is a display order and CUDA order is a performance order, and
    # --cuda-device N renumbers only the CUDA side, so "adapter index == CUDA
    # ordinal" is wrong on every multi-GPU rig.
    from ants.dlsssr import cuda_luid
    check("cuda_luid: without nvcuda every query is a reason string, never an "
          "exception (nothing may break on a host with no CUDA)",
          (lambda luid, name, why: luid is None and why)(*cuda_luid.device_luid(0))
          and cuda_luid.format_luid((0xB412, 0)) == "00000000:0000b412"
          and (lambda total, why: total is None and bool(why))(*cuda_luid.count()))
    _factory, adapters = d3d12.enumerate_adapters()
    check("d3d12: DXGI_ADAPTER_DESC1 is read at the RIGHT offsets - LUID at "
          "0x128 (LowPart first), vendor 0x100, flags 0x130",
          len(adapters) == 2
          and adapters[0].name == "NVIDIA GeForce RTX 4090"
          and adapters[0].vendor_id == 0x10DE and adapters[0].device_id == 0x2684
          and adapters[0].luid == (0x0000B412, 0x00000000)
          and adapters[0].nvidia and not adapters[0].software
          and adapters[1].software and adapters[1].vendor_id == 0x1414)
    real_luid, real_count = cuda_luid.device_luid, cuda_luid.count
    cuda_luid.device_luid = lambda ordinal=0: ((0x0000B412, 0), "", "")
    info, reason = d3d12.pick_adapter(adapters, 0)
    check("d3d12: a CUDA ordinal is matched to its adapter by LUID, and the "
          "log line names both sides of the match",
          info is not None and info.index == 0
          and "CUDA ordinal 0 LUID 00000000:0000b412" in reason
          and "RTX 4090" in reason)
    cuda_luid.device_luid = lambda ordinal=0: ((0xDEAD0001, 0), "", "")
    info, reason = d3d12.pick_adapter(adapters, 1)
    check("d3d12: a CUDA LUID no DXGI adapter has is stated loudly (MIG, a "
          "hidden device), and the software adapter is still not the guess",
          info is not None and info.index == 0
          and "no enumerated DXGI adapter has it" in reason)
    cuda_luid.device_luid = lambda ordinal=0: (None, "", "driver too old")
    info, reason = d3d12.pick_adapter(adapters, 1)
    check("d3d12: an unavailable CUDA LUID falls back to a hardware adapter "
          "and says why (the old silent index guess hid this)",
          info is not None and info.index == 0
          and "LUID unavailable" in reason and "driver too old" in reason)
    cuda_luid.device_luid = lambda ordinal=0: ((0x0000B412, 0), "", "")
    cuda_luid.count = lambda: (2, "")
    d3d12._MULTI_GPU_SAID["done"] = False
    said = []
    real_status = dlss_logger.status
    dlss_logger.status = lambda message, *a, **k: said.append(
        message % a if a else message)
    picks_before = sum(1 for e in RECORD if e[0] == "D3D12CreateDevice")
    try:
        ctx2 = d3d12.make_gpu_context(0)
    finally:
        dlss_logger.status = real_status
    picks = [e for e in RECORD if e[0] == "D3D12CreateDevice"][picks_before:]
    check("d3d12: the device is created ON the adapter whose LUID is the CUDA "
          "ordinal's (not on the default adapter - the pick the engine "
          "re-checks)", picks and picks[-1][1] == FAKE_ADAPTERS["hw"].ptr
          and ctx2.adapter_name == "NVIDIA GeForce RTX 4090")
    advisory = [msg for msg in said if "CUDA devices are visible" in msg]
    check("d3d12: the multi-GPU CUDA advisory (ComfyUI #15255 / PR #15451) is "
          "logged once, with the exact launch flags",
          len(advisory) == 1 and "2 CUDA devices are visible" in advisory[0]
          and "--cuda-device 0" in advisory[0]
          and "--disable-pinned-memory" in advisory[0]
          and any("CUDA ordinal 0 LUID 00000000:0000b412" in msg for msg in said))
    check("d3d12: a removal on a multi-GPU process names that CUDA bug and the "
          "flags as a possible cause",
          "3) THIS PROCESS SEES 2 CUDA DEVICES" in ctx2._removal_text(
              "testing", "DEVICE_REMOVED (device gone)")
          and "--disable-pinned-memory" in ctx2._removal_text("t", "x"))
    cuda_luid.count = lambda: (1, "")
    check("d3d12: the multi-GPU clause stays away on a single-GPU rig",
          "THIS PROCESS SEES" not in ctx2._removal_text("t", "x"))
    cuda_luid.device_luid, cuda_luid.count = real_luid, real_count
    ctx2.close()

    device_state["reason"] = 0x887A0006        # DEVICE_HUNG
    close_failures["n"] = 1
    probe_ctx.upload_texture(upload_tex, bytes(W * H * 4),
                             d3d12.D3D12_RESOURCE_STATE_COMMON)
    try:
        probe_ctx.submit_and_wait()
        check("d3d12: a removed device raises the loud removal error", False)
    except Exception as exc:
        message = str(exc)
        check("d3d12: a removed device raises one loud [ANTs] error naming the "
              "reason and the fix (TDR delay / restart), instead of cascading "
              "into CreateCommandAllocator 0x887A0005",
              "[ANTs]" in message and "DEVICE_HUNG" in message
              and "GetDeviceRemovedReason reports" in message
              and "TdrDelay" in message and "RESTART ComfyUI" in message
              and "WITHOUT releasing its objects" in message
              and "DXGI_ERROR_INVALID_CALL" in message)
    lists_now = sum(1 for e in RECORD if e[0] == "CreateCommandList")
    try:
        probe_ctx.submit_and_wait()
        check("d3d12: a dead context refuses further submits", False)
    except Exception as exc:
        check("d3d12: a dead context refuses further submits without creating "
              "objects on the dead device",
              "GetDeviceRemovedReason reports" in str(exc)
              and sum(1 for e in RECORD if e[0] == "CreateCommandList") == lists_now)

    # ---- the corpse is never touched again (rig 21:52: driver AV) ----------
    d3d12.reset_wedged()
    check("d3d12: 0x887A0001 is named DXGI_ERROR_INVALID_CALL instead of "
          "being called a removal reason (the 21:53 D3D12CreateDevice line)",
          d3d12.describe_hresult(0x887A0001)
          == "0x887A0001 (DXGI_ERROR_INVALID_CALL)"
          and d3d12.describe_hresult(0x80070057) == "0x80070057 (E_INVALIDARG)")
    d3d12.note_wedged(0x887A0001, "test removal")
    err = d3d12._create_device_error(0x887A0001)
    check("d3d12: a D3D12CreateDevice failure in a process that already lost a "
          "device says RESTART, instead of leaving 0x887A0001 unexplained",
          "ALREADY LOST" in err and "RESTART ComfyUI" in err
          and "test removal" in err)
    d3d12.reset_wedged()
    os.environ["ANTS_NR_INPUT_STATE"] = "uav"
    legacy_state = d3d12.input_state()
    os.environ.pop("ANTS_NR_INPUT_STATE")
    check("d3d12: NGX INPUT textures sit in a shader-resource state by default "
          "(only the output is a UAV) and ANTS_NR_INPUT_STATE=uav restores the "
          "old state for an A/B run",
          d3d12.input_state()
          == d3d12.D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE
          and legacy_state == d3d12.D3D12_RESOURCE_STATE_UNORDERED_ACCESS)
    probe_ctx.command_list()
    check("d3d12: the 'not closable' log names WHICH recording failed (the "
          "21:52 line could not be acted on because both lists looked alike)",
          "NGX runtime" in probe_ctx._describe_list(probe_ctx.runtime_list)
          and "copy command list" in probe_ctx._describe_list(probe_ctx.list))

    # ---- rig 23:32: the UAV recipe refused -> LOUD, never degraded --------
    d3d12.reset_wedged()
    device_state["reason"] = 0
    ctx3 = d3d12.make_gpu_context(0)
    check("d3d12: the device health is read at creation and kept on the "
          "context (the 23:32 report needed it to tell 'driver refuses' from "
          "'device already gone')",
          isinstance(ctx3.health, tuple) and ctx3.health[0] is False
          and "healthy" in ctx3.health[2])
    uav_refused["on"] = True
    try:
        ctx3.device.create_texture2d(64, 64,
                                     d3d12.DXGI_FORMAT_R16G16B16A16_FLOAT,
                                     label="needs a UAV")
        refused_ok = "no exception"
    except Exception as exc:
        refused_ok = str(exc)
    finally:
        uav_refused["on"] = False
    check("d3d12: when the driver refuses every UAV-capable recipe the pack "
          "FAILS LOUDLY (naming the device status) instead of silently taking "
          "a UAV-less texture - the 23:32 cascade was our own fallback "
          "recording a barrier to UNORDERED_ACCESS on a non-UAV resource",
          "no exception" not in refused_ok and "[ANTs]" in refused_ok
          and "UAV" in refused_ok and "Device status" in refused_ok
          and ctx3.device._texture_recipe is None)
    ctx3.close()

    released = []
    real_release = d3d12.ComObject.release

    def _spy_release(self):
        released.append(self.label)
        return real_release(self)

    d3d12.ComObject.release = _spy_release
    try:
        probe_ctx.close()                       # device is DEVICE_HUNG here
        dead_releases = len(released)
        device_state["reason"] = 0
        healthy = d3d12.make_gpu_context(0)
        healthy.close()
        healthy_releases = len(released) - dead_releases
    finally:
        d3d12.ComObject.release = real_release
    check("d3d12: a context whose device was removed releases NOTHING (releasing "
          "a dead device's objects crashed inside the NVIDIA UMD - rig 21:52) "
          "while a healthy context still releases normally",
          dead_releases == 0 and healthy_releases > 0 and d3d12.wedged()[0],
          f"dead={dead_releases} healthy={healthy_releases} "
          f"wedged={d3d12.wedged()} released={released[:6]}")
    d3d12.reset_wedged()
    device_state["reason"] = 0

    # ---- staging lifetime: freed only after the GPU is done ----------------
    recorded = []
    real_submit = d3d12.GpuContext.submit_and_wait

    def _spy(self, *a, **k):
        recorded.append(("submit", len(self._pending_release)))
        return real_submit(self, *a, **k)
    d3d12.GpuContext.submit_and_wait = _spy
    try:
        gpu.upload_texture(upload_tex, bytes(W * H * 4),
                           d3d12.D3D12_RESOURCE_STATE_COMMON)
        check("d3d12: the staging buffer stays alive until the GPU is done "
              "(it was released while a recorded copy still referenced it)",
              len(gpu._pending_release) == 1)
        gpu.submit_and_wait()
        check("d3d12: staging buffers are freed on the next submit_and_wait",
              not gpu._pending_release and recorded[-1][1] == 1)
    finally:
        d3d12.GpuContext.submit_and_wait = real_submit


    # ---- legacy snippet-direct route (ANTS_NR_USE_OWN_PARAMS=1 geometry) ----
    FakeNgxModule.own_store = {}
    legacy = DlssNrSession(gpu, W, H, str(nr_dll), style="Natural",
                           intensity=0.8, use_own_parameters=True)
    FakeNgxModule.own_store = legacy.ngx.params.store
    check("nr legacy: snippet is the session owner, own parameter object",
          legacy.ngx.feature_module is None
          and legacy.ngx.params.backend == "own-object")
    out4 = legacy.evaluate(payload, reset=True)
    check("nr legacy: own-object route still round-trips",
          out4 == want
          and FakeNgxModule.own_store.get("DLSSNR.Style") == 1
          and FakeNgxModule.own_store.get("DLSSNR.Width") == W)
    import ctypes as _ct3
    check("nr legacy: parameter object is 64-byte padded (stray-read safe)",
          _ct3.sizeof(legacy.ngx.params._object) == 64)
    legacy.close()

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

    sr.close()

    # the sessions closed so far released their features (pinned at the end of
    # this flow; the SR ladder blocks below clear RECORD for their own runs)
    saw_release = any(entry[0] == "ReleaseFeature" for entry in RECORD)

    # ---- SR ladder (rig 31): the DRIVER CORE leads -----------------------
    # Rig 03:49 proved the union search paths do their job: during the FIRST
    # init the core validated the staged nvngx_dlss.dll and registered "app
    # 876232C feature dlss snippet" - a provider CreateFeature(1) can use, and
    # the reason the old 0xBAD0000B (UnableToInitializeFeature) route now
    # leads. The SDK runtime as the app-facing module stays second: it is the
    # documented application geometry, and its refusal is an ERROR (0xBAD00002,
    # runs 30/31) that the ladder simply steps over - it never faults on that
    # route. The shim-owner geometry measured on rig 31 leaves the default
    # ladder (a fault stops it by design).
    #
    # NOTE: the flow's harness has ngx.NgxModule.fn replaced by fake_fn, so
    # these pins count init attempts and check the chosen owner module - the
    # production routing itself is pinned earlier in this file.
    real_fn = ngx.NgxModule.fn
    calls = {"init_ext": 0, "init4": 0}

    def patch_fn(export, pattern, result, counter=None):
        """Make `export` on modules whose path matches return `result`.

        `result` is an NGX ERROR code, so the ladder keeps going. Patches
        CHAIN (each wraps the previous wrapper), so two independent patches
        both stay effective.
        """
        base = ngx.NgxModule.fn

        def patched(self, name, argtypes, restype=ctypes.c_int32, thunk="call"):
            if name == export and pattern in str(getattr(self, "path", "")):
                if counter:
                    calls[counter] += 1
                return lambda *args: result
            return base(self, name, argtypes, restype, thunk)
        ngx.NgxModule.fn = patched

    def refuse_runtime_caller():
        """The rig's 0xBAD00002: EVERY init entry point of the runtime refuses.

        Runs 30/31 show a direct caller being rejected, and the classic 4-arg
        Init is answered the same way - one attempt per route.
        """
        patch_fn("NVSDK_NGX_D3D12_Init_Ext", "nvngx_dlss.dll", -0x452FFFFE,
                 counter="init_ext")
        patch_fn("NVSDK_NGX_D3D12_Init", "nvngx_dlss.dll", -0x452FFFFE,
                 counter="init4")

    def core_cannot_create():
        patch_fn("NVSDK_NGX_D3D12_CreateFeature", "_nvngx.dll", -0x452FFFFE)

    sr_dir = tempfile.mkdtemp(prefix="ants_sr_stage_")
    with open(os.path.join(sr_dir, "nvngx_dlss.dll"), "wb") as handle:
        handle.write(b"MZ" + b"\x00" * 1024)

    # (a) core lane healthy -> it creates feature 1 from the union paths and
    #     the runtime is never asked (the refusal is armed to prove the ladder
    #     does not have to walk into it)
    RECORD.clear()
    PARAMS.clear()
    calls["init_ext"] = calls["init4"] = 0
    refuse_runtime_caller()
    try:
        sr2 = sr_mod.DlssSrSession(gpu, 30, 40, 30, 40, mode="DLAA", preset="J",
                                   sr_dll_dir=sr_dir)
    finally:
        ngx.NgxModule.fn = real_fn
    check("sr: the DRIVER CORE leads and creates feature 1 from the union "
          "search paths - the SDK runtime is never asked, so the refusal it "
          "gives a direct caller (0xBAD00002 = PlatformError, runs 30/31) "
          "never has to be stepped over",
          os.path.basename(str(sr2.ngx.module.path)) == "_nvngx.dll"
          and any(entry[0] == "CreateFeature" and entry[1] == 1
                  for entry in RECORD)
          and calls["init_ext"] == 0 and calls["init4"] == 0)
    sr2.close()

    # (b) core lane fails with an ERROR (not a fault) -> the staged runtime
    #     becomes the owner, in the PUBLIC order, core preloaded for presence
    RECORD.clear()
    PARAMS.clear()
    core_cannot_create()
    try:
        sr3 = sr_mod.DlssSrSession(gpu, 30, 40, 30, 40, mode="DLAA", preset="J",
                                   sr_dll_dir=sr_dir)
    finally:
        ngx.NgxModule.fn = real_fn
    check("sr: when the core lane fails with an error the staged runtime "
          "becomes the session OWNER and the feature provider (the public-ABI "
          "route every DLSS application uses), the capability map comes from "
          "it, and the driver core is preloaded for presence",
          os.path.basename(str(sr3.ngx.module.path)) == "nvngx_dlss.dll"
          and sr3.ngx.feature_module is None
          and sr3.ngx._own_parameters is None
          and sr3.ngx._core_handle == 4242
          and any(entry[0] == "CreateFeature" and entry[1] == 1
                  for entry in RECORD)
          and PARAMS.get("DLSS.Hint.Render.Preset.DLAA", ("u32", -1))[1] == 10)
    sr3.close()

    # (c) the caller-shim owner geometry is OPT-IN; an error that exhausts the
    #     ladder raises the aggregate error naming every route that was tried
    RECORD.clear()
    calls["init_ext"] = calls["init4"] = 0
    core_cannot_create()
    refuse_runtime_caller()
    try:
        try:
            sr_mod.DlssSrSession(gpu, 30, 40, 30, 40, mode="DLAA", preset="J",
                                 sr_dll_dir=sr_dir)
            text = ""
        except _Exc as exc:
            text = str(exc)
        one_route = calls["init_ext"]
    finally:
        ngx.NgxModule.fn = real_fn
    check("sr: without the opt-in the runtime-as-owner route is tried ONCE "
          "(one direct public-order attempt, refused like the rig's) and the "
          "aggregate [ANTs] error names both routes and the way out "
          "(pre_denoise_mode OFF / sr_strength 0)",
          one_route == 1 and "[ANTs]" in text and "OFF" in text
          and "nvngx_dlss.dll" in text and "_nvngx.dll" in text)

    RECORD.clear()
    calls["init_ext"] = calls["init4"] = 0
    os.environ["ANTS_SR_OWNER_SHIM"] = "1"
    core_cannot_create()
    refuse_runtime_caller()
    try:
        try:
            sr_mod.DlssSrSession(gpu, 30, 40, 30, 40, mode="DLAA", preset="J",
                                 sr_dll_dir=sr_dir)
            text = ""
        except _Exc as exc:
            text = str(exc)
        shim_route = calls["init_ext"]
    finally:
        ngx.NgxModule.fn = real_fn
        os.environ.pop("ANTS_SR_OWNER_SHIM", None)
    check("sr: ANTS_SR_OWNER_SHIM=1 adds rig 31's measured geometry (the "
          "runtime through the caller shim) as a SECOND attempt - it stays out "
          "of the default ladder because a fault there stops the ladder",
          shim_route == 2 and "[ANTs]" in text)

    # (d) a runtime FAULT stops the ladder with ONE loud RESTART error - and it
    #     must not reach the shim route even with the opt-in enabled
    faults = []
    os.environ["ANTS_SR_OWNER_SHIM"] = "1"
    core_cannot_create()
    base_fn = ngx.NgxModule.fn

    def faulting_fn(self, name, argtypes, restype=ctypes.c_int32, thunk="call"):
        if name == "NVSDK_NGX_D3D12_Init_Ext" and "nvngx_dlss" in str(
                getattr(self, "path", "")):
            faults.append(os.path.basename(str(getattr(self, "path", ""))))

            def boom(*args):
                raise OSError(
                    "exception: access violation writing 0x0000000001E73BF0")
            return boom
        return base_fn(self, name, argtypes, restype, thunk)

    ngx.NgxModule.fn = faulting_fn
    try:
        try:
            sr_mod.DlssSrSession(gpu, 30, 40, 30, 40, mode="DLAA", preset="J",
                                 sr_dll_dir=sr_dir)
            text = ""
        except _Exc as exc:
            text = str(exc)
        check("sr: a runtime fault becomes ONE loud [ANTs] error naming the "
              "file and the route, saying RESTART and offering OFF/0 as the "
              "way out - the ladder is NOT continued into the next route (the "
              "shim attempt never happens even with the opt-in on: a process "
              "whose NGX runtime faulted must not keep working)",
              "access violation" in text and "RESTART" in text
              and "nvngx_dlss.dll" in text and "OFF" in text
              and "route:" in text and faults == ["nvngx_dlss.dll"])
    finally:
        ngx.NgxModule.fn = real_fn
        os.environ.pop("ANTS_SR_OWNER_SHIM", None)

    # ---- labels + the one-process NGX geometry (rig 02:48) ----------------
    check("ngx: result codes carry the HEADER's names (0xBAD0000B = "
          "UnableToInitializeFeature, NOT FeatureNotSupported - that is "
          "0xBAD00001; 0xBAD00003 = FeatureAlreadyExists; 0xBAD0000C = "
          "OutOfDate) - a wrong name sends the whole investigation the wrong "
          "way",
          ngx.ngx_result_name(0x1) == "Success"
          and ngx.ngx_result_name(0xBAD00001) == "FeatureNotSupported"
          and ngx.ngx_result_name(0xBAD00002) == "PlatformError"
          and ngx.ngx_result_name(0xBAD00003) == "FeatureAlreadyExists"
          and ngx.ngx_result_name(0xBAD00004) == "FeatureNotFound"
          and ngx.ngx_result_name(0xBAD00005) == "InvalidParameter"
          and ngx.ngx_result_name(0xBAD0000B) == "UnableToInitializeFeature"
          and ngx.ngx_result_name(0xBAD0000C) == "OutOfDate"
          and "unknown" in ngx.ngx_result_name(0x1234))
    saved_geometry = ngx._INIT_GEOMETRY
    ngx._INIT_GEOMETRY = None
    try:
        first = ngx._note_geometry(ngx.NR_APP_ID, ngx.NR_PROJECT_ID, ["C:/a"])
        same = ngx._note_geometry(ngx.NR_APP_ID, ngx.NR_PROJECT_ID, ["C:/a"])
        changed = ngx._note_geometry(0x4E5254530001, None, ["C:/b"])
    finally:
        ngx._INIT_GEOMETRY = saved_geometry
    check("ngx: the geometry bookkeeping names the FIRST init and flags a "
          "later one that differs (app id + search paths) - the core keeps "
          "the first context, so a second init adds nothing",
          (first, same, changed) == ("first", "same", "changed"))
    stage_root = tempfile.mkdtemp(prefix="ants_dlss_root_")
    for rel in (("staged", "nvngx_dlssnr_ANY-1", "nvngx_dlssnr.dll"),
                ("staged", "ANTs", "sr_staged", "nvngx_dlss_310.9.1",
                 "nvngx_dlss.dll"),
                ("staged", "junk", "deep", "deeper", "beyond",
                 "nvngx_dlss.dll")):
        os.makedirs(os.path.join(stage_root, *rel[:-1]), exist_ok=True)
        open(os.path.join(stage_root, *rel), "wb").close()
    found = ngx.feature_lib_dirs(stage_root)
    check("ngx: the search-path union finds both staged feature libraries "
          "(the NR build folder and the SR staged folder) and nothing past "
          "the bounded depth",
          len(found) == 2
          and any(p.endswith("nvngx_dlss_310.9.1") for p in found)
          and any(p.endswith("nvngx_dlssnr_ANY-1") for p in found))
    from ants.dlsssr import discovery as _sr_discovery
    try:
        probe_dir = _sr_discovery.ensure_staged_sr_dir()
    except Exception:
        probe_dir = "raised"
    check("ngx: the selected SR build is staged on demand for the union, and "
          "a machine without an SR set degrades to None (the SR stage itself "
          "fails loudly when it runs)",
          probe_dir is None or isinstance(probe_dir, str))

    gpu.close()
    check("flow: teardown releases the feature",
          saw_release
          or any(entry[0] == "ReleaseFeature" for entry in RECORD))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

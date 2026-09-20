"""Minimal D3D12/DXGI bindings for the NGX host (only what NGX needs).

One adapter, one device, one direct queue + allocator + command list + fence
("record, submit, wait"), committed textures/buffers, staging upload and
readback. Vtable slots are 0-based including IUnknown and match the public
d3d12.h / dxgi.h declarations.
"""

import ctypes
import os

from .com import ComObject, guid, hresult_check
from .errors import DlssSrError
from . import win32

# --- IIDs (public headers) ---
IID_IDXGIFactory1 = guid("{770aae78-f26f-4dba-a829-253c83d1b387}")
IID_IDXGIAdapter1 = guid("{29038f61-3839-4626-91fd-086879011a05}")
IID_ID3D12Device = guid("{189819f1-1db6-4b57-be54-1821339b85f7}")
IID_ID3D12CommandQueue = guid("{0ec870a6-5d7e-4c22-8cfc-5baae07616ed}")
IID_ID3D12CommandAllocator = guid("{6102dee4-af59-4b09-b999-b44d73f09b24}")
IID_ID3D12GraphicsCommandList = guid("{5b160d0f-ac1b-4185-8ba8-b3ae42a5a455}")
IID_ID3D12Resource = guid("{696442be-a72e-4059-bc79-5b5c98040fad}")
IID_ID3D12Fence = guid("{0a753dcf-c4d8-4b91-adf6-be5a60d95a76}")

# --- enums / constants (d3d12.h) ---
D3D_FEATURE_LEVEL_11_0 = 0xB000
D3D12_COMMAND_LIST_TYPE_DIRECT = 0
D3D12_HEAP_TYPE_UNKNOWN = 0
D3D12_HEAP_TYPE_DEFAULT = 1  # NOT 0 - 0 is UNKNOWN (rig run 11: E_INVALIDARG)
D3D12_HEAP_TYPE_UPLOAD = 2
D3D12_HEAP_TYPE_READBACK = 3
D3D12_HEAP_FLAG_NONE = 0
D3D12_RESOURCE_FLAG_NONE = 0
D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS = 0x8
D3D12_RESOURCE_STATE_COMMON = 0
D3D12_RESOURCE_STATE_UNORDERED_ACCESS = 0x8
D3D12_RESOURCE_STATE_COPY_DEST = 0x400
D3D12_RESOURCE_STATE_COPY_SOURCE = 0x800
D3D12_RESOURCE_STATE_GENERIC_READ = 0xAC3
D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE = 0x40
D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE = 0x80
D3D12_RESOURCE_DIMENSION_BUFFER = 1
D3D12_RESOURCE_DIMENSION_TEXTURE2D = 3  # (2 = TEXTURE1D - rig-proven trap)
D3D12_TEXTURE_LAYOUT_UNKNOWN = 0
D3D12_TEXTURE_LAYOUT_ROW_MAJOR = 1
D3D12_TEXTURE_DATA_PITCH_ALIGNMENT = 256
D3D12_TEXTURE_DATA_PLACEMENT_ALIGNMENT = 512

DXGI_FORMAT_UNKNOWN = 0
DXGI_FORMAT_R8G8B8A8_UNORM = 28
DXGI_FORMAT_R16G16B16A16_FLOAT = 10
DXGI_FORMAT_R16G16_FLOAT = 34
DXGI_FORMAT_R32_FLOAT = 41

BPP = {DXGI_FORMAT_R16G16B16A16_FLOAT: 8, DXGI_FORMAT_R8G8B8A8_UNORM: 4,
       DXGI_FORMAT_R16G16_FLOAT: 4, DXGI_FORMAT_R32_FLOAT: 4}

# ID3D12Device vtable slots
_DEVICE_CREATE_COMMAND_QUEUE = 8
_DEVICE_CREATE_COMMAND_ALLOCATOR = 9
_DEVICE_CREATE_COMMAND_LIST = 12
_DEVICE_CREATE_COMMITTED_RESOURCE = 27
_DEVICE_CREATE_FENCE = 36
_DEVICE_DEVICE_REMOVED_REASON = 37
# ID3D12Resource
_RESOURCE_MAP = 8
_RESOURCE_UNMAP = 9
# ID3D12Fence
_FENCE_GET_COMPLETED = 8
_FENCE_SET_EVENT = 9
# ID3D12CommandAllocator
_ALLOCATOR_RESET = 8
# ID3D12CommandQueue
_QUEUE_EXECUTE_COMMAND_LISTS = 10
_QUEUE_SIGNAL = 14
# ID3D12GraphicsCommandList
_LIST_CLOSE = 9
_LIST_RESET = 10
_LIST_COPY_TEXTURE_REGION = 16  # CopyTextureRegion (works across types)
_LIST_RESOURCE_BARRIER = 26


# --- D3D12 device status ---------------------------------------------------
# The rig runs proved that a "Close failed" message alone is ambiguous: the
# list can be poisoned while the device is perfectly alive, OR the device can
# have been REMOVED underneath us (a GPU timeout / TDR, a driver reset, or a
# driver-internal error) - in which case every later call is meaningless and
# creating new objects just cascades into more confusing HRESULTs
# (rig 2026-09-20 20:39: Close 0x80070057 twice, then CreateCommandAllocator
# 0x887A0005 = DXGI_ERROR_DEVICE_REMOVED).
DXGI_ERROR_INVALID_CALL = 0x887A0001
DXGI_ERROR_DEVICE_REMOVED = 0x887A0005
DXGI_ERROR_DEVICE_HUNG = 0x887A0006
DXGI_ERROR_DEVICE_RESET = 0x887A0007
DXGI_ERROR_DRIVER_INTERNAL_ERROR = 0x887A0020
DXGI_ERROR_UNSUPPORTED = 0x887A0004
DXGI_ERROR_DEVICE_REMOVED_NAMES = {
    DXGI_ERROR_INVALID_CALL: (
        "INVALID_CALL - the call or the object state was invalid. A healthy "
        "device does not report this through GetDeviceRemovedReason, so it "
        "means the device is already gone and the runtime is refusing calls "
        "(a fresh D3D12CreateDevice in this process answers the same code)"),
    DXGI_ERROR_DEVICE_REMOVED: (
        "DEVICE_REMOVED (the device is gone; everything recorded is void)"),
    DXGI_ERROR_DEVICE_HUNG: (
        "DEVICE_HUNG - the GPU did not finish within the driver's timeout "
        "(TDR, 2 s by default). A single frame/evaluate that takes longer "
        "than that resets the device"),
    DXGI_ERROR_DEVICE_RESET: (
        "DEVICE_RESET - the driver reset the device (often the same GPU "
        "timeout, seen from the driver's side)"),
    DXGI_ERROR_DRIVER_INTERNAL_ERROR: (
        "DRIVER_INTERNAL_ERROR - the driver itself failed"),
    DXGI_ERROR_UNSUPPORTED: "UNSUPPORTED - the device does not support the call",
}

HRESULT_NAMES = {
    0x80070057: "E_INVALIDARG",
    0x80004005: "E_FAIL",
    0x8007000E: "E_OUTOFMEMORY",
    0x80004002: "E_NOINTERFACE",
    DXGI_ERROR_INVALID_CALL: "DXGI_ERROR_INVALID_CALL",
    DXGI_ERROR_DEVICE_REMOVED: "DXGI_ERROR_DEVICE_REMOVED",
    DXGI_ERROR_DEVICE_HUNG: "DXGI_ERROR_DEVICE_HUNG",
    DXGI_ERROR_DEVICE_RESET: "DXGI_ERROR_DEVICE_RESET",
    DXGI_ERROR_DRIVER_INTERNAL_ERROR: "DXGI_ERROR_DRIVER_INTERNAL_ERROR",
    DXGI_ERROR_UNSUPPORTED: "DXGI_ERROR_UNSUPPORTED",
}


def describe_device_reason(hr):
    """Plain-English name for a GetDeviceRemovedReason HRESULT."""
    code = hr & 0xFFFFFFFF
    if code == 0:
        return "0x00000000 (device present and healthy)"
    return f"0x{code:08X} - " + DXGI_ERROR_DEVICE_REMOVED_NAMES.get(
        code, "unknown removal reason")


def describe_hresult(hr):
    """``0x887A0001 (DXGI_ERROR_INVALID_CALL)`` - name the code, always.

    The rig logs used to say "HRESULT 0x887A0001" and then, in the same
    sentence, call it a *removal* - 0x887A0001 is DXGI_ERROR_INVALID_CALL,
    which is what the runtime answers when the *object state* is wrong or when
    the whole process is already finished with D3D12 (a fresh
    D3D12CreateDevice in a process whose device was removed gets exactly this).
    """
    code = hr & 0xFFFFFFFF
    name = HRESULT_NAMES.get(code)
    return f"0x{code:08X} ({name})" if name else f"0x{code:08X}"


def input_state():
    """The D3D12 state NGX expects for its INPUT textures (Color/MVec/Depth).

    Proven-host contract (and the shipped open-source ComfyUI host): the
    colour input lives in a SHADER-RESOURCE state and only the OUTPUT sits in
    UNORDERED_ACCESS. This pack used to hand NGX a colour texture in the
    UNORDERED_ACCESS state - a state the runtime does not record its reads
    for, which is exactly the kind of thing D3D12 answers with E_INVALIDARG at
    ``Close()`` (rig 18:25 / 20:39 / 21:52) and the driver can turn into a
    GPU fault (device removal, 20:39 / 21:52).

    ``ANTS_NR_INPUT_STATE=uav`` restores the old behaviour for an A/B run.
    """
    legacy = os.environ.get("ANTS_NR_INPUT_STATE", "").strip().lower()
    if legacy in ("uav", "unordered_access", "8"):
        return D3D12_RESOURCE_STATE_UNORDERED_ACCESS
    return D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE


def state_name(state):
    """Short name for a resource state (log lines)."""
    return {D3D12_RESOURCE_STATE_COMMON: "COMMON",
            D3D12_RESOURCE_STATE_UNORDERED_ACCESS: "UNORDERED_ACCESS",
            D3D12_RESOURCE_STATE_COPY_DEST: "COPY_DEST",
            D3D12_RESOURCE_STATE_COPY_SOURCE: "COPY_SOURCE",
            D3D12_RESOURCE_STATE_GENERIC_READ: "GENERIC_READ",
            D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE:
                "NON_PIXEL_SHADER_RESOURCE",
            D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE:
                "PIXEL_SHADER_RESOURCE"}.get(state, f"state {state}")


# --- process-wide "this process lost its device" memory --------------------
# A removed D3D12 device poisons the whole PROCESS, not just our objects:
#   * a fresh D3D12CreateDevice on the next frame answered 0x887A0001
#     (DXGI_ERROR_INVALID_CALL) - the driver refuses new devices, and the
#     message read as a mystery instead of "restart ComfyUI";
#   * releasing a poisoned command list CRASHED inside the NVIDIA UMD - an
#     access violation at nvwgf2umx.dll+0x6D3471, right after the removal
#     (rig 21:52, first frame of a native run).
# Both are remembered here so the pack can say what happened and stop
# touching the corpse (see GpuContext.close).
_WEDGED = {"done": False, "hr": 0, "text": ""}


def note_wedged(hr, text=""):
    """Remember (once) that this process has lost a D3D12 device."""
    if not _WEDGED["done"]:
        _WEDGED.update(done=True, hr=(hr or 0) & 0xFFFFFFFF, text=text or "")


def wedged():
    """(True, hr, text) once a D3D12 device was removed in this process."""
    return _WEDGED["done"], _WEDGED["hr"], _WEDGED["text"]


def reset_wedged():
    """Forget the removal - tests only; the real state cannot be undone."""
    _WEDGED.update(done=False, hr=0, text="")


def _create_device_error(hr):
    """The loud error for a failed D3D12CreateDevice, with the wedge named."""
    text = f"[ANTs] D3D12CreateDevice failed: HRESULT {describe_hresult(hr)}."
    done, w_hr, w_text = wedged()
    if done:
        text += (" This process ALREADY LOST a D3D12 device"
                 + (f" ({w_text})" if w_text else "")
                 + " and after that the driver refuses to hand out new ones -"
                 " RESTART ComfyUI; no node can bring the GPU back in this"
                 " process.")
    elif (hr & 0xFFFFFFFF) == DXGI_ERROR_INVALID_CALL:
        text += (" DXGI_ERROR_INVALID_CALL is not a removal reason - the call"
                 " or the object state was invalid (a stale adapter pointer, or"
                 " a driver that is still unwinding an earlier device loss).")
    return text


def _align(value, to):
    return -(-value // to) * to


def linear_layout(width, height, fmt):
    """Layout of a 2D texture copied linearly into a buffer."""
    row_bytes = width * BPP[fmt]
    row_pitch = _align(row_bytes, D3D12_TEXTURE_DATA_PITCH_ALIGNMENT)
    total = _align(row_pitch * height, D3D12_TEXTURE_DATA_PLACEMENT_ALIGNMENT)
    return row_bytes, row_pitch, total


def _heap_properties(heap_type):
    # D3D12_HEAP_PROPERTIES: Type, CPUPageProperty, MemoryPoolPreference,
    # CreationNodeMask, VisibleNodeMask (20 bytes)
    heap = struct_pack("<IIIII", heap_type, 0, 0, 1, 1)
    assert len(heap) == 20
    return heap


def struct_pack(fmt, *values):
    return ctypes.create_string_buffer(ctypes.pack(fmt, *values), ctypes.calcsize(fmt)) \
        if hasattr(ctypes, "pack") else _pack(fmt, *values)


def _pack(fmt, *values):
    import struct as _s
    return ctypes.create_string_buffer(_s.pack(fmt, *values), _s.calcsize(fmt))


_RESOURCE_DESC_FMT = "<I4xQQIHHIIIIQ"  # 56 bytes (d3d12.h layout, PE32+)


def _resource_desc_texture(width, height, fmt, flags):
    # D3D12_RESOURCE_DESC: Dimension u32 (+4 pad), Alignment u64, Width u64,
    # Height u32, DepthOrArraySize u16, MipLevels u16, Format u32,
    # SampleDesc {Count u32, Quality u32}, Layout u32, Flags u64
    desc = _pack(_RESOURCE_DESC_FMT, D3D12_RESOURCE_DIMENSION_TEXTURE2D, 0,
                 width, height, 1, 1, fmt, 1, 0, D3D12_TEXTURE_LAYOUT_UNKNOWN, flags)
    assert len(desc) == 56
    return desc


def _resource_desc_buffer(size):
    desc = _pack(_RESOURCE_DESC_FMT, D3D12_RESOURCE_DIMENSION_BUFFER, 0, size, 1,
                 1, 1, DXGI_FORMAT_UNKNOWN, 1, 0, D3D12_TEXTURE_LAYOUT_ROW_MAJOR, 0)
    assert len(desc) == 56
    return desc


def _addr(p):
    """int address from an int or c_void_p (never int(c_void_p) - that parses
    the pointed-to memory as a string literal)."""
    return int(p.value) if isinstance(p, ctypes.c_void_p) else int(p)


def _copy_location_texture(resource_ptr, subresource=0):
    # D3D12_TEXTURE_COPY_LOCATION { pResource, Type=SUBRESOURCE_INDEX,
    # { SubresourceIndex } } = 48 bytes
    buf = _pack("<QI4xQ24x", _addr(resource_ptr), 0, subresource)
    assert len(buf) == 48
    return buf


def _copy_location_footprint(resource_ptr, fmt, width, height, row_pitch, offset=0):
    # D3D12_TEXTURE_COPY_LOCATION { pResource, Type=PLACED_FOOTPRINT,
    # { Offset, Footprint { Format, Width, Height, Depth, RowPitch } } }
    buf = _pack("<QI4xQIIIIQ", _addr(resource_ptr), 1, offset, fmt,
                int(width), int(height), 1, int(row_pitch))
    assert len(buf) == 48
    return buf


def _transition_barrier(resource_ptr, before, after):
    # D3D12_RESOURCE_BARRIER: Type u32, Flags u32, [pad], Transition {
    # pResource (ptr, aligned 8), StateBefore u32, StateAfter u32 }
    buf = ctypes.create_string_buffer(32)
    ctypes.memset(buf, 0, 32)
    struct_pack_into = lambda o, v: ctypes.memmove(
        ctypes.addressof(buf) + o, _pack("<I", v), 4)
    struct_pack_into(0, 0)  # D3D12_RESOURCE_BARRIER_TYPE_TRANSITION
    struct_pack_into(4, 0)  # D3D12_RESOURCE_BARRIER_FLAG_NONE
    ctypes.cast(ctypes.addressof(buf) + 8, ctypes.POINTER(ctypes.c_void_p))[0] = resource_ptr
    struct_pack_into(16, 0xFFFFFFFF)  # ALL_SUBRESOURCES
    struct_pack_into(20, before)
    struct_pack_into(24, after)
    return buf


UPLOAD_READBACK_HEAPS = (D3D12_HEAP_TYPE_UPLOAD, D3D12_HEAP_TYPE_READBACK)


class D3D12Resource(ComObject):
    def __init__(self, ptr, label, width, height, fmt, byte_size, state,
                 heap_type=D3D12_HEAP_TYPE_DEFAULT):
        super().__init__(ptr, label)
        self.width = width
        self.height = height
        self.format = fmt
        self.byte_size = byte_size
        self.state = state
        # Staging resources (UPLOAD/READBACK heaps) live in an IMPLICIT,
        # permanent state and must never be listed in a barrier - see
        # GpuContext.transition.
        self.heap_type = heap_type

    def map(self):
        range_buf = _pack("<QQ", 0, 0)  # D3D12_RANGE {0, 0} = whole resource
        out = ctypes.c_void_p()
        self.call_hr(_RESOURCE_MAP, [_CVOID_U32(), _CVOID_P(), _CVOID_P()],
                     ctypes.c_uint32(0), range_buf, ctypes.byref(out), what="Map")
        return out.value

    def unmap(self):
        range_buf = _pack("<QQ", 0, 0)
        self.call(_RESOURCE_UNMAP, [_CVOID_U32(), _CVOID_P()], None,
                  ctypes.c_uint32(0), range_buf)


def _CVOID_U32():
    return ctypes.c_uint32


def _CVOID_U64():
    return ctypes.c_uint64


def _CVOID_P():
    return ctypes.c_void_p


class D3D12Device(ComObject):
    def __init__(self, ptr):
        super().__init__(ptr, "ID3D12Device")
        self._texture_recipe = None  # winning (flags, state) combo

    @staticmethod
    def create(adapter_ptr=None, feature_level=D3D_FEATURE_LEVEL_11_0):
        create = win32.d3d12_create_device_symbol()
        create.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                           ctypes.POINTER(ctypes.c_char * 16),
                           ctypes.POINTER(ctypes.c_void_p)]
        create.restype = ctypes.c_int32
        out = ctypes.c_void_p()
        hr = create(adapter_ptr, feature_level, IID_ID3D12Device, ctypes.byref(out))
        if hr < 0:
            raise DlssSrError(_create_device_error(hr))
        return D3D12Device(out.value)

    def _create(self, slot, what, argtypes, *args):
        # callers pass the IID as the last positional arg; we only append the
        # out-pointer (an extra forwarded None here would land in the driver's
        # out-parameter register - a guaranteed access violation)
        out = ctypes.c_void_p()
        self.call_hr(slot, list(argtypes) + [_CVOID_P()], *args,
                     ctypes.byref(out), what=what)
        return out.value

    def create_command_queue(self):
        desc = _pack("<IIII", D3D12_COMMAND_LIST_TYPE_DIRECT, 0, 0, 0)
        return ComObject(
            self._create(_DEVICE_CREATE_COMMAND_QUEUE, "CreateCommandQueue",
                         [_CVOID_P()], desc, IID_ID3D12CommandQueue),
            "ID3D12CommandQueue")

    def create_command_allocator(self):
        return ComObject(
            self._create(_DEVICE_CREATE_COMMAND_ALLOCATOR, "CreateCommandAllocator",
                         [_CVOID_U32()], ctypes.c_uint32(D3D12_COMMAND_LIST_TYPE_DIRECT),
                         IID_ID3D12CommandAllocator),
            "ID3D12CommandAllocator")

    def create_command_list(self, allocator, label="ID3D12GraphicsCommandList"):
        return ComObject(
            self._create(_DEVICE_CREATE_COMMAND_LIST, "CreateCommandList",
                         [_CVOID_U32(), _CVOID_U32(), _CVOID_P(), _CVOID_P()],
                         ctypes.c_uint32(0), ctypes.c_uint32(D3D12_COMMAND_LIST_TYPE_DIRECT),
                         allocator.ptr, None, IID_ID3D12GraphicsCommandList),
            label)

    def create_fence(self, initial=0):
        return ComObject(
            self._create(_DEVICE_CREATE_FENCE, "CreateFence",
                         [_CVOID_U64(), _CVOID_U32()],
                         ctypes.c_uint64(initial), ctypes.c_uint32(0), IID_ID3D12Fence),
            "ID3D12Fence")

    def device_removed_reason(self):
        return self.call(_DEVICE_DEVICE_REMOVED_REASON, [], ctypes.c_int32)

    def device_status(self):
        """(removed, hr, text): is the device gone, and why.

        ``GetDeviceRemovedReason`` returns S_OK while the device is usable; any
        error means the device is removed and every recorded/queued call is
        void. Never raises - a status query must not become the failure.
        """
        try:
            hr = self.device_removed_reason()
        except Exception as exc:                      # device object itself gone
            return True, DXGI_ERROR_DEVICE_REMOVED, f"query failed ({exc})"
        removed = (hr & 0xFFFFFFFF) not in (0, 1)     # S_OK / S_FALSE
        return removed, hr & 0xFFFFFFFF, describe_device_reason(hr)

    def _committed(self, heap_type, desc, initial_state, label, heap_flags=0):
        heap = _heap_properties(heap_type)
        return self._create(_DEVICE_CREATE_COMMITTED_RESOURCE, f"CreateCommittedResource({label})",
                            [_CVOID_P(), _CVOID_U32(), _CVOID_P(), _CVOID_U32(), _CVOID_P()],
                            heap, ctypes.c_uint32(heap_flags), desc,
                            ctypes.c_uint32(initial_state), None, IID_ID3D12Resource)

    # (resource_flags, initial_state) combos for UAV-capable textures, most
    # permissive first. NVIDIA's own NGX hosts create them in the UAV state;
    # DVT's rig-validated host uses COMMON. The first combo the driver
    # accepts is cached on the device and reused for every later texture.
    # NOTE: a UAV-less fallback is deliberately NOT in this list. Rig 23:32
    # showed what it costs: the driver refused the UAV recipe for 'nr output',
    # the pack silently took (FLAG_NONE, COMMON), the NR path then recorded a
    # barrier to UNORDERED_ACCESS on that resource - an INVALID COMMAND - and
    # the next Close() answered E_INVALIDARG, which looked like a fresh bug.
    # A texture the runtime writes through a UAV must carry the flag, or the
    # creation has to FAIL LOUDLY right here.
    _UAV_RECIPES = ((D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS,
                     D3D12_RESOURCE_STATE_UNORDERED_ACCESS),
                    (D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS,
                     D3D12_RESOURCE_STATE_COMMON))
    # INPUT textures (what the runtime reads). Rig 23:08: the driver answered
    # E_INVALIDARG for (ALLOW_UNORDERED_ACCESS, NON_PIXEL_SHADER_RESOURCE), so
    # the UAV flag must not ride along with a shader-resource initial state -
    # the proven host creates its inputs as PLAIN shader resources, and so do
    # we. ``common`` is the emergency landing spot (a barrier from COMMON into
    # the shader state is always legal).
    _INPUT_RECIPES = ((D3D12_RESOURCE_FLAG_NONE,
                       D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE),
                      (D3D12_RESOURCE_FLAG_NONE,
                       D3D12_RESOURCE_STATE_COMMON))

    def _recipe_candidates(self, allow_uav, state):
        """The (flags, initial state) combos to try, best first."""
        if state is None:
            if not allow_uav:
                return ((D3D12_RESOURCE_FLAG_NONE,
                         D3D12_RESOURCE_STATE_COMMON),)
            if self._texture_recipe:
                return (self._texture_recipe,)
            return self._UAV_RECIPES
        if state == D3D12_RESOURCE_STATE_UNORDERED_ACCESS:
            # a UAV initial state REQUIRES the flag
            return ((D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS, state),
                    (D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS,
                     D3D12_RESOURCE_STATE_COMMON))
        if allow_uav:
            return ((D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS, state),
                    (D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS,
                     D3D12_RESOURCE_STATE_COMMON))
        return ((D3D12_RESOURCE_FLAG_NONE, state),) + self._INPUT_RECIPES[1:]

    def create_texture2d(self, width, height, fmt, allow_uav=True, label="texture",
                         state=None):
        """One committed 2D texture.

        ``state`` pins the INITIAL state; without it the UAV recipe cascade
        picks the first combination the driver accepts and caches it for every
        later texture. A refused combo is not silent: the pack logs which one
        the driver took instead, because "the texture is not what the runtime
        expects" is exactly how E_INVALIDARG at Close() and device removals
        start (rig 18:25 / 20:39 / 21:52 / 23:08).
        """
        candidates = self._recipe_candidates(allow_uav, state)
        last = None
        accepted = None
        for index, (flags, initial) in enumerate(candidates):
            desc = _resource_desc_texture(width, height, fmt, flags)
            try:
                ptr = self._committed(D3D12_HEAP_TYPE_DEFAULT, desc, initial,
                                      label)
            except DlssSrError as exc:
                last = exc
                continue
            accepted = (flags, initial, index)
            break
        if accepted is None:
            from ..log import dlss_logger
            _removed, _hr, status = self.device_status()
            dlss_logger.error(
                "[ANTs] D3D12 cannot create the texture '%s' (%dx%d) in any "
                "recipe this path needs: %s. Device status right now: %s. "
                "A UAV-capable texture is not optional - NGX feature 18 "
                "writes its output through one - so the creation path stops "
                "here instead of recording an illegal barrier later. If the "
                "device reports HEALTHY, the driver itself is refusing the "
                "resource descriptions (rig 23:32) - restart ComfyUI and send "
                "this line with the console.", label, int(width), int(height),
                str(last).strip(), status)
            raise DlssSrError(
                "[ANTs] Could not create the D3D12 texture '%s' (%dx%d): %s. "
                "Device status: %s." % (label, int(width), int(height),
                                        str(last).strip(), status)) from last
        flags, initial, index = accepted
        if state is None and self._texture_recipe is None and allow_uav:
            # only the recipe the caller WANTED is cached; a fallback would
            # silently degrade every later texture on this device
            self._texture_recipe = (flags, initial)
        if index:
            from ..log import dlss_logger
            wanted = candidates[0]
            _removed, _hr, status = self.device_status()
            dlss_logger.warning(
                "[ANTs] D3D12: the driver REFUSED the intended texture recipe "
                "for '%s' (flags 0x%X, initial state %s; it answered %s) and "
                "accepted (flags 0x%X, initial state %s) instead. Device "
                "status at the refusal: %s. The contract still holds (the "
                "fallback keeps the required flags), but send this line: a "
                "driver that refuses the exact recipe the proven host uses is "
                "usually a process that already lost its D3D12 device.",
                label, wanted[0], state_name(wanted[1]),
                str(last).strip()[:120], flags, state_name(initial), status)
        self.report_texture_recipe()
        return D3D12Resource(ptr, label, width, height, fmt,
                             width * height * BPP[fmt], initial)

    def create_input_texture2d(self, width, height, fmt, label="texture"):
        """An NGX INPUT texture: plain shader resource, no UAV flag.

        This is the contract the runtime validates (see :func:`input_state`
        and ``_INPUT_RECIPES``); ``ANTS_NR_INPUT_STATE=uav`` restores the old
        UAV-state behaviour for an A/B run.
        """
        want = input_state()
        return self.create_texture2d(
            width, height, fmt,
            allow_uav=(want == D3D12_RESOURCE_STATE_UNORDERED_ACCESS),
            label=label, state=want)

    def report_texture_recipe(self):
        """Name the (flags, state) combo the driver accepted - once.

        The cascade can silently fall back to a texture WITHOUT
        ALLOW_UNORDERED_ACCESS; NGX feature 18 writes its output through a UAV,
        so a degraded recipe would be a hard contract violation rather than a
        harmless fallback. One line in the rig log settles it.
        """
        recipe = self._texture_recipe
        if recipe is None or getattr(self, "_recipe_reported", False):
            return
        self._recipe_reported = True
        flags, state = recipe
        from ..log import dlss_logger
        if flags != D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS:
            dlss_logger.warning(
                "[ANTs] D3D12 textures were created WITHOUT "
                "ALLOW_UNORDERED_ACCESS (flags 0x%X, %s) - the NGX runtime "
                "writes its output through a UAV, so this recipe can only "
                "fail. Report this line: it means the driver refused every "
                "UAV-capable recipe.", flags, state_name(state))
        else:
            dlss_logger.status(
                "[ANTs] D3D12 texture recipe: flags ALLOW_UNORDERED_ACCESS, "
                "initial state %s", state_name(state))

    def create_buffer(self, size, heap_type, label="buffer"):
        state = {"upload": D3D12_RESOURCE_STATE_GENERIC_READ,
                 "readback": D3D12_RESOURCE_STATE_COPY_DEST}.get(
                     {D3D12_HEAP_TYPE_UPLOAD: "upload",
                      D3D12_HEAP_TYPE_READBACK: "readback"}.get(heap_type),
                     D3D12_RESOURCE_STATE_COMMON)
        ptr = self._committed(heap_type, _resource_desc_buffer(size), state, label)
        return D3D12Resource(ptr, label, size, 1, DXGI_FORMAT_UNKNOWN, size,
                             state, heap_type=heap_type)


VENDOR_NVIDIA = 0x10DE
VENDOR_MICROSOFT = 0x1414          # WARP / Basic Render Driver
DXGI_ADAPTER_FLAG_SOFTWARE = 0x2
# DXGI_ADAPTER_DESC1: Description[128] WCHAR, VendorId, DeviceId, SubSysId,
# Revision, three SIZE_T, LUID, Flags. The LUID is EIGHT bytes at 0x128
# (LowPart, then HighPart) - 0x12C is its HighPart, and reading the pair from
# there returned {HighPart, Flags}: a wrong reference value for the engine's
# own "cannot match CUDA ordinal by LUID" message.
_ADAPTER_DESC_SIZE = 312
_ADAPTER_NAME_CHARS = 64
_ADAPTER_VENDOR_OFFSET = 0x100
_ADAPTER_DEVICE_OFFSET = 0x104
_ADAPTER_LUID_OFFSET = 0x128
_ADAPTER_FLAGS_OFFSET = 0x130


class AdapterInfo:
    """One DXGI adapter: the pointer to create a device on, plus its identity."""

    def __init__(self, com, index, name, luid, vendor_id, device_id, flags):
        self.com = com                       # keeps the IDXGIAdapter1 alive
        self.ptr = com.ptr                   # the raw pointer CreateDevice wants
        self.index = index
        self.name = name
        self.luid = luid                     # (LowPart, HighPart) or None
        self.vendor_id = vendor_id
        self.device_id = device_id
        self.flags = flags

    @property
    def software(self):
        """WARP / Basic Render Driver - never the GPU we want."""
        return bool(self.flags & DXGI_ADAPTER_FLAG_SOFTWARE) \
            or self.vendor_id == VENDOR_MICROSOFT

    @property
    def nvidia(self):
        return self.vendor_id == VENDOR_NVIDIA

    def describe(self):
        from . import cuda_luid
        return (f"#{self.index} '{self.name}' "
                f"(vendor 0x{self.vendor_id:04x}, LUID "
                f"{cuda_luid.format_luid(self.luid)})")


def enumerate_adapters():
    """(factory, [AdapterInfo]) - call release() on the adapters not used."""
    create = win32.create_dxgi_factory1_symbol()
    create.argtypes = [ctypes.POINTER(ctypes.c_char * 16), ctypes.POINTER(ctypes.c_void_p)]
    create.restype = ctypes.c_int32
    out = ctypes.c_void_p()
    hresult_check(create(IID_IDXGIFactory1, ctypes.byref(out)), "CreateDXGIFactory1")
    factory = ComObject(out.value, "IDXGIFactory1")
    adapters = []
    index = 0
    while True:
        adapter_out = ctypes.c_void_p()
        hr = factory.call(12, [_CVOID_U32(), _CVOID_P()], ctypes.c_int32,  # EnumAdapters1
                          ctypes.c_uint32(index), ctypes.byref(adapter_out))
        if hr < 0:
            break
        adapter = ComObject(adapter_out.value, "IDXGIAdapter1")
        desc = ctypes.create_string_buffer(_ADAPTER_DESC_SIZE)  # DXGI_ADAPTER_DESC1
        adapter.call_hr(10, [_CVOID_P()], desc, what="GetDesc1")  # GetDesc1 = 10
        base = ctypes.addressof(desc)
        wide_part = ctypes.wstring_at(base, _ADAPTER_NAME_CHARS)
        luid_pair = ctypes.cast(base + _ADAPTER_LUID_OFFSET,
                                ctypes.POINTER(ctypes.c_uint32 * 2)).contents
        adapters.append(AdapterInfo(
            adapter, index, wide_part.split("\x00")[0],
            (luid_pair[0], luid_pair[1]),
            int.from_bytes(desc.raw[_ADAPTER_VENDOR_OFFSET:
                                    _ADAPTER_VENDOR_OFFSET + 4], "little"),
            int.from_bytes(desc.raw[_ADAPTER_DEVICE_OFFSET:
                                    _ADAPTER_DEVICE_OFFSET + 4], "little"),
            int.from_bytes(desc.raw[_ADAPTER_FLAGS_OFFSET:
                                    _ADAPTER_FLAGS_OFFSET + 4], "little")))
        index += 1
    return factory, adapters


def pick_adapter(adapters, ordinal=0):
    """(AdapterInfo, reason): the adapter that belongs to CUDA ``ordinal``.

    The reference host matches CUDA to DXGI **by LUID**, and so must we: the
    neuroframe engine is handed a D3D12 device and then matches it against its
    own CUDA device by LUID, so a device created on the wrong adapter fails
    with "Could not create a D3D12 device matching CUDA ordinal N by LUID".
    DXGI enumeration order (a display order) and CUDA order (a performance
    order) need not agree, and ``--cuda-device N`` / CUDA_VISIBLE_DEVICES
    renumber the CUDA side only.

    Never raises: when the LUID is unavailable the best hardware adapter is
    used and the reason says so, so the caller can log it loudly.
    """
    from . import cuda_luid
    if not adapters:
        return None, "no DXGI adapter was enumerated"
    luid, _name, why = cuda_luid.device_luid(ordinal)
    if luid is not None:
        for info in adapters:
            if info.luid == luid:
                return info, (f"CUDA ordinal {ordinal} LUID "
                              f"{cuda_luid.format_luid(luid)} -> adapter "
                              f"{info.describe()}")
        return _best_guess(adapters), (
            f"CUDA ordinal {ordinal} is LUID {cuda_luid.format_luid(luid)} but "
            f"no enumerated DXGI adapter has it - using the best guess")
    return _best_guess(adapters), (
        f"CUDA ordinal {ordinal} LUID unavailable ({why}) - using the best guess")


def _best_guess(adapters):
    """The first hardware adapter, NVIDIA preferred (never a software one)."""
    hardware = [info for info in adapters if not info.software]
    for info in hardware:
        if info.nvidia:
            return info
    if hardware:
        return hardware[0]
    return adapters[0]


def make_gpu_context(ordinal=0):
    """The one way to build a GpuContext: the device lives on the adapter that
    belongs to CUDA ``ordinal`` (see :func:`pick_adapter`)."""
    _factory, adapters = enumerate_adapters()
    info, reason = pick_adapter(adapters, ordinal)
    device = D3D12Device.create(info.ptr if info else None)
    context = GpuContext(device, adapter_index=info.index if info else 0,
                         adapter=info, ordinal=ordinal, pick_reason=reason)
    _multi_gpu_advisory(ordinal, reason)
    return context


def _multi_gpu_advisory(ordinal, pick_reason):
    """Say it once, when a second CUDA device is visible.

    ComfyUI issue #15255 (CORE-398) / PR #15451: on Windows a CUDA bug can
    poison the CUDA context as soon as the process touches more than one GPU -
    host->device copies then fail with CUDA_ERROR_OUT_OF_MEMORY and nothing in
    that process recovers. ComfyUI core enumerates every visible GPU at
    startup, so on a multi-GPU rig the process is already in the risky shape
    before any node runs; the fix upstream is to stop doing that, the
    workaround today is to restrict the visible devices.
    """
    from . import cuda_luid
    from ..log import dlss_logger
    if _MULTI_GPU_SAID["done"]:
        return
    total, _why = cuda_luid.count()
    if total is None or total <= 1:
        return
    _MULTI_GPU_SAID["done"] = True
    dlss_logger.status(
        "[ANTs] %d CUDA devices are visible to this process, and this run uses "
        "ordinal %d (%s). On Windows a CUDA driver bug can poison the CUDA "
        "context once a process touches more than one GPU - host->device copies "
        "then fail with CUDA_ERROR_OUT_OF_MEMORY and nothing in that process "
        "recovers (ComfyUI issue #15255 / PR #15451). If a run dies like that, "
        "restart ComfyUI and launch it with --cuda-device %d (single GPU) "
        "and/or --disable-pinned-memory.",
        total, ordinal, pick_reason, ordinal)


_MULTI_GPU_SAID = {"done": False}


class GpuContext:
    """One queue/allocator/list/fence; record, submit, wait (synchronous)."""

    def __init__(self, device, adapter_index=0, adapter=None, ordinal=None,
                 pick_reason=""):
        self.device = device
        if adapter is None:
            self.factory, adapters = enumerate_adapters()
            adapter = adapters[adapter_index] if adapter_index < len(adapters) \
                else None
        else:
            self.factory = None
        self.adapter_info = adapter
        self.adapter = adapter.ptr if adapter else None
        self.adapter_name = adapter.name if adapter else "?"
        self.adapter_luid = adapter.luid if adapter else None
        self.ordinal = ordinal
        self.pick_reason = pick_reason
        # Which adapter we bound matters: the neuroframe engine matches its
        # CUDA device by LUID, so this line is the reference when it reports
        # "cannot match CUDA ordinal by LUID".
        from ..log import dlss_logger
        if pick_reason:
            dlss_logger.status("[ANTs] D3D12 host adapter %s - %s",
                               adapter.describe() if adapter else "?", pick_reason)
        else:
            dlss_logger.status(
                "[ANTs] D3D12 host adapter %s (index %d, LUID %s)",
                self.adapter_name, adapter_index,
                "{%s, %s}" % self.adapter_luid if self.adapter_luid else "?")
        health = device.device_status()
        if health[0]:
            from ..log import dlss_logger as _dl
            _dl.warning(
                "[ANTs] the D3D12 device was created but is ALREADY unusable "
                "(GetDeviceRemovedReason %s) - this process has lost a D3D12 "
                "device before (an earlier removal, a driver reset, or the "
                "legacy engine's CUDA work in this same process). RESTART "
                "ComfyUI: nothing can be drawn on this device.", health[2])
        self.queue = device.create_command_queue()
        self.allocator = device.create_command_allocator()
        self.list = device.create_command_list(self.allocator)
        # our copy list keeps its own label: every log line and every kill line
        # then says WHICH list was in flight (the NGX runtime has its own)
        self.list.label = "ID3D12GraphicsCommandList (ours)"
        self.fence = device.create_fence()
        self.event = win32.create_event()
        self.fence_value = 0
        self._pending_release = []
        self._parked = []              # objects a poisoned list replaced
        self.dead = None               # (hr, text) once the device is removed
        self.recorded = []             # command descriptions, for checkpoints
        self.health = health           # (removed, hr, text) at creation time
        self.runtime_allocator = None  # the pair NGX records into (lazy)
        self.runtime_list = None
        self.runtime_recoveries = 0
        self._closed = False

    def command_list(self):
        """The command list handed to the NGX runtime (Create/EvaluateFeature).

        Deliberately a SEPARATE pair from ``self.list``: the runtime records
        into whatever it is handed, and the rig proved it does more than
        record - after the first EvaluateFeature our list answered
        E_INVALIDARG on Close (18:25 run), which took the whole node down.
        Keeping the runtime's recording in its own list means a poisoned
        runtime list can never carry our frame uploads with it, and the
        recovery (see ``_rebuild_pair``) has a much smaller blast radius.
        """
        if self.runtime_list is None:
            self.runtime_allocator = self.device.create_command_allocator()
            self.runtime_list = self.device.create_command_list(
                self.runtime_allocator)
            self.runtime_list.label = "ID3D12GraphicsCommandList (NGX runtime)"
        return self.runtime_list

    def _cmd(self, description):
        """Note the command just recorded (and verify it in checkpoint mode).

        Rig runs left one open question: WHICH command poisons the list when
        Close answers E_INVALIDARG while the device is alive. With
        ANTS_D3D12_CHECKPOINT=1 every recorded command is closed, executed and
        waited on immediately, so the first verify failure names the exact
        command and its index instead of "something in this recording".
        Diagnostic only (it serializes GPU work), never enabled by default.
        """
        self.recorded.append(description)
        if os.environ.get("ANTS_D3D12_CHECKPOINT") != "1":
            return
        index = len(self.recorded) - 1
        try:
            self._submit_pair(self.list, self.allocator, 30000, "copy")
        except DlssSrError as exc:
            from ..log import dlss_logger
            dlss_logger.error(
                "[ANTs] D3D12 checkpoint: the recording failed at command #%d "
                "(%s) - everything recorded before it was valid. %s",
                index, description, exc)
            raise

    def transition(self, resource, to):
        if getattr(resource, "heap_type", D3D12_HEAP_TYPE_DEFAULT) in \
                UPLOAD_READBACK_HEAPS:
            # D3D12 rule: UPLOAD-heap resources are permanently in
            # GENERIC_READ and READBACK-heap ones in COPY_DEST; they take no
            # barriers, and COMMON is not even a legal state for them. A
            # barrier here is an INVALID COMMAND - the runtime poisons the
            # command list and the next Close() fails with E_INVALIDARG
            # (rig: "[ANTs] ID3D12GraphicsCommandList.Close failed:
            # 0x80070057" right after the first staging upload). Skip it,
            # loudly once per process, and never record it.
            if not getattr(self, "_staging_warned", False):
                self._staging_warned = True
                from ..log import dlss_logger
                dlss_logger.warning(
                    "[ANTs] ignored a resource-state transition of an "
                    "upload/readback-heap staging resource (%s) - D3D12 "
                    "keeps those heaps in a fixed implicit state and "
                    "rejects barriers on them.", resource.label)
            return
        if resource.state == to:
            return  # already there - a redundant barrier only warns the debug layer
        barrier = _transition_barrier(resource.ptr, resource.state, to)
        self._cmd(f"Barrier({getattr(resource, 'label', '?')} -> {to})")
        self.list.call(_LIST_RESOURCE_BARRIER, [_CVOID_U32(), _CVOID_P()], None,
                       ctypes.c_uint32(1), barrier)
        resource.state = to

    def upload_texture(self, texture, pixels, final_state):
        """Record a staging-buffer copy into ``texture`` (caller submits).

        The staging buffer is released only after the next ``submit_and_wait``:
        D3D12 lets the application free a resource once the GPU is done with
        it, and a command list that references a resource the application has
        released is undefined behaviour (the debug layer calls it out as
        "resource destroyed while still referenced by a command list"). The
        obvious bug here is freeing before ExecuteCommandLists even ran - the
        copy can then read whatever the allocator handed the next resource.
        """
        row_bytes, row_pitch, total = linear_layout(texture.width, texture.height, texture.format)
        if len(pixels) != row_bytes * texture.height:
            raise DlssSrError(
                f"[ANTs] uploadTexture({texture.label}): expected "
                f"{row_bytes * texture.height} bytes, got {len(pixels)}.")
        if row_pitch == row_bytes:
            payload = bytes(pixels)
        else:
            padded = bytearray(total)
            for y in range(texture.height):
                padded[y * row_pitch:y * row_pitch + row_bytes] = \
                    pixels[y * row_bytes:(y + 1) * row_bytes]
            payload = bytes(padded)
        staging = self.device.create_buffer(total, D3D12_HEAP_TYPE_UPLOAD, "upload staging")
        addr = staging.map()
        ctypes.memmove(addr, payload, len(payload))
        staging.unmap()
        # No barrier on the staging buffer: UPLOAD-heap resources are always
        # readable by the copy engine (barriers on them are invalid and
        # poison the list - see transition()).
        self.transition(texture, D3D12_RESOURCE_STATE_COPY_DEST)
        src_loc = _copy_location_footprint(staging.ptr, texture.format,
                                           texture.width, texture.height, row_pitch)
        dst_loc = _copy_location_texture(texture.ptr)
        self._cmd(f"CopyTextureRegion(upload -> {texture.label} "
                  f"{texture.width}x{texture.height})")
        self.list.call(_LIST_COPY_TEXTURE_REGION,
                       [_CVOID_P(), _CVOID_U32(), _CVOID_U32(), _CVOID_U32(),
                        _CVOID_P(), _CVOID_P()],
                       None, dst_loc, 0, 0, 0, src_loc, None)
        self.transition(texture, final_state)
        # Keep the staging resource alive until the GPU has finished with it.
        self._pending_release.append(staging)

    def readback_texture(self, texture, state):
        _, row_pitch, total = linear_layout(texture.width, texture.height, texture.format)
        self.transition(texture, D3D12_RESOURCE_STATE_COPY_SOURCE)
        readback = self.device.create_buffer(total, D3D12_HEAP_TYPE_READBACK, "readback staging")
        dst_loc = _copy_location_footprint(readback.ptr, texture.format,
                                           texture.width, texture.height, row_pitch)
        src_loc = _copy_location_texture(texture.ptr)
        self._cmd(f"CopyTextureRegion({texture.label} -> readback "
                  f"{texture.width}x{texture.height})")
        self.list.call(_LIST_COPY_TEXTURE_REGION,
                       [_CVOID_P(), _CVOID_U32(), _CVOID_U32(), _CVOID_U32(),
                        _CVOID_P(), _CVOID_P()],
                       None, dst_loc, 0, 0, 0, src_loc, None)
        self.transition(texture, state)
        self.submit_and_wait()
        addr = readback.map()
        src = ctypes.string_at(addr, total)
        readback.unmap()
        readback.release()
        row_bytes = texture.width * BPP[texture.format]
        out = bytearray(row_bytes * texture.height)
        for y in range(texture.height):
            out[y * row_bytes:(y + 1) * row_bytes] = \
                src[y * row_pitch:y * row_pitch + row_bytes]
        return bytes(out)

    def submit_and_wait(self, timeout_ms=30000):
        """Close, execute and wait on OUR list (copies, barriers, readbacks)."""
        self._submit_pair(self.list, self.allocator, timeout_ms, "copy")

    def runtime_submit_and_wait(self, timeout_ms=30000):
        """Close, execute and wait on the RUNTIME's list.

        The runtime recorded its work into this list during the feature call;
        until it is closed, executed and waited on, the output means nothing
        (the proven hosts do exactly this after every feature call).
        """
        self._submit_pair(self.command_list(), self.runtime_allocator,
                          timeout_ms, "runtime")

    def device_status(self):
        """(removed, hr, text) for the device this context runs on."""
        if self.dead is not None:
            return True, self.dead[0], self.dead[1]
        try:
            return self.device.device_status()
        except Exception as exc:
            return False, 0, f"status unavailable ({exc})"

    def mark_device_removed(self, hr, text):
        """Remember that the device is gone (idempotent, process-wide)."""
        if self.dead is None:
            self.dead = (hr & 0xFFFFFFFF, text)
            note_wedged(self.dead[0], text)

    def device_removed_error(self, where):
        """The one loud error for a removed device."""
        _removed, hr, text = self.device_status()
        return DlssSrError(self._removal_text(where, text))

    def _removal_text(self, where, text):
        multi = ""
        try:
            from . import cuda_luid
            total, _why = cuda_luid.count()
        except Exception:
            total = None
        if total is not None and total > 1:
            multi = (
                " (3) THIS PROCESS SEES %d CUDA DEVICES: on Windows a CUDA "
                "driver bug can poison the CUDA context as soon as more than "
                "one GPU is touched, and the driver then reports the D3D12 "
                "device as REMOVED (ComfyUI issue #15255 / PR #15451). Restart "
                "ComfyUI and launch it with --cuda-device %d (single GPU) "
                "and/or --disable-pinned-memory, then try the same frame "
                "again." % (total, self.ordinal if self.ordinal is not None
                            else 0))
        return (
            f"[ANTs] The D3D12 device is unusable - GetDeviceRemovedReason "
            f"reports {text} - and it failed while {where}. Everything queued "
            "or recorded on it is void, so this frame cannot be produced. The "
            "pack drops the native session and the GPU context WITHOUT "
            "releasing its objects (releasing a dead device's objects crashes "
            "inside the NVIDIA driver - rig 21:52 died that way), and the next "
            "frame cannot build a fresh device in this process. If it keeps "
            "happening: (1) the usual cause is a single GPU operation longer "
            "than the driver's ~2 s timeout (a big first evaluate or a huge "
            "frame) - run a much smaller image once to confirm, and raise the "
            "TDR delay if you need the big one (Windows registry: "
            "HKLM\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers\\"
            "TdrDelay, DWORD seconds, reboot); (2) RESTART ComfyUI before the "
            "next run - after a removal the driver refuses new devices in the "
            "process, and the next frame answers D3D12CreateDevice 0x887A0001 "
            "(DXGI_ERROR_INVALID_CALL), which is the same wedged state, not a "
            "second bug.") + multi

    def _submit_pair(self, command_list, allocator, timeout_ms, tag):
        if self.dead is not None:
            raise self.device_removed_error("submitting")
        try:
            command_list.call_hr(_LIST_CLOSE, [], what="Close")
        except DlssSrError as exc:
            removed, hr, text = self.device_status()
            if removed:
                self.mark_device_removed(hr, text)
                raise self.device_removed_error("closing the command list") \
                    from exc
            self._drop_recording(tag, exc, command_list)
            return
        cell = (_CVOID_P() * 1)(command_list.ptr)
        self.queue.call(_QUEUE_EXECUTE_COMMAND_LISTS, [_CVOID_U32(), _CVOID_P()], None,
                        ctypes.c_uint32(1), cell)
        self.fence_value += 1
        self.queue.call_hr(_QUEUE_SIGNAL, [_CVOID_P(), _CVOID_U64()],
                           self.fence.ptr, ctypes.c_uint64(self.fence_value), what="Signal")
        done = self.fence.call(_FENCE_GET_COMPLETED, [], ctypes.c_uint64)
        if done < self.fence_value:
            self.fence.call_hr(_FENCE_SET_EVENT, [_CVOID_U64(), _CVOID_P()],
                               ctypes.c_uint64(self.fence_value), ctypes.c_void_p(self.event),
                               what="SetEventOnCompletion")
            if not win32.wait_event(self.event, timeout_ms):
                removed, hr, text = self.device_status()
                if removed:
                    self.mark_device_removed(hr, text)
                    raise self.device_removed_error(
                        f"waiting for the GPU ({timeout_ms} ms, tag '{tag}')")
                raise DlssSrError(
                    f"[ANTs] The GPU did not finish within {timeout_ms} ms "
                    f"(tag '{tag}') while the device itself reports healthy "
                    f"({text}). A fence wait this long usually means the work "
                    "queued before it is enormous - try a smaller frame.")
        removed, hr, text = self.device_status()
        if removed:
            self.mark_device_removed(hr, text)
            raise self.device_removed_error(f"executing the '{tag}' recording")
        allocator.call_hr(_ALLOCATOR_RESET, [], what="Reset")
        command_list.call_hr(_LIST_RESET, [_CVOID_P(), _CVOID_P()],
                             allocator.ptr, None, what="Reset")
        if command_list is self.list:
            self.recorded.clear()       # the recording was executed and reset
        # The GPU is idle here, so staging buffers from the recording we just
        # executed can be freed (see upload_texture).
        while self._pending_release:
            try:
                self._pending_release.pop().release()
            except Exception:
                pass

    def _describe_list(self, command_list=None):
        """Say WHICH recording could not be closed, and what it holds.

        The 21:52 report proved that "ID3D12GraphicsCommandList.Close failed"
        alone cannot be acted on: it was our own copy list (device healthy, so
        something IN the recording was invalid) rather than the runtime's list.
        """
        if command_list is self.runtime_list:
            return ("the NGX runtime's command list (the runtime recorded the "
                    "feature work into it)")
        entries = self.recorded[-6:]
        if not entries:
            return "our copy command list (nothing recorded by us)"
        more = "" if len(self.recorded) <= 6 else \
            f", +{len(self.recorded) - 6} more"
        return (f"our copy command list ({len(self.recorded)} recorded "
                f"command(s): {', '.join(entries)}{more})")

    def _drop_recording(self, tag, exc, command_list=None):
        """A recording that cannot be closed is VOID - rebuild and carry on.

        Two causes are known from the rig:
          * 0x80070057 (E_INVALIDARG): the recording contained an invalid
            command (D3D12 poisons the list; one such command was our own
            barrier on an upload-heap resource - fixed at the source) or the
            runtime closed the list behind us;
          * 0x80004005 (E_FAIL): the ReShade-oriented RenoDX build mangles the
            shared list.
        Whatever the cause, the list state is unknown and must not be reused:
        the object is parked (never released while the GPU may still see it)
        and a FRESH allocator+list replaces it. Recovery never raises unless
        ANTS_D3D12_STRICT_CLOSE=1 asks for the hard error (A/B runs), because
        a diagnostic run must not lose the node to a recovery step.
        """
        if os.environ.get("ANTS_D3D12_STRICT_CLOSE") == "1":
            raise exc
        from ..log import dlss_logger
        _removed, _hr, status = self.device_status()
        who = self._describe_list(command_list)
        if tag == "runtime":
            self.runtime_recoveries += 1
            dlss_logger.warning(
                "[ANTs] D3D12: %s could not be closed after the feature call "
                "(%s) - the runtime's recording is VOID and this frame's "
                "enhanced output is stale or unchanged. The list and its "
                "allocator were replaced (recovery #%d). Please send this line "
                "with the console and the nvngx.log from the evidence "
                "collector; ANTS_D3D12_STRICT_CLOSE=1 turns this into a hard "
                "error if you prefer the run to stop here. Device status: %s",
                who, exc, self.runtime_recoveries, status)
            old_list, old_alloc = self.runtime_list, self.runtime_allocator
            self.runtime_list, self.runtime_allocator = None, None
            self._parked += [obj for obj in (old_list, old_alloc)
                             if obj is not None]
            self.command_list()          # fresh pair for the next frame
            return
        dlss_logger.warning(
            "[ANTs] D3D12 command list not closable (%s) - %s could not be "
            "closed while the device itself is HEALTHY (%s), so something in "
            "that recording is invalid. Dropping it and replacing the list: "
            "GPU work recorded since the last submit is LOST; if the output "
            "looks stale, send this line with the rest of the log. "
            "ANTS_NR_INPUT_STATE=uav tests the resource-state contract and "
            "ANTS_D3D12_CHECKPOINT=1 names the offending command; set "
            "ANTS_D3D12_STRICT_CLOSE=1 if you prefer the run to stop here.",
            exc, who, status)
        old_list, old_alloc = self.list, self.allocator
        self._parked += [obj for obj in (old_list, old_alloc) if obj is not None]
        self._pending_release.clear()   # the recording never reached the GPU
        try:
            self.allocator = self.device.create_command_allocator()
            self.list = self.device.create_command_list(
                self.allocator, "ID3D12GraphicsCommandList (ours)")
        except DlssSrError as exc:
            # Creating objects can itself fail when the device went away in
            # the meantime - report THAT, not the cascade (rig 20:39:
            # CreateCommandAllocator 0x887A0005 after two E_INVALIDARGs).
            removed, hr, text = self.device_status()
            if removed or "887A0005" in str(exc).upper():
                self.mark_device_removed(hr, text)
                raise self.device_removed_error("rebuilding the command list") \
                    from exc
            raise

    def close(self):
        """Drop the context - or leave the corpse alone if the device is gone.

        Releasing D3D12 objects that belong to a REMOVED device crashes inside
        the NVIDIA user-mode driver: rig 21:52 died with an access violation at
        nvwgf2umx.dll+0x6D3471 while ``ComObject.release`` was in flight, one
        frame after the removal (and the crash box named the command list).
        Nothing is lost by not releasing them - the process has to be
        restarted after a removal anyway - so the objects are parked and the
        console says so.
        """
        if self._closed:
            return
        self._closed = True
        win32.close_handle(self.event)
        if self.dead is None:
            removed, hr, text = self.device_status()
            if removed:
                self.mark_device_removed(hr, text)
        else:
            # closing a context whose device is gone IS the process-wide
            # "this process lost its device" fact (idempotent)
            note_wedged(*self.dead)
        objects = [obj for obj in (self.list, self.allocator, self.runtime_list,
                                   self.runtime_allocator, self.queue, self.fence,
                                   *self._parked, *self._pending_release)
                   if obj is not None]
        self._parked.clear()
        self._pending_release.clear()
        self.adapter = None
        if self.dead is not None:
            _, hr, text = self.device_status()
            from ..log import dlss_logger
            dlss_logger.warning(
                "[ANTs] NOT releasing %d D3D12 object(s): the device is gone "
                "(%s) and releasing them crashes inside the NVIDIA driver "
                "(rig 21:52: access violation at nvwgf2umx.dll during "
                "ID3D12GraphicsCommandList::Release). They are left to the "
                "process teardown; restart ComfyUI before the next run.",
                len(objects), text)
            return
        for obj in objects:
            try:
                obj.release()
            except Exception:
                pass

"""Minimal D3D12/DXGI bindings for the NGX host (only what NGX needs).

One adapter, one device, one direct queue + allocator + command list + fence
("record, submit, wait"), committed textures/buffers, staging upload and
readback. Vtable slots are 0-based including IUnknown and match the public
d3d12.h / dxgi.h declarations.
"""

import ctypes

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
D3D12_HEAP_TYPE_DEFAULT = 0
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
D3D12_TEXTURE_DATA_PITCH_ALIGNMENT = 256
D3D12_TEXTURE_DATA_PLACEMENT_ALIGNMENT = 512

DXGI_FORMAT_UNKNOWN = 0
DXGI_FORMAT_R8G8B8A8_UNORM = 28
DXGI_FORMAT_R16G16_FLOAT = 34
DXGI_FORMAT_R32_FLOAT = 41

BPP = {DXGI_FORMAT_R8G8B8A8_UNORM: 4, DXGI_FORMAT_R16G16_FLOAT: 4, DXGI_FORMAT_R32_FLOAT: 4}

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
    desc = _pack(_RESOURCE_DESC_FMT, 2, 0, width, height, 1, 1, fmt, 1, 0, 0, flags)
    assert len(desc) == 56
    return desc


def _resource_desc_buffer(size):
    desc = _pack(_RESOURCE_DESC_FMT, 1, 0, size, 0, 1, 1, DXGI_FORMAT_UNKNOWN, 1, 0, 0, 0)
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


class D3D12Resource(ComObject):
    def __init__(self, ptr, label, width, height, fmt, byte_size, state):
        super().__init__(ptr, label)
        self.width = width
        self.height = height
        self.format = fmt
        self.byte_size = byte_size
        self.state = state

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

    @staticmethod
    def create(adapter_ptr=None, feature_level=D3D_FEATURE_LEVEL_11_0):
        create = win32.d3d12_create_device_symbol()
        create.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                           ctypes.POINTER(ctypes.c_char * 16),
                           ctypes.POINTER(ctypes.c_void_p)]
        create.restype = ctypes.c_int32
        out = ctypes.c_void_p()
        hr = create(adapter_ptr, feature_level, IID_ID3D12Device, ctypes.byref(out))
        hresult_check(hr, "D3D12CreateDevice")
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

    def create_command_list(self, allocator):
        return ComObject(
            self._create(_DEVICE_CREATE_COMMAND_LIST, "CreateCommandList",
                         [_CVOID_U32(), _CVOID_U32(), _CVOID_P(), _CVOID_P()],
                         ctypes.c_uint32(0), ctypes.c_uint32(D3D12_COMMAND_LIST_TYPE_DIRECT),
                         allocator.ptr, None, IID_ID3D12GraphicsCommandList),
            "ID3D12GraphicsCommandList")

    def create_fence(self, initial=0):
        return ComObject(
            self._create(_DEVICE_CREATE_FENCE, "CreateFence",
                         [_CVOID_U64(), _CVOID_U32()],
                         ctypes.c_uint64(initial), ctypes.c_uint32(0), IID_ID3D12Fence),
            "ID3D12Fence")

    def device_removed_reason(self):
        return self.call(_DEVICE_DEVICE_REMOVED_REASON, [], ctypes.c_int32)

    def _committed(self, heap_type, desc, initial_state, label, heap_flags=0):
        heap = _heap_properties(heap_type)
        return self._create(_DEVICE_CREATE_COMMITTED_RESOURCE, f"CreateCommittedResource({label})",
                            [_CVOID_P(), _CVOID_U32(), _CVOID_P(), _CVOID_U32(), _CVOID_P()],
                            heap, ctypes.c_uint32(heap_flags), desc,
                            ctypes.c_uint32(initial_state), None, IID_ID3D12Resource)

    def create_texture2d(self, width, height, fmt, allow_uav=True, label="texture"):
        flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS if allow_uav else 0
        desc = _resource_desc_texture(width, height, fmt, flags)
        ptr = self._committed(D3D12_HEAP_TYPE_DEFAULT, desc,
                              D3D12_RESOURCE_STATE_COMMON, label)
        return D3D12Resource(ptr, label, width, height, fmt, width * height * BPP[fmt],
                             D3D12_RESOURCE_STATE_COMMON)

    def create_buffer(self, size, heap_type, label="buffer"):
        state = {"upload": D3D12_RESOURCE_STATE_GENERIC_READ,
                 "readback": D3D12_RESOURCE_STATE_COPY_DEST}.get(
                     {D3D12_HEAP_TYPE_UPLOAD: "upload",
                      D3D12_HEAP_TYPE_READBACK: "readback"}.get(heap_type),
                     D3D12_RESOURCE_STATE_COMMON)
        ptr = self._committed(heap_type, _resource_desc_buffer(size), state, label)
        return D3D12Resource(ptr, label, size, 1, DXGI_FORMAT_UNKNOWN, size, state)


def enumerate_adapters():
    """List (adapter_ptr, description) — call release() on the ones not used."""
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
        desc = ctypes.create_string_buffer(312)  # DXGI_ADAPTER_DESC1
        adapter.call_hr(10, [_CVOID_P()], desc, what="GetDesc1")  # GetDesc1 = 10
        wide_part = ctypes.wstring_at(ctypes.addressof(desc), 64)
        adapters.append((adapter, wide_part.split("\x00")[0]))
        index += 1
    return factory, adapters


class GpuContext:
    """One queue/allocator/list/fence; record, submit, wait (synchronous)."""

    def __init__(self, device, adapter_index=0):
        self.device = device
        self.factory, adapters = enumerate_adapters()
        self.adapter = adapters[adapter_index][0] if adapters else None
        self.queue = device.create_command_queue()
        self.allocator = device.create_command_allocator()
        self.list = device.create_command_list(self.allocator)
        self.fence = device.create_fence()
        self.event = win32.create_event()
        self.fence_value = 0
        self._closed = False

    def transition(self, resource, to):
        barrier = _transition_barrier(resource.ptr, resource.state, to)
        self.list.call(_LIST_RESOURCE_BARRIER, [_CVOID_U32(), _CVOID_P()], None,
                       ctypes.c_uint32(1), barrier)
        resource.state = to

    def upload_texture(self, texture, pixels, final_state):
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
        self.transition(staging, D3D12_RESOURCE_STATE_COPY_SOURCE)
        self.transition(texture, D3D12_RESOURCE_STATE_COPY_DEST)
        src_loc = _copy_location_footprint(staging.ptr, texture.format,
                                           texture.width, texture.height, row_pitch)
        dst_loc = _copy_location_texture(texture.ptr)
        self.list.call(_LIST_COPY_TEXTURE_REGION,
                       [_CVOID_P(), _CVOID_U32(), _CVOID_U32(), _CVOID_U32(),
                        _CVOID_P(), _CVOID_P()],
                       None, dst_loc, 0, 0, 0, src_loc, None)
        self.transition(texture, final_state)
        self.transition(staging, D3D12_RESOURCE_STATE_COMMON)
        staging.release()

    def readback_texture(self, texture, state):
        _, row_pitch, total = linear_layout(texture.width, texture.height, texture.format)
        self.transition(texture, D3D12_RESOURCE_STATE_COPY_SOURCE)
        readback = self.device.create_buffer(total, D3D12_HEAP_TYPE_READBACK, "readback staging")
        dst_loc = _copy_location_footprint(readback.ptr, texture.format,
                                           texture.width, texture.height, row_pitch)
        src_loc = _copy_location_texture(texture.ptr)
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
        self.list.call_hr(_LIST_CLOSE, [], what="Close")
        cell = (_CVOID_P() * 1)(self.list.ptr)
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
                reason = self.device.device_removed_reason()
                raise DlssSrError(
                    f"[ANTs] GPU did not finish within {timeout_ms} ms "
                    f"(device removed reason 0x{reason & 0xFFFFFFFF:08X}).")
        removed = self.device.device_removed_reason()
        if removed < 0:
            raise DlssSrError(
                f"[ANTs] D3D12 device removed: 0x{removed & 0xFFFFFFFF:08X}.")
        self.allocator.call_hr(_ALLOCATOR_RESET, [], what="Reset")
        self.list.call_hr(_LIST_RESET, [_CVOID_P(), _CVOID_P()],
                          self.allocator.ptr, None, what="Reset")

    def close(self):
        if self._closed:
            return
        self._closed = True
        win32.close_handle(self.event)
        for obj in (self.list, self.allocator, self.queue, self.fence):
            try:
                obj.release()
            except Exception:
                pass
        self.adapter = None

"""NGX parameter objects: the core-allocated wrapper and our own store.

Two flavors exist because the runtimes differ:

- the **driver NGX core** (`_nvngx.dll`) allocates parameter objects
  (``NVSDK_NGX_D3D12_AllocateParameters``) whose vtable has the MSVC layout
  (setPointer 0, setD3d12 1, ... setU64 7, getters 8-15 mirrored, reset 16);
- the **snippet runtimes** (`nvngx_dlssnr.dll`) export the whole NGX API
  except the parameter allocator — so we hand them OUR OWN object: a
  17-slot vtable (same MSVC order) over a Python dict, implemented with
  ctypes callbacks. Unknown parameter names are simply never read by the
  runtime (inert), and unset names answer NVSDK_NGX_Result_FeatureNotFound
  (0xBAD00004) which the runtimes treat as "parameter not set".
"""

import ctypes

from .com import ComObject
from .errors import DlssSrError

NGX_SUCCESS = 0x1
NGX_RESULT_FEATURE_NOT_FOUND = 0xBAD00004

_CVOID = ctypes.c_void_p
_CU32 = ctypes.c_uint32
_CI32 = ctypes.c_int32
_CF32 = ctypes.c_float
_CF64 = ctypes.c_double
_CU64 = ctypes.c_uint64

# MSVC layout (the only one current runtimes use; see params docs)
SLOT_SET_POINTER = 0
SLOT_SET_D3D12 = 1
SLOT_SET_D3D11 = 2
SLOT_SET_I32 = 3
SLOT_SET_U32 = 4
SLOT_SET_F64 = 5
SLOT_SET_F32 = 6
SLOT_SET_U64 = 7
SLOT_GET_POINTER = 8
SLOT_GET_D3D12 = 9
SLOT_GET_D3D11 = 10
SLOT_GET_I32 = 11
SLOT_GET_U32 = 12
SLOT_GET_F64 = 13
SLOT_GET_F32 = 14
SLOT_GET_U64 = 15
SLOT_RESET = 16


def _read_cstring(ptr):
    return ctypes.string_at(ptr).decode("utf-8", errors="ignore")


class OwnParameterObject:
    """A snippet-consumable NVSDK_NGX_Parameter backed by a Python dict."""

    def __init__(self):
        self.store = {}
        self._callbacks = []
        vtable = ctypes.c_void_p * 17
        self._vtable = vtable()
        for slot in range(8):
            value_types = {
                0: _CU64, 1: _CU64, 2: _CU64,
                3: _CI32, 4: _CU32, 5: _CF64, 6: _CF32, 7: _CU64,
            }
            is_u64 = slot == 7

            def make_setter(is_u64=is_u64):
                proto = ctypes.CFUNCTYPE(None, _CVOID, _CVOID,
                                         value_types[slot])
                def impl(_self, name_ptr, value):
                    self.store[_read_cstring(name_ptr)] = int(value) if is_u64 or slot in (0, 1, 2) else float(value)
                cb = proto(impl)
                self._callbacks.append(cb)
                return cb

            setter = make_setter()
            self._vtable[slot] = ctypes.cast(setter, _CVOID).value

        for slot in range(8, 16):
            width = {
                8: _CU64, 9: _CU64, 10: _CU64, 11: _CI32,
                12: _CU32, 13: _CF64, 14: _CF32, 15: _CU64,
            }[slot]

            def make_getter(width=width):
                proto = ctypes.CFUNCTYPE(_CI32, _CVOID, _CVOID, _CVOID)
                def impl(_self, name_ptr, out_ptr):
                    name = _read_cstring(name_ptr)
                    if name not in self.store:
                        return ctypes.c_int32(NGX_RESULT_FEATURE_NOT_FOUND).value
                    value = self.store[name]
                    ctypes.memmove(out_ptr, ctypes.byref(width(int(value) if isinstance(value, int) else value)), ctypes.sizeof(width))
                    return NGX_SUCCESS
                cb = proto(impl)
                self._callbacks.append(cb)
                return cb

            getter = make_getter()
            self._vtable[slot] = ctypes.cast(getter, _CVOID).value

        reset_proto = ctypes.CFUNCTYPE(None, _CVOID)

        def reset_impl(_self):
            self.store.clear()

        self._reset_cb = reset_proto(reset_impl)
        self._callbacks.append(self._reset_cb)
        self._vtable[SLOT_RESET] = ctypes.cast(self._reset_cb, _CVOID).value

        # The object's first field points at the vtable.
        object_type = ctypes.c_void_p * 1
        self._object = object_type(ctypes.cast(self._vtable, _CVOID))

    @property
    def ptr(self):
        return ctypes.cast(self._object, _CVOID)

    # Python-side setters (host convenience; identical semantics)
    def set_u32(self, name, value):
        self.store[name] = int(value) & 0xFFFFFFFF

    def set_i32(self, name, value):
        self.store[name] = int(value)

    def set_f32(self, name, value):
        self.store[name] = float(value)

    def set_f64(self, name, value):
        self.store[name] = float(value)

    def set_u64(self, name, value):
        self.store[name] = int(value)

    def set_resource(self, name, pointer):
        self.store[name] = int(pointer)

    def close(self):
        self._callbacks.clear()


class CoreParameterObject(ComObject):
    """A core-allocated NVSDK_NGX_Parameter, driven through its vtable."""

    def __init__(self, ptr):
        super().__init__(ptr, "NVSDK_NGX_Parameter")

    def _setter(self, slot, argtype):
        return self.vtable_method(slot, [_CVOID, argtype], None)

    def set_u32(self, name, value):
        buf = ctypes.create_string_buffer(name.encode() + b"\x00")
        self._setter(SLOT_SET_U32, _CU32)(self.ptr, buf, int(value) & 0xFFFFFFFF)

    def set_i32(self, name, value):
        buf = ctypes.create_string_buffer(name.encode() + b"\x00")
        self._setter(SLOT_SET_I32, _CI32)(self.ptr, buf, int(value))

    def set_f32(self, name, value):
        buf = ctypes.create_string_buffer(name.encode() + b"\x00")
        self._setter(SLOT_SET_F32, _CF32)(self.ptr, buf, float(value))

    def set_f64(self, name, value):
        buf = ctypes.create_string_buffer(name.encode() + b"\x00")
        self._setter(SLOT_SET_F64, _CF64)(self.ptr, buf, float(value))

    def set_u64(self, name, value):
        buf = ctypes.create_string_buffer(name.encode() + b"\x00")
        self._setter(SLOT_SET_U64, _CU64)(self.ptr, buf, int(value))

    def set_resource(self, name, pointer):
        buf = ctypes.create_string_buffer(name.encode() + b"\x00")
        self._setter(SLOT_SET_D3D12, _CVOID)(self.ptr, buf, ctypes.c_void_p(int(pointer)))

    def get_u32(self, name):
        buf = ctypes.create_string_buffer(name.encode() + b"\x00")
        out = _CU32(0)
        fn = self.vtable_method(SLOT_GET_U32, [_CVOID, _CVOID, _CVOID], _CI32)
        hr = fn(self.ptr, buf, ctypes.byref(out))
        if hr != NGX_SUCCESS:
            raise DlssSrError(f"[ANTs] Parameter {name!r} not available (0x{hr & 0xFFFFFFFF:08X}).")
        return out.value

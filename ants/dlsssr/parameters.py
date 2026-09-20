"""NGX parameter objects: the core-allocated wrapper and our own store.

Two flavors exist because the runtimes differ:

- the **driver NGX core** (`_nvngx.dll`) allocates the parameter maps
  (``NVSDK_NGX_D3D12_AllocateParameters`` / ``..._GetCapabilityParameters``)
  that both the ordinary DLSS features and the NR snippet expect;
- the **snippet runtimes** (`nvngx_dlssnr.dll`) export the whole NGX D3D12
  API except the parameter allocator — so in the legacy snippet-direct route
  we hand them OUR OWN object: a vtable over a Python dict, built with ctypes
  callbacks. Unknown names are simply never read by the runtime (inert), and
  unset names answer NVSDK_NGX_Result_FeatureNotFound (0xBAD00004), which the
  runtimes treat as "parameter not set".

**The vtable layout is the tricky part.** The public header
(``nvsdk_ngx_params.h``) declares ``Set`` in the order
``(u64, float, double, uint, int, d3d11, d3d12, void*)``, but the *shipped*
runtime exposes the overloads grouped by argument family instead — the
community host that already drives ``nvngx_dlssnr.dll`` documents and uses

    resource -> slot 0,  void* -> slot 2,  int/uint -> slot 3,  float -> 6

and reports that writing a float through the header's slot 1 leaves every
float parameter at its default, which the runtime answers with
``0xBAD00005`` (InvalidParameter). Only the **intersection** of both mappings
is safe to use blind: slot 0 (64-bit value / resource pointer), slot 3
(32-bit integers — the sign is a bit pattern) and slot 6 (float).

Two ways out of the ambiguity, both implemented here:

1. ``NVSDK_NGX_Parameter_Set{F,UI,I,ULL,D3d12Resource,VoidPointer}`` — the
   flat C parameter API NVIDIA ships *precisely because* the C++ vtable order
   moves between versions. When the loaded module exports them we use them
   and never touch a vtable slot (``preferred`` backend).
2. ``ANTS_NR_PARAM_ABI=header`` forces the public-header mapping for the
   vtable backend, for a rig A/B if the shipped layout ever differs.
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

# --- the proven (shipped-runtime) map -------------------------------------
SLOT_SET_RESOURCE = 0      # Set(const char*, ID3D12Resource*) family / u64
SLOT_SET_POINTER = 2       # Set(const char*, void*) - e.g. our callbacks
SLOT_SET_I32 = 3           # int and unsigned int share the 32-bit slot
SLOT_SET_U32 = 3
SLOT_SET_F32 = 6

# --- the public-header map (ANTS_NR_PARAM_ABI=header) ---------------------
HEADER_SLOT_SET_U64 = 0
HEADER_SLOT_SET_F32 = 1
HEADER_SLOT_SET_F64 = 2
HEADER_SLOT_SET_U32 = 3
HEADER_SLOT_SET_I32 = 4
HEADER_SLOT_SET_D3D11 = 5
HEADER_SLOT_SET_D3D12 = 6
HEADER_SLOT_SET_POINTER = 7

# Getter slots of OUR OWN object (read by the runtime, never by us blind):
# 8 u64, 9 resource, 10 d3d11, 11 i32, 12 u32, 13 f64, 14 f32, 15 u64.
SLOT_GET_U64 = 8
SLOT_GET_RESOURCE = 9
SLOT_GET_D3D11 = 10
SLOT_GET_I32 = 11
SLOT_GET_U32 = 12
SLOT_GET_F64 = 13
SLOT_GET_F32 = 14
SLOT_GET_POINTER = 15
SLOT_RESET = 16


def _read_cstring(ptr):
    return ctypes.string_at(ptr).decode("utf-8", errors="ignore")


class OwnParameterObject:
    """A snippet-consumable NVSDK_NGX_Parameter backed by a Python dict."""

    @property
    def backend(self):
        return "own-object"

    def __init__(self):
        self.store = {}
        self._callbacks = []
        vtable = ctypes.c_void_p * 17
        self._vtable = vtable()
        # Setters: every pointer-ish slot stores an int, every integer slot an
        # int and every float slot a float, so a runtime that picks a
        # different (but family-correct) slot still reads a sane value.
        pointer_slots = (SLOT_SET_RESOURCE, 1, SLOT_SET_POINTER)
        integer_slots = (SLOT_SET_I32, SLOT_SET_U32, 4)
        float_slots = (5, SLOT_SET_F32)
        u64_slots = (7,)

        def make_setter(width, store_float):
            proto = ctypes.CFUNCTYPE(None, _CVOID, _CVOID, width)

            def impl(_self, name_ptr, value):
                key = _read_cstring(name_ptr)
                if store_float:
                    self.store[key] = float(value)
                else:
                    try:
                        self.store[key] = int(value)
                    except (TypeError, ValueError):
                        self.store[key] = value
            cb = proto(impl)
            self._callbacks.append(cb)
            return cb

        for slot in pointer_slots:
            self._vtable[slot] = ctypes.cast(
                make_setter(_CU64, False), _CVOID).value
        for slot in integer_slots:
            self._vtable[slot] = ctypes.cast(
                make_setter(_CI32, False), _CVOID).value
        for slot in float_slots:
            self._vtable[slot] = ctypes.cast(
                make_setter(_CF32, True), _CVOID).value
        for slot in u64_slots:
            self._vtable[slot] = ctypes.cast(
                make_setter(_CU64, False), _CVOID).value

        widths = {
            SLOT_GET_U64: _CU64, SLOT_GET_RESOURCE: _CU64, SLOT_GET_D3D11: _CU64,
            SLOT_GET_I32: _CI32, SLOT_GET_U32: _CU32, SLOT_GET_F64: _CF64,
            SLOT_GET_F32: _CF32, SLOT_GET_POINTER: _CU64,
        }
        for slot, width in widths.items():
            self._vtable[slot] = ctypes.cast(
                self._make_getter(width), _CVOID).value

        reset_proto = ctypes.CFUNCTYPE(None, _CVOID)

        def reset_impl(_self):
            self.store.clear()

        self._reset_cb = reset_proto(reset_impl)
        self._callbacks.append(self._reset_cb)
        self._vtable[SLOT_RESET] = ctypes.cast(self._reset_cb, _CVOID).value

        # The object's first field points at the vtable.
        # 64-byte object (first field -> vtable): some runtimes touch bytes
        # beyond the pointer; a tight 8-byte allocation turns any stray
        # access into heap corruption (observed as a crash inside the very
        # first EvaluateFeature - rig run 15; DVT pads for the same reason)
        object_type = ctypes.c_void_p * 8
        self._object = object_type(ctypes.cast(self._vtable, _CVOID),
                                   None, None, None, None, None, None, None)

    def _make_getter(self, width):
        """A Get overload: returns the stored value in `width`, coercing
        numerically when the runtime asks for a different family (an int
        stored for a float name is a value, not corruption)."""
        proto = ctypes.CFUNCTYPE(_CI32, _CVOID, _CVOID, _CVOID)

        def impl(_self, name_ptr, out_ptr):
            name = _read_cstring(name_ptr)
            if name not in self.store:
                return ctypes.c_int32(NGX_RESULT_FEATURE_NOT_FOUND).value
            value = self.store[name]
            try:
                if width is _CF32:
                    cell = _CF32(float(value))
                elif width is _CF64:
                    cell = _CF64(float(value))
                elif width is _CI32:
                    cell = _CI32(int(value) & 0xFFFFFFFF if int(value) > 0x7FFFFFFF else int(value))
                else:
                    cell = width(int(value) & 0xFFFFFFFFFFFFFFFF
                                 if not isinstance(value, float) else int(value))
            except (TypeError, ValueError, OverflowError):
                from ..log import dlss_logger
                dlss_logger.status(
                    "NGX parameter %r requested as %s but stored as %r - "
                    "answering FeatureNotFound.", name, width.__name__, value)
                return ctypes.c_int32(NGX_RESULT_FEATURE_NOT_FOUND).value
            ctypes.memmove(out_ptr, ctypes.byref(cell), ctypes.sizeof(width))
            return NGX_SUCCESS

        cb = proto(impl)
        self._callbacks.append(cb)
        return cb

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
        self.store[name] = (pointer.value
                            if isinstance(pointer, ctypes.c_void_p)
                            else int(pointer))

    def set_pointer(self, name, pointer):
        if pointer is None:
            self.store[name] = 0
        elif hasattr(pointer, "value"):
            self.store[name] = pointer.value
        elif callable(pointer):
            # a ctypes callback: keep it alive here, store its address
            self.store[name] = ctypes.cast(pointer, _CVOID).value
        else:
            self.store[name] = int(pointer)

    def close(self):
        self._callbacks.clear()


# Names of the flat C parameter API (nvsdk_ngx_params.h). All of them are
# void(void* map, const char* name, value) and are exported by the NGX runtime
# itself, so passing the CORE's map object through them is ABI-exact.
FLAT_API_SETTERS = (
    "NVSDK_NGX_Parameter_SetULL",
    "NVSDK_NGX_Parameter_SetF",
    "NVSDK_NGX_Parameter_SetD",
    "NVSDK_NGX_Parameter_SetUI",
    "NVSDK_NGX_Parameter_SetI",
    "NVSDK_NGX_Parameter_SetD3d11Resource",
    "NVSDK_NGX_Parameter_SetD3d12Resource",
    "NVSDK_NGX_Parameter_SetVoidPointer",
)


def flat_api_slots(kind, abi=None):
    """(resource, pointer, i32/u32, f32) vtable slots for the chosen ABI."""
    if abi is None:
        import os
        abi = os.environ.get("ANTS_NR_PARAM_ABI", "shipped").lower()
    if abi == "header":
        return (HEADER_SLOT_SET_D3D12, HEADER_SLOT_SET_POINTER,
                HEADER_SLOT_SET_I32, HEADER_SLOT_SET_F32)
    return (SLOT_SET_RESOURCE, SLOT_SET_POINTER, SLOT_SET_I32, SLOT_SET_F32)


class CoreParameterObject(ComObject):
    """A core-allocated NVSDK_NGX_Parameter, driven through its vtable — or
    through NVIDIA's flat C parameter API when the module exports it (the
    ABI-stable route; see the module docstring)."""

    def __init__(self, ptr, flat_api=None, abi=None):
        super().__init__(ptr, "NVSDK_NGX_Parameter")
        self.flat_api = flat_api or {}
        self._slots = flat_api_slots(None, abi)
        self.written = []   # parameter names we set, for diagnostics/logging

    @property
    def backend(self):
        return "c-api" if self.flat_api else "vtable"

    def _name_buf(self, name):
        self.written.append(name)
        return ctypes.create_string_buffer(name.encode() + b"\x00")

    def _setter(self, slot, argtype):
        return self.vtable_method(slot, [_CVOID, argtype], None)

    def _flat(self, fn_name, name, value, argtype):
        fn = self.flat_api.get(fn_name)
        if fn is None:
            return False
        fn(self.ptr, self._name_buf(name), argtype(value))
        return True

    def set_u32(self, name, value):
        if self._flat("NVSDK_NGX_Parameter_SetUI", name, value, _CU32):
            return
        self._setter(self._slots[2], _CU32)(self.ptr, self._name_buf(name),
                                            int(value) & 0xFFFFFFFF)

    def set_i32(self, name, value):
        if self._flat("NVSDK_NGX_Parameter_SetI", name, value, _CI32):
            return
        self._setter(self._slots[2], _CI32)(self.ptr, self._name_buf(name),
                                            int(value))

    def set_f32(self, name, value):
        if self._flat("NVSDK_NGX_Parameter_SetF", name, value, _CF32):
            return
        self._setter(self._slots[3], _CF32)(self.ptr, self._name_buf(name),
                                            float(value))

    def set_f64(self, name, value):
        if self._flat("NVSDK_NGX_Parameter_SetD", name, value, _CF64):
            return
        self._setter(2, _CF64)(self.ptr, self._name_buf(name), float(value))

    def set_u64(self, name, value):
        if self._flat("NVSDK_NGX_Parameter_SetULL", name, value, _CU64):
            return
        self._setter(0, _CU64)(self.ptr, self._name_buf(name), int(value))

    def set_resource(self, name, pointer):
        raw = pointer.value if isinstance(pointer, ctypes.c_void_p) else int(pointer)
        if self._flat("NVSDK_NGX_Parameter_SetD3d12Resource", name, raw,
                      _CVOID):
            return
        self._setter(self._slots[0], _CU64)(
            self.ptr, self._name_buf(name), raw)

    def set_pointer(self, name, pointer):
        if pointer is None:
            raw = 0
        elif hasattr(pointer, "value"):
            raw = pointer.value or 0
        elif callable(pointer):
            raw = ctypes.cast(pointer, _CVOID).value
        else:
            raw = int(pointer)
        if self._flat("NVSDK_NGX_Parameter_SetVoidPointer", name, raw, _CVOID):
            return
        self._setter(self._slots[1], _CVOID)(self.ptr, self._name_buf(name),
                                             ctypes.c_void_p(raw))

    def get_u32(self, name):
        buf = self._name_buf(name)
        out = _CU32(0)
        fn = self.vtable_method(SLOT_GET_U32, [_CVOID, _CVOID, _CVOID], _CI32)
        hr = fn(self.ptr, buf, ctypes.byref(out))
        if hr != NGX_SUCCESS:
            raise DlssSrError(f"[ANTs] Parameter {name!r} not available (0x{hr & 0xFFFFFFFF:08X}).")
        return out.value


FLAT_API_ARG_TYPES = {
    "NVSDK_NGX_Parameter_SetULL": _CU64,
    "NVSDK_NGX_Parameter_SetF": _CF32,
    "NVSDK_NGX_Parameter_SetD": _CF64,
    "NVSDK_NGX_Parameter_SetUI": _CU32,
    "NVSDK_NGX_Parameter_SetI": _CI32,
    "NVSDK_NGX_Parameter_SetD3d11Resource": _CVOID,
    "NVSDK_NGX_Parameter_SetD3d12Resource": _CVOID,
    "NVSDK_NGX_Parameter_SetVoidPointer": _CVOID,
}


def build_flat_api(module):
    """Resolve NVIDIA's flat C parameter setters from a loaded NgxModule.

    Returns {} when the module does not export them (older cores), which
    leaves the vtable backend in charge.
    """
    api = {}
    for name in FLAT_API_SETTERS:
        try:
            if not module.has_export(name):
                continue
        except Exception:
            continue
        api[name] = module.fn(name, [_CVOID, _CVOID, FLAT_API_ARG_TYPES[name]])
    return api

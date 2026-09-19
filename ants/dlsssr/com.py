"""Tiny COM helpers: vtable calls by slot, HRESULT checking, GUIDs.

Slot numbers are 0-based and INCLUDE IUnknown (QueryInterface 0, AddRef 1,
Release 2). The numbers used across this package match the public D3D12
headers (ID3D12Device::CreateCommittedResource = 27, etc.).
"""

import ctypes

from .errors import DlssSrError

_S_OK = 0
_POINTER = ctypes.POINTER
_CVOID = ctypes.c_void_p


def hresult_check(hr, what):
    if hr < 0:
        raise DlssSrError(f"[ANTs] {what} failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")


def guid(text):
    """'{xxxxxxxx-...}' -> 16-byte GUID buffer."""
    hexpart = text.strip("{}")
    parts = hexpart.split("-")
    raw = bytes.fromhex(parts[3] + parts[2] + parts[1] + parts[0]) + bytes.fromhex("".join(parts[4:]))
    return (ctypes.c_char * 16).from_buffer_copy(raw)


class ComObject:
    """A COM interface pointer with slot-based vtable calls."""

    __slots__ = ("ptr", "label", "_vtable")

    def __init__(self, ptr, label):
        self.ptr = ctypes.c_void_p(ptr)
        self.label = label
        self._vtable = ctypes.cast(self.ptr, _POINTER(_POINTER(_CVOID))).contents

    def _slot_proto(self, slot, argtypes, restype):
        return ctypes.CFUNCTYPE(restype, _CVOID, *argtypes)(self._vtable[slot])

    def call(self, slot, argtypes, restype, *args):
        fn = self._slot_proto(slot, argtypes, restype)
        return fn(self.ptr, *args)

    def call_hr(self, slot, argtypes, *args, what="COM call"):
        hr = self.call(slot, argtypes, ctypes.c_int32, *args)
        hresult_check(hr, f"{self.label}.{what}")
        return hr

    def add_ref(self):
        self.call(1, [], ctypes.c_uint64)

    def release(self):
        if self.ptr:
            self.call(2, [], ctypes.c_uint64)
            self.ptr = None

    def vtable_method(self, slot, argtypes, restype):
        """Bind a slot once for repeated calls (parameter objects, hot loops)."""
        return self._slot_proto(slot, argtypes, restype)

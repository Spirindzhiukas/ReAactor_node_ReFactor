"""Tiny COM helpers: vtable calls by slot, HRESULT checking, GUIDs.

Slot numbers are 0-based and INCLUDE IUnknown (QueryInterface 0, AddRef 1,
Release 2). The numbers used across this package match the public D3D12
headers (ID3D12Device::CreateCommittedResource = 27, etc.).
"""

import ctypes

from . import crashlog
from .errors import DlssSrError

_S_OK = 0
_POINTER = ctypes.POINTER
_CVOID = ctypes.c_void_p


def hresult_check(hr, what):
    if hr < 0:
        raise DlssSrError(f"[ANTs] {what} failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")


def guid(text):
    """'{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}' -> 16-byte GUID buffer.

    Windows wire layout: Data1 u32 LE, Data2 u16 LE, Data3 u16 LE, then
    Data4's 8 bytes verbatim.
    """
    parts = text.strip("{}").split("-")
    raw = (int(parts[0], 16).to_bytes(4, "little")
           + int(parts[1], 16).to_bytes(2, "little")
           + int(parts[2], 16).to_bytes(2, "little")
           + bytes.fromhex(parts[3] + parts[4]))
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
        # the crash box prints the in-flight label on any TERMINATION /
        # exception line, so a kill inside a call is not anonymous
        previous = crashlog.phase()
        crashlog.set_phase(self.label)
        try:
            return fn(self.ptr, *args)
        finally:
            crashlog.set_phase(previous)

    def call_hr(self, slot, argtypes, *args, what="COM call"):
        previous = crashlog.phase()
        crashlog.set_phase(f"{self.label}.{what}")
        try:
            hr = self.call(slot, argtypes, ctypes.c_int32, *args)
        finally:
            crashlog.set_phase(previous)
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

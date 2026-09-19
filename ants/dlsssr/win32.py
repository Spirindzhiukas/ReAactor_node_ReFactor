"""Minimal Win32/D3D12 loader layer for the NGX host (Windows only).

Everything here raises :class:`DlssSrError` with an ``[ANTs]`` prefix and a
concrete fix hint when used off-Windows or against a broken system — import
is always safe.
"""

import ctypes
import os

from .errors import DlssSrError

_is_windows = os.name == "nt"
_k32 = ctypes.WinDLL("kernel32", use_last_error=True) if _is_windows else None
_d3d12 = ctypes.WinDLL("d3d12", use_last_error=True) if _is_windows else None
_dxgi = ctypes.WinDLL("dxgi", use_last_error=True) if _is_windows else None

_LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008
_GENERIC_ALL = 0x10000000
_INFINITE = 0xFFFFFFFF
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102


def _require_windows(what):
    if not _is_windows:
        raise DlssSrError(
            f"[ANTs] {what} requires Windows (D3D12/NGX). "
            "Run this nodepack's DLSS nodes on the Windows ComfyUI installation.")


def load_library(path):
    """LoadLibraryExW with altered search path; returns a module handle."""
    _require_windows(f"Loading {os.path.basename(path)}")
    handle = _k32.LoadLibraryExW(ctypes.c_wchar_p(str(path)), None, _LOAD_WITH_ALTERED_SEARCH_PATH)
    if not handle:
        raise DlssSrError(
            f"[ANTs] LoadLibraryExW failed for {path} (WinError {ctypes.get_last_error()}). "
            "Check that the file exists and that its dependencies are present.")
    return handle


def get_proc(module_handle, name):
    """GetProcAddress; raises when the export is missing."""
    addr = _k32.GetProcAddress(ctypes.c_void_p(module_handle), ctypes.c_char_p(name.encode()))
    if not addr:
        raise DlssSrError(
            f"[ANTs] Export {name!r} not found in the loaded module - the DLL is "
            "not the runtime this host expects.")
    return addr


def free_library(module_handle):
    if module_handle:
        _k32.FreeLibrary(ctypes.c_void_p(module_handle))


def callable_at(address, argtypes, restype):
    """Turn a raw export address into a ctypes callable (the COM/FFI seam)."""
    proto = ctypes.CFUNCTYPE(restype, *argtypes)
    return proto(ctypes.c_void_p(address))


def create_event():
    _require_windows("CreateEvent")
    handle = _k32.CreateEventW(None, False, False, None)
    if not handle:
        raise DlssSrError("[ANTs] CreateEventW failed.")
    return handle


def wait_event(handle, timeout_ms=_INFINITE):
    code = _k32.WaitForSingleObject(ctypes.c_void_p(handle), ctypes.c_uint(timeout_ms))
    return code == _WAIT_OBJECT_0


def close_handle(handle):
    _k32.CloseHandle(ctypes.c_void_p(handle))


def wide(text):
    return (ctypes.c_wchar * (len(text) + 1))(text)


# D3D12CreateDevice / CreateDXGIFactory1 -------------------------------

def d3d12_create_device_symbol():
    _require_windows("D3D12CreateDevice")
    return _d3d12.D3D12CreateDevice


def create_dxgi_factory1_symbol():
    _require_windows("CreateDXGIFactory1")
    return _dxgi.CreateDXGIFactory1

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

if _is_windows:
    # Without an explicit restype ctypes truncates returns to 32-bit c_int -
    # fatal for HMODULE/HPROC/FARPROC (64-bit addresses). Simple ctypes
    # restypes hand back plain Python ints.
    _k32.LoadLibraryExW.restype = ctypes.c_void_p
    _k32.GetProcAddress.restype = ctypes.c_void_p
    _k32.CreateEventW.restype = ctypes.c_void_p

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
            f"[ANTs] Export {name!r} not found in the loaded module (WinError "
            f"{ctypes.get_last_error()}) - the DLL is not the runtime this host expects.")
    return addr


def get_module_filename(module_handle, buf_len=1024):
    """The image path Windows loaded for this module handle ('' if unknown)."""
    if not _is_windows or not module_handle:
        return ""
    buf = ctypes.create_unicode_buffer(buf_len)
    if not _k32.GetModuleFileNameW(ctypes.c_void_p(module_handle), buf, buf_len):
        return ""
    return buf.value


def export_address(module_handle, rva):
    """Export address by KNOWN RVA: HMODULE is the loaded image base, so
    base + RVA is the function even if GetProcAddress refuses to parse our
    export directory (the loader maps the image without walking it)."""
    if isinstance(module_handle, ctypes.c_void_p):
        module_handle = module_handle.value or 0
    return int(module_handle) + int(rva)


def free_library(module_handle):
    if module_handle:
        _k32.FreeLibrary(ctypes.c_void_p(module_handle))


def callable_at(address, argtypes, restype):
    """Turn a raw export address into a ctypes callable (the COM/FFI seam)."""
    # c_void_p is a simple type (NOT _Pointer) but carries .value too
    if isinstance(address, (ctypes.c_void_p, ctypes._Pointer)):
        address = address.value or 0
    proto = ctypes.CFUNCTYPE(restype, *argtypes)
    return proto(int(address))  # CFUNCTYPE wants the raw int, not a pointer object


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
    return ctypes.create_unicode_buffer(text)


# D3D12CreateDevice / CreateDXGIFactory1 -------------------------------

def d3d12_create_device_symbol():
    _require_windows("D3D12CreateDevice")
    return _d3d12.D3D12CreateDevice


def create_dxgi_factory1_symbol():
    _require_windows("CreateDXGIFactory1")
    return _dxgi.CreateDXGIFactory1

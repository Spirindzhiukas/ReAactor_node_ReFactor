"""A first-chance-exception black box for native NGX calls.

When the NGX runtime dies inside a native call, CPython vanishes with the
process: no traceback, no log, nothing. Windows DOES dispatch first-chance
exceptions to vectored handlers before any other handler runs, so we
register one from Python right before the first native evaluate. On a hard
crash it writes a one-line verdict ("exception 0xC0000005 (access
violation) at <module>+0x<offset>") both to stderr and to a pre-opened
file next to the other runtime artifacts, then lets the crash proceed
normally.

If a crash produces NO verdict line at all, the process died through a
path that bypasses ALL exception handling (fail-fast / forceful heap
termination) - which is itself a strong signal: deliberate host checks in
a dll use exactly that, while accidents almost always raise a catchable
exception first.

The handler only reports hardware-class exception codes (access violation,
illegal instruction, stack overflow, heap corruption, divide by zero);
benign first-chance C++ exceptions thrown and caught inside the runtime
are not our business. Emissions are capped; the LAST line before death is
the verdict.
"""
import ctypes
import os

_INTERESTING = {
    0xC0000005: "access violation",
    0xC000001D: "illegal instruction",
    0xC0000094: "integer divide by zero",
    0xC00000FD: "stack overflow",
    0xC0000374: "heap corruption",
}

_MAX_EMISSIONS = 50

_state = {"fd": None, "handler": None, "count": 0}


def _kernel32():
    try:
        return ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:  # non-Windows (tests, CI): the box simply never arms
        return None


def _module_for(k32, address):
    """Resolve an address to '<module path>+0x<offset>' (no refcount churn)."""
    handle = ctypes.c_void_p(0)
    FROM_ADDRESS = 0x4
    UNCHANGED_REFCOUNT = 0x2
    get = k32.GetModuleHandleExW
    get.argtypes = [ctypes.c_ulong, ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_void_p)]
    if get(FROM_ADDRESS | UNCHANGED_REFCOUNT,
           ctypes.c_void_p(address), ctypes.byref(handle)):
        buf = ctypes.create_unicode_buffer(1024)
        name = k32.GetModuleFileNameW
        name.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
        if name(handle, buf, 1024):
            return buf.value, address - (handle.value or 0)
    return None, 0


def _emit(text):
    raw = text.encode("utf-8", "replace")
    try:
        os.write(2, raw)
    except Exception:
        pass
    fd = _state["fd"]
    if fd is not None:
        try:
            os.write(fd, raw)
        except Exception:
            pass


def arm(crash_file_path):
    """Register the vectored handler and open the crash file. Idempotent."""
    k32 = _kernel32()
    if _state["handler"] is not None:
        return
    if k32 is None:
        return
    try:
        parent = os.path.dirname(str(crash_file_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        _state["fd"] = os.open(str(crash_file_path),
                               os.O_WRONLY | os.O_CREAT | os.O_APPEND
                               | getattr(os, "O_BINARY", 0))
    except Exception:
        _state["fd"] = None

    # LONG NTAPI VectoredHandler(PEXCEPTION_POINTERS): EXCEPTION_POINTERS is
    # { EXCEPTION_RECORD *record; CONTEXT *context; } and EXCEPTION_RECORD
    # starts with { DWORD code; DWORD flags; RECORD *chain; VOID *address; }.
    @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)
    def handler(exception_pointers):
        if _state["count"] < _MAX_EMISSIONS:
            try:
                record = ctypes.c_void_p.from_address(
                    exception_pointers or 0).value
                if record is not None:
                    code = ctypes.c_uint32.from_address(record).value
                    address = ctypes.c_void_p.from_address(record + 16).value
                    if code in _INTERESTING:
                        _state["count"] += 1
                        where = ""
                        if address:
                            module, offset = _module_for(k32, address)
                            if module:
                                where = f" at {module}+0x{offset:X}"
                            else:
                                where = f" at 0x{address:X} (unknown module)"
                        _emit(f"\n[ANTs] NATIVE CRASH: exception 0x{code:08X} "
                              f"({_INTERESTING[code]}){where}\n")
            except Exception:
                pass
        return 0  # EXCEPTION_CONTINUE_SEARCH - the crash proceeds normally

    _state["handler"] = handler
    add = k32.AddVectoredExceptionHandler
    add.restype = ctypes.c_void_p
    add.argtypes = [ctypes.c_ulong, ctypes.c_void_p]
    add(1, ctypes.cast(handler, ctypes.c_void_p))

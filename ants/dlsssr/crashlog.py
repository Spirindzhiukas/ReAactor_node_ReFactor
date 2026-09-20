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

# Deliberate-termination APIs. A death through any of these raises NO
# exception - the vectored handler never sees it (run 24's exact signature:
# snippet finishes its params callback, then the process just ends, crash
# file empty). Patching the NGX modules' import tables with log-first
# trampolines catches the killer and names its call site (module+offset,
# resolvable with tools/resolve_crash_offset.py), then chains to the real
# function so the behavior is otherwise unchanged.
_TERM_PROTOS = {
    "ExitProcess": (None, (ctypes.c_uint,)),
    "TerminateProcess": (ctypes.c_int, (ctypes.c_void_p, ctypes.c_uint)),
    "RaiseFailFastException": (
        ctypes.c_long, (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)),
    "NtTerminateProcess": (ctypes.c_long, (ctypes.c_void_p, ctypes.c_long)),
    "abort": (None, ()),
    "exit": (None, (ctypes.c_uint,)),
    "_exit": (None, (ctypes.c_uint,)),
    "terminate": (None, ()),
}

_MAX_EMISSIONS = 50

_state = {"fd": None, "handler": None, "count": 0,
          "trap": False, "trap_keep": []}


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


# --- deliberate-termination tracer ------------------------------------------
# Run 24 (fault-free dumper, return 0): the snippet printed its params
# callback dump and the process still ended - with NO exception anywhere
# and an empty crash file. Deliberate host-side kills use exactly the
# no-exception paths: CRT abort()/__fastfail, ExitProcess,
# TerminateProcess, RaiseFailFastException. This tracer rewrites the
# import address tables of the loaded NGX modules so any of those calls
# first logs WHO made it (stack frames resolved to module+offset), then
# forwards to the original function.

def _stack_chain(k32, skip=0, count=8):
    """Raw return-address chain, each frame named 'module+0x<offset>'."""
    try:
        ntdll = ctypes.WinDLL("ntdll")
        capture = ntdll.RtlCaptureStackBackTrace
    except Exception:  # non-Windows: never armed anyway
        return []
    capture.restype = ctypes.c_ushort
    capture.argtypes = [ctypes.c_ulong, ctypes.c_ulong,
                        ctypes.POINTER(ctypes.c_void_p),
                        ctypes.POINTER(ctypes.c_uint)]
    buf = (ctypes.c_void_p * 16)()
    n = capture(skip, min(count, 16), buf, None) or 0
    out = []
    for i in range(n):
        addr = buf[i] or 0
        module, offset = _module_for(k32, addr)
        if module:
            out.append(f"{os.path.basename(module)}+0x{offset:X}")
        else:
            out.append(f"0x{addr:X}")
    return out


def _write_iat_ptr(k32, addr, value):
    old = ctypes.c_uint32(0)
    protect = k32.VirtualProtect
    protect.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                        ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    if not protect(ctypes.c_void_p(addr), ctypes.c_size_t(8), 0x04,
                   ctypes.byref(old)):  # PAGE_READWRITE
        return False
    ctypes.c_uint64.from_address(addr).value = value
    protect(ctypes.c_void_p(addr), ctypes.c_size_t(8), old.value,
            ctypes.byref(old))
    return True


def _term_stub(k32, name, original, keep):
    """Log-first trampoline for one termination API; returns the raw
    function pointer to store in the IAT (trampoline pinned via `keep`)."""
    ret, args = _TERM_PROTOS[name]
    # WINFUNCTYPE everywhere real, CFUNCTYPE on non-Windows test hosts
    # (identical ABI on x64).
    proto = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(ret, *args)
    real = proto(original)
    keep.append(real)

    @proto
    def stub(*a):
        chain = _stack_chain(k32)
        _emit(f"\n[ANTs] TERMINATION via {name}; call chain: "
              + (" <- ".join(chain) or "unresolved") + "\n")
        return real(*a)

    keep.append(stub)
    return ctypes.cast(stub, ctypes.c_void_p).value


def _patch_iat(k32, base, keep):
    """Rewrite the termination-API thunks of one loaded image.

    In-memory PE walk (base = HMODULE; RVAs are direct offsets from the
    base in a mapped image). Returns (patched, wanted_seen): patched as
    'dll!name' strings, wanted_seen = which of _TERM_PROTOS the module
    imports at all (the static audit: absent names mean a statically
    linked CRT or inline syscall stubs)."""
    base = int(base)

    def u32(off):
        return ctypes.c_uint32.from_address(base + off).value

    def u64(off):
        return ctypes.c_uint64.from_address(base + off).value

    def cstr(rva):
        try:
            raw = ctypes.string_at(base + rva, 96)
        except Exception:
            return ""
        return raw.split(b"\x00", 1)[0].decode("ascii", "replace")

    e_lfanew = u32(0x3C)
    if ctypes.c_uint32.from_address(base + e_lfanew).value != 0x00004550:
        return [], set()
    imp_rva = u32(e_lfanew + 144)  # optional header +112 (data dirs), dir[1]
    if not imp_rva:
        return [], set()
    patched, wanted_seen = [], set()
    d = 0
    while d < 4096:  # import descriptor array, zero-terminated
        desc = imp_rva + d * 20
        oft, name_rva, ft = u32(desc), u32(desc + 12), u32(desc + 16)
        if not (oft or ft or name_rva):
            break
        dll = cstr(name_rva)
        i = 0
        while i < 65535:  # thunk array, zero-terminated
            t = u64((oft or ft) + i * 8)
            if not t:
                break
            if not t >> 63:  # by-name import (ordinals can't be matched)
                fname = cstr(t + 2)  # IMAGE_IMPORT_BY_NAME: hint + name
                if fname in _TERM_PROTOS:
                    wanted_seen.add(fname)
                    original = u64(ft + i * 8)  # loader-resolved target
                    if original:
                        ptr = _term_stub(k32, fname, original, keep)
                        if _write_iat_ptr(k32, base + ft + i * 8, ptr):
                            patched.append(f"{dll}!{fname}")
            i += 1
        d += 1
    return patched, wanted_seen


def install_termination_trap(module_targets):
    """Arm the tracer on every loaded NGX module. `module_targets` is a
    list of (module_handle, label). Idempotent; no-op off Windows. All
    output goes through _emit (stderr + the armed crash file)."""
    k32 = _kernel32()
    if k32 is None or _state["trap"]:
        return
    _state["trap"] = True
    all_seen = set()
    for base, label in module_targets:
        if not base:
            continue
        try:
            patched, wanted = _patch_iat(k32, base, _state["trap_keep"])
        except Exception as exc:
            _emit(f"[ANTs] termination trap: import walk failed for {label} "
                  f"(base 0x{int(base):X}): {exc}\n")
            continue
        all_seen |= wanted
        if patched:
            _emit(f"[ANTs] termination trap on {label}: patched "
                  + ", ".join(patched) + "\n")
    absent = sorted(set(_TERM_PROTOS) - all_seen)
    if absent:
        _emit("[ANTs] termination trap: no NGX module imports "
              + ", ".join(absent)
              + " - a silent kill would then be statically linked CRT "
                "abort/fastfail or an inline syscall.\n")

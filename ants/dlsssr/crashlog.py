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
import time

_INTERESTING = {
    0xC0000005: "access violation",
    0xC000001D: "illegal instruction",
    0xC0000094: "integer divide by zero",
    0xC00000FD: "stack overflow",
    0xC0000374: "heap corruption",
    # raised by the int29 trap's CD29->CC90 rewrites: the break address IS
    # the fast-fail site we could otherwise never see
    0x80000003: "breakpoint (patched fast-fail site)",
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

# MSVC C++ throw ("msc" + 3): RaiseException(0xE06D7363, ...) from
# _CxxThrowException. Run 30 (2026-09-20) surfaced one of these out of the
# snippet's EvaluateFeature - ctypes turned it into a Python OSError, and for
# the first time the failure was CATCHABLE instead of a silent process kill.
# The record carries everything needed to name it: the thrown object, and the
# ThrowInfo -> CatchableTypeArray -> TypeDescriptor chain whose tail holds the
# RTTI type name (e.g. ".?AVinvalid_argument@std@@"), plus the live stack.
_CXX_CODE = 0xE06D7363
_CXX_MAGIC = 0x19930520
_MAX_CXX = 20
_CXX_NOISE = ("ntdll", "kernelbase", "kernel32", "vcruntime", "ucrtbase",
              "python", "libffi", "_ctypes", "msvcp")

_state = {"fd": None, "handler": None, "count": 0, "cxx_count": 0,
          "cxx": [], "trap": False, "trap_keep": [], "path": None,
          "phase": ""}


def set_phase(text):
    """Name the call that is IN FLIGHT, in one word (the crash box prints it).

    The rig's TERMINATION lines so far named only libffi/_ctypes/python
    frames, so "something killed the process" could not be tied to a call.
    Every ctypes call into the engine/NGX sets this before it runs and
    restores it after, which makes the next kill line say WHERE it happened.
    """
    _state["phase"] = text or ""


def phase():
    """The current in-flight label (empty outside any ctypes call)."""
    return _state.get("phase") or ""


def _phase_note():
    current = phase()
    return f" [in-flight call: {current}]" if current else ""


def _kernel32():
    try:
        return ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:  # non-Windows (tests, CI): the box simply never arms
        return None


class _MBI(ctypes.Structure):
    """MEMORY_BASIC_INFORMATION (x64)."""

    _fields_ = [("BaseAddress", ctypes.c_void_p),
                ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", ctypes.c_uint32),
                ("_align", ctypes.c_uint32),
                ("RegionSize", ctypes.c_size_t),
                ("State", ctypes.c_uint32),
                ("Protect", ctypes.c_uint32),
                ("Type", ctypes.c_uint32)]


_MEM_COMMIT = 0x1000
_PAGE_GUARD = 0x100
# page protections we may READ through (PAGE_EXECUTE alone is NOT readable -
# reading it faults just like an unmapped page)
_READABLE_PAGES = (0x02,   # PAGE_READONLY
                   0x04,   # PAGE_READWRITE
                   0x08,   # PAGE_WRITECOPY
                   0x20,   # PAGE_EXECUTE_READ
                   0x40,   # PAGE_EXECUTE_READWRITE
                   0x80)   # PAGE_EXECUTE_WRITECOPY


class _Mem:
    """Fault-free reads over another module's in-memory image.

    A diagnostic must NEVER fault, and ``try/except`` does not buy that: an
    access violation raised inside a raw ctypes dereference or
    ``string_at`` is a HARD Windows exception, not a Python one. Run 28
    (2026-09-20) proved it the expensive way - the int29 scanner killed the
    whole ComfyUI process while reading a section header, before NGX was
    even initialized, so the run tested nothing.

    Every read therefore goes through VirtualQuery first and only touches
    committed, non-guard, readable pages. Refused addresses are recorded so
    the verdict can say what was skipped instead of dying.
    """

    def __init__(self, k32):
        self.k32 = k32
        self._mbi = _MBI()
        self.holes = []          # addresses we refused to touch
        try:
            k32.VirtualQuery.argtypes = [ctypes.c_void_p,
                                         ctypes.POINTER(_MBI),
                                         ctypes.c_size_t]
            k32.VirtualQuery.restype = ctypes.c_size_t
        except Exception:
            pass

    def region(self, address):
        """(region_end, protect) for the committed region holding `address`."""
        mbi = self._mbi
        # the struct is passed BY REFERENCE through the declared argtype;
        # that also lets tests hand in a python stand-in that fills it
        if not self.k32.VirtualQuery(ctypes.c_void_p(address),
                                     mbi, ctypes.sizeof(mbi)):
            return 0, 0
        if mbi.State != _MEM_COMMIT:
            return 0, 0
        if mbi.Protect & _PAGE_GUARD:                  # guard page: refuse
            return 0, 0
        if (mbi.Protect & 0xFF) not in _READABLE_PAGES:
            return 0, 0                                # NOACCESS / EXECUTE-only
        return (mbi.BaseAddress or 0) + mbi.RegionSize, mbi.Protect

    def read(self, address, size):
        """Bytes at `address`, or None when any part is not readable."""
        out = bytearray()
        at = int(address)
        while len(out) < size:
            end, _ = self.region(at)
            if not end or end <= at:
                self.holes.append(at)
                return None
            take = min(end - at, size - len(out))
            out += ctypes.string_at(at, take)
            at += take
        return bytes(out)

    def read_some(self, address, size):
        """Whatever is readable from `address`, up to `size` bytes.

        For C strings whose length is unknown: stopping at a region
        boundary is fine (the NUL is normally well before it), while an
        unmapped address simply yields nothing."""
        address = int(address)
        end, _ = self.region(address)
        if not end or end <= address:
            self.holes.append(address)
            return b""
        take = min(size, end - address)
        return ctypes.string_at(address, take) if take > 0 else b""

    def u16(self, address):
        data = self.read(address, 2)
        return None if data is None else int.from_bytes(data, "little")

    def u32(self, address):
        data = self.read(address, 4)
        return None if data is None else int.from_bytes(data, "little")

    def u64(self, address):
        data = self.read(address, 8)
        return None if data is None else int.from_bytes(data, "little")


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


def _emit_session_header():
    """`=== ANTs crash black box: session <time> pid <pid> build <..> ===`"""
    build = "?"
    try:
        from .ngx import HOST_BUILD as build     # noqa: F401 (lazy: import cycle)
    except Exception:
        pass
    _emit("\n=== ANTs crash black box: session "
          + time.strftime("%Y-%m-%d %H:%M:%S")
          + f" pid {os.getpid()} build {build} ===\n")


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
    """Register the vectored handler and open the append-only crash file.

    A session header is written every time we arm, because the file is
    APPEND-ONLY and outlives the session: run 30's report showed two stale
    ``IsBadReadPtr`` lines (the retired callback dumper of run 23) sitting
    above a fresh termination pair, and there was no way to tell them apart
    from the content alone. Now the newest header marks the newest session,
    and the report reader only trusts what follows it.
    """
    k32 = _kernel32()
    if _state["handler"] is not None:
        _emit_session_header()
        return
    if k32 is None:
        return
    _state["path"] = str(crash_file_path)
    try:
        parent = os.path.dirname(str(crash_file_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        _state["fd"] = os.open(str(crash_file_path),
                               os.O_WRONLY | os.O_CREAT | os.O_APPEND
                               | getattr(os, "O_BINARY", 0))
    except Exception:
        _state["fd"] = None

    _emit_session_header()

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
                    if code == _CXX_CODE:
                        _report_cxx(k32, record)
                    elif code in _INTERESTING:
                        # Hardware faults get the caller chain too: run 30's
                        # crash file shows an access violation INSIDE
                        # KERNEL32 (a faulting GetProcAddress-descended
                        # dereference), and the chain is what says whether
                        # the caller was our ctypes frame or the runtime's
                        # own code.
                        _state["count"] += 1
                        where = ""
                        if address:
                            module, offset = _module_for(k32, address)
                            if module:
                                where = f" at {module}+0x{offset:X}"
                            else:
                                where = f" at 0x{address:X} (unknown module)"
                        chain = ""
                        if _state["count"] <= 8:
                            try:
                                frames = _caller_chain(k32, 16, 8)
                                if frames:
                                    chain = " from " + " <- ".join(frames)
                            except Exception:
                                chain = ""
                        _emit(f"\n[ANTs] NATIVE CRASH: exception 0x{code:08X} "
                              f"({_INTERESTING[code]}){where}{_phase_note()}"
                              f"{chain}\n")
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


def _cxx_type_and_message(mem, thrown, throw_info):
    """(RTTI type name, best-effort message) for an MSVC throw record.

    x64 chain: ThrowInfo{attributes, pUnwind, pForwardCompat,
    pCatchableTypeArray} -> CatchableTypeArray{count, ptrs} ->
    CatchableType{properties, pType} -> TypeDescriptor{vftable, spare,
    name[]}. Every hop is a guarded read: a diagnostic that faults is worse
    than no diagnostic (that is the run-28 lesson), and none of this memory
    belongs to us.
    """
    type_name = None
    if throw_info:
        catchable = mem.u64(throw_info + 24)          # pCatchableTypeArray
        if catchable:
            count = mem.u32(catchable)
            first = mem.u64(catchable + 8) if count else None
            descriptor = mem.u64(first + 8) if first else None   # pType
            if descriptor:
                raw = mem.read_some(descriptor + 16, 240)
                text = raw.split(b"\x00", 1)[0].decode("ascii", "replace")
                if text.startswith(".?"):
                    type_name = text
    message = None
    if thrown and type_name:
        # std::exception-derived objects keep {void* vfptr; _Data{ptr,len}}:
        # the second qword normally points at the what() text. It is a guess,
        # so it is reported as one.
        ptr = mem.u64(thrown + 8)
        if ptr:
            raw = mem.read_some(ptr, 256)
            text = raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
            if text and text.isprintable():
                message = text
    return type_name, message


def _report_cxx(k32, record):
    """Log one MSVC C++ throw: type, guess at the message, live stack."""
    if os.environ.get("ANTS_NR_CXX_TRAP", "1") == "0":
        return
    if _state["cxx_count"] >= _MAX_CXX:
        return
    if ctypes.sizeof(ctypes.c_void_p) != 8:      # x64 record offsets
        return
    mem = _Mem(k32)
    try:
        count = ctypes.c_uint32.from_address(record + 24).value
        info = [ctypes.c_void_p.from_address(record + 32 + 8 * i).value or 0
                for i in range(min(count, 4))]
    except Exception:
        return
    if not info:
        return
    type_name, message = (None, None)
    try:
        type_name, message = _cxx_type_and_message(
            mem, info[1] if len(info) > 1 else 0,
            info[2] if len(info) > 2 else 0)
    except Exception:
        pass
    try:
        frames = [f for f in _stack_chain(k32, 0, 16)
                  if not any(noise in f.lower() for noise in _CXX_NOISE)]
        frames = frames[:6] or _stack_chain(k32, 0, 6)
    except Exception:
        frames = []
    # The object's first bytes sometimes carry a code or an inline string;
    # a bounded hex peek costs nothing and has already paid for itself on
    # other rig artefacts.
    peek = ""
    if len(info) > 1 and info[1]:
        raw = mem.read_some(info[1], 32)
        if raw:
            peek = " object " + raw[:32].hex()
    _state["cxx_count"] += 1
    text = (f"[ANTs] C++ exception 0x{_CXX_CODE:08X} (magic 0x{info[0]:X})"
            f"{_phase_note()} "
            f"type {type_name or '<?> (type descriptor unreadable)'}"
            + (f" message guess {message!r}" if message else "")
            + peek
            + (f" thrown from {' <- '.join(frames)}" if frames else ""))
    _state["cxx"].append(text)
    _emit("\n" + text + "\n")


def last_cxx_report():
    """The most recent C++ throw we saw, for a Python-side error message."""
    return _state["cxx"][-1] if _state["cxx"] else None


def crash_file_path():
    """Where the black box writes (once armed), for the error message."""
    return _state["path"]


def _write_iat_ptr(k32, addr, value, mem=None):
    """Point one IAT slot at a trampoline. The slot address comes out of the
    module's own import table, so verify it is committed AND readable before
    asking for write access - VirtualProtect fails on an unmapped address
    anyway, but a guard keeps the intent explicit (a diagnostic never writes
    anywhere it has not verified)."""
    if mem is None:
        mem = _Mem(k32)
    if mem.read(addr, 8) is None:
        return False
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


def _caller_chain(k32, count=16, limit=8):
    """`_stack_chain` with the FFI/interpreter frames removed.

    A termination or crash chain taken inside a ctypes call is dominated by
    libffi's internal frames: run 30's chain was eight libffi/_ctypes/python
    frames and nothing else, which hid the native code that actually called
    it. Dropping those leaves the frames that answer the question.
    """
    frames = _stack_chain(k32, 0, count)
    filtered = [f for f in frames
                if not any(noise in f.lower() for noise in _CXX_NOISE)]
    return (filtered or frames)[:limit]


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
        chain = _caller_chain(k32)
        _emit(f"\n[ANTs] TERMINATION via {name}{_phase_note()}; call chain: "
              + (" <- ".join(chain) or "unresolved") + "\n")
        if os.environ.get("ANTS_NR_BLOCK_TERMINATION") == "1":
            # A/B knob (opt-in): do not let the runtime kill the process -
            # return to its caller instead, so the node can fail LOUDLY and
            # ComfyUI survives to print the reason. `abort`/fast-fail are
            # never blocked (the CRT does not expect them to return).
            if name not in ("abort", "RaiseFailFastException"):
                _emit(f"[ANTs] ANTS_NR_BLOCK_TERMINATION=1: {name} did NOT "
                      "terminate the process; the caller resumes (state after "
                      "this point is undefined - send this line)\n")
                return None if ret is None else ret()   # 0 = "success"
        return real(*a)

    keep.append(stub)
    return ctypes.cast(stub, ctypes.c_void_p).value


def _patch_iat(k32, base, keep, mem=None):
    """Rewrite the termination-API thunks of one loaded image.

    In-memory PE walk (base = HMODULE; RVAs are direct offsets from the
    base in a mapped image). Every read goes through the VirtualQuery
    guard, so a malformed or partially mapped image is skipped instead of
    faulting (see _Mem). Returns (patched, wanted_seen): patched as
    'dll!name' strings, wanted_seen = which of _TERM_PROTOS the module
    imports at all (the static audit: absent names mean a statically
    linked CRT or inline syscall stubs)."""
    base = int(base)
    if mem is None:
        mem = _Mem(k32)

    def u32(off):
        return mem.u32(base + off)

    def u64(off):
        return mem.u64(base + off)

    def cstr(rva):
        raw = mem.read(base + rva, 96)
        if raw is None:
            return ""
        return raw.split(b"\x00", 1)[0].decode("ascii", "replace")

    e_lfanew = u32(0x3C)
    if e_lfanew is None or u32(e_lfanew) != 0x00004550:
        return [], set()
    imp_rva = u32(e_lfanew + 144)  # optional header +112 (data dirs), dir[1]
    if not imp_rva:
        return [], set()
    patched, wanted_seen = [], set()
    d = 0
    while d < 4096:  # import descriptor array, zero-terminated
        desc = imp_rva + d * 20
        oft, name_rva, ft = u32(desc), u32(desc + 12), u32(desc + 16)
        if oft is None or name_rva is None or ft is None:
            break
        if not (oft or ft or name_rva):
            break
        dll = cstr(name_rva)
        i = 0
        while i < 65535:  # thunk array, zero-terminated
            t = u64((oft or ft) + i * 8)
            if t is None or not t:
                break
            if not t >> 63:  # by-name import (ordinals can't be matched)
                fname = cstr(t + 2)  # IMAGE_IMPORT_BY_NAME: hint + name
                if fname in _TERM_PROTOS:
                    wanted_seen.add(fname)
                    original = u64(ft + i * 8)  # loader-resolved target
                    if original:
                        ptr = _term_stub(k32, fname, original, keep)
                        if _write_iat_ptr(k32, base + ft + i * 8, ptr, mem):
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
    mem = _Mem(k32)
    all_seen = set()
    for base, label in module_targets:
        if not base:
            continue
        try:
            patched, wanted = _patch_iat(k32, base, _state["trap_keep"], mem)
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


def install_ntdll_terminate_detour():
    """Final discriminator (shipped after run 26): both NGX modules' import
    traps were armed and verified, yet the silent death called NEITHER
    TerminateProcess NOR ExitProcess. Either an unpatched module kills us,
    or the kill bypasses imports (static CRT abort -> __fastfail/int 29h,
    or a raw syscall). Every self-termination in the process funnels into
    ntdll!NtTerminateProcess - so detour THAT function: verify the standard
    Win10/11 syscall-stub prologue, steal it verbatim into an executable
    buffer, overwrite the entry with 'mov rax, <trampoline>; jmp rax'. The
    trampoline logs the call chain, then executes the stolen stub (which
    performs the real syscall). Never restored - diagnostic-grade by
    design; skipped loudly on any unexpected prologue."""
    k32 = _kernel32()
    if k32 is None or _state.get("ntdll_detoured"):
        return
    _state["ntdll_detoured"] = True
    try:
        k32.GetProcAddress.restype = ctypes.c_void_p  # 64-bit addresses
        ntdll = ctypes.WinDLL("ntdll")
        addr = k32.GetProcAddress(ctypes.c_void_p(ntdll._handle),
                                  b"NtTerminateProcess")
        if not addr:
            _emit("[ANTs] ntdll detour: NtTerminateProcess not found\n")
            return
        addr = int(addr)
        # Win10/11 stub: mov r10,rcx (4C 8B D1); mov eax,<ssn> (B8 ...);
        # optionally build-specific check bytes; syscall (0F 05); ret (C3).
        # Steal whole instructions up to and including the syscall - the
        # checks between mov-eax and syscall are rsp/flag-relative and copy
        # verbatim - and ensure the stolen block ends with a ret so the
        # kernel's return comes back to our trampoline.
        raw = _Mem(k32).read(addr, 32)
        if raw is None:
            _emit("[ANTs] ntdll detour SKIPPED: the NtTerminateProcess page "
                  "is not readable\n")
            return
        if raw[:3] != b"\x4c\x8b\xd1" or raw[3] != 0xB8:
            _emit("[ANTs] ntdll detour SKIPPED: prologue is not 'mov r10,rcx; "
                  f"mov eax,<ssn>' ({raw[:32].hex()}) - report this line\n")
            return
        syscall = raw.find(b"\x0f\x05")
        if not (8 <= syscall <= 24):
            _emit("[ANTs] ntdll detour SKIPPED: no syscall instruction "
                  f"within reach ({raw[:32].hex()}) - obfuscated stub build; "
                  "report this line\n")
            return
        stolen = raw[:syscall + 2] + b"\xc3"
        k32.VirtualAlloc.restype = ctypes.c_void_p
        k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                                     ctypes.c_uint32, ctypes.c_uint32]
        buf = k32.VirtualAlloc(None, 64, 0x3000, 0x40)  # RWX, never freed
        if not buf:
            _emit("[ANTs] ntdll detour SKIPPED: VirtualAlloc failed\n")
            return
        buf = int(buf)
        ctypes.memmove(buf, stolen, len(stolen))
        proto = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
            ctypes.c_long, ctypes.c_void_p, ctypes.c_long)
        stolen_fn = proto(buf)

        def detour(handle, status):
            chain = _caller_chain(k32)
            _emit("\n[ANTs] TERMINATION via ntdll!NtTerminateProcess"
                  f"(handle=0x{int(handle or 0) & 0xFFFFFFFFFFFFFFFF:X}, "
                  f"status=0x{status & 0xFFFFFFFF:08X}){_phase_note()}; "
                  "call chain: "
                  + (" <- ".join(chain) or "unresolved") + "\n")
            return stolen_fn(handle, status)

        detour_cb = proto(detour)
        keep = _state["trap_keep"]
        keep.append(detour_cb)
        keep.append(stolen_fn)
        keep.append(buf)
        target = ctypes.cast(detour_cb, ctypes.c_void_p).value
        patch = (b"\x48\xb8" + int(target).to_bytes(8, "little")
                 + b"\xff\xe0")  # mov rax, trampoline; jmp rax
        old = ctypes.c_uint32(0)
        protect = k32.VirtualProtect
        protect.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                            ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
        if not protect(ctypes.c_void_p(addr), ctypes.c_size_t(len(patch)),
                       0x40, ctypes.byref(old)):  # PAGE_EXECUTE_READWRITE
            _emit("[ANTs] ntdll detour SKIPPED: VirtualProtect denied\n")
            return
        ctypes.memmove(addr, patch, len(patch))
        protect(ctypes.c_void_p(addr), ctypes.c_size_t(len(patch)),
                old.value, ctypes.byref(old))
        _emit(f"[ANTs] ntdll detour ARMED on NtTerminateProcess (stolen "
              f"{len(stolen)}-byte syscall stub; a TERMINATION line right at "
              "ComfyUI shutdown is normal exit traffic, not a kill)\n")
    except Exception as exc:
        _emit(f"[ANTs] ntdll detour failed (diagnostic only): {exc}\n")


def _rewrite_site(mem, k32, site):
    """CD 29 -> CC 90 at one verified fast-fail site."""
    if mem.read(site, 2) != b"\xcd\x29":
        return False
    old = ctypes.c_uint32(0)
    try:
        protect = k32.VirtualProtect
        protect.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                            ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
        if not protect(ctypes.c_void_p(site), ctypes.c_size_t(2), 0x40,
                       ctypes.byref(old)):
            return False
    except Exception:
        return False
    ctypes.c_uint8.from_address(site).value = 0xCC
    ctypes.c_uint8.from_address(site + 1).value = 0x90
    try:
        protect(ctypes.c_void_p(site), ctypes.c_size_t(2), old.value,
                ctypes.byref(old))
    except Exception:
        pass
    return True


def _patch_section(mem, k32, start, size):
    """Rewrite every fast-fail site in one executable section.

    A section's VirtualSize is a CLAIM, not a promise: the loader can leave
    parts of it unmapped (discardable sections, a VirtualSize larger than
    what was committed), and reading one of those pages is exactly what
    killed the run-28 attempt (`string_at` on an unmapped page, repeated
    forever by whatever handles the AV). So the scan reads ONLY through the
    guard, takes exactly the bytes VirtualQuery vouches for, and steps over
    a hole in page-sized strides - readable bytes are never skipped, and an
    unmapped page is never touched.
    """
    chunk_limit = 64 << 10
    patched = 0
    done = 0
    while done < size:
        n = min(chunk_limit, size - done)
        chunk = mem.read_some(start + done, n)
        if not chunk:
            done += 0x1000      # unmapped page: skip it, never touch it
            continue
        at = 0
        while True:
            at = chunk.find(b"\xcd\x29", at)
            if at < 0:
                break
            if _rewrite_site(mem, k32, start + done + at):
                patched += 1
            at += 2
        done += len(chunk)
    return patched


def _scan_int29_sites(mem, k32, base, label):
    """One module: validate the headers, then walk its executable sections.

    Returns (patched, note). NEVER dereferences anything the guard has not
    verified: a module that is not a plain mapped image is skipped with a
    reason instead of killing the process (run 28's exact failure mode)."""
    header = mem.read(base, 0x40)
    if header is None:
        return 0, "header page not readable"
    if header[:2] != b"MZ":
        return 0, "no MZ signature"
    e_lfanew = int.from_bytes(header[0x3C:0x40], "little")
    if not 0x40 <= e_lfanew <= 0x1000:
        return 0, f"implausible e_lfanew 0x{e_lfanew:X}"
    head = mem.read(base + e_lfanew, 24)
    if head is None or head[:4] != b"PE\x00\x00":
        return 0, "no PE signature"
    n_sec = int.from_bytes(head[6:8], "little")
    size_opt = int.from_bytes(head[20:22], "little")
    if not 1 <= n_sec <= 96:
        return 0, f"implausible section count {n_sec}"
    if size_opt > 0x400:
        return 0, f"implausible optional-header size {size_opt}"
    table = mem.read(base + e_lfanew + 24 + size_opt, n_sec * 40)
    if table is None:
        return 0, "section table outside the mapped headers"
    patched = 0
    scanned = 0
    for index in range(n_sec):
        entry = table[index * 40:(index + 1) * 40]
        vsize = int.from_bytes(entry[8:12], "little")
        vaddr = int.from_bytes(entry[12:16], "little")
        chars = int.from_bytes(entry[36:40], "little")
        if not vsize or not chars & 0x20000000:      # not executable
            continue
        scanned += 1
        patched += _patch_section(mem, k32, base + vaddr, vsize)
    note = f"{scanned} executable section(s)"
    if mem.holes:
        note += (f"; refused {len(mem.holes)} unreadable page(s), first at "
                 f"0x{mem.holes[0]:X}")
        del mem.holes[:]
    return patched, note


def install_int29_trap(module_targets, k32=None):
    """Convert the runtime's fast-fail instructions into breakpoints.

    Runs 14-27c terminal diagnosis: the deliberate kill survives the
    patched IATs AND the ntdll detour => it is kernel-direct - CRT abort
    compiles to __fastfail = 'int 29h' (opcode CD 29), which bypasses
    vectored handlers, SEH, and every ntdll function BY DESIGN. The only
    way to see the site is to rewrite CD 29 -> CC 90 (int3 + nop) in the
    executable sections: the break raises STATUS_BREAKPOINT, which the
    vectored handler DOES see, and the crash verdict then names the exact
    module+offset of the fast-fail. Diagnostic-grade; the process was
    dying at that instruction anyway - a logged site is strictly better.

    Every read is VirtualQuery-verified (_Mem). This runs at SESSION INIT,
    before NGX even initializes, so a fault here destroys a whole
    experiment - run 28 (2026-09-20) died exactly that way. The hardening
    is pinned by tests over a synthetic PE plus hostile (unmapped /
    absurd-header) fixtures.
    """
    if k32 is None:
        k32 = _kernel32()
    if k32 is None or _state.get("int29_done"):
        return
    _state["int29_done"] = True
    mem = _Mem(k32)
    targets = [(int(base), label) for base, label in module_targets if base]
    _emit(f"[ANTs] int29 trap: scanning {len(targets)} module(s) for "
          "kernel-direct fast-fail sites (CD 29)\n")
    for base, label in targets:
        try:
            patched, note = _scan_int29_sites(mem, k32, base, label)
        except Exception as exc:      # a diagnostic never kills the run
            _emit(f"[ANTs] int29 trap: {label} scan skipped ({exc})\n")
            continue
        if patched:
            _emit(f"[ANTs] int29 trap: {patched} fast-fail site(s) "
                  f"converted to breakpoints in {label} ({note}) - the next "
                  "fast-fail logs its exact address instead of dying "
                  "silently\n")
        else:
            _emit(f"[ANTs] int29 trap: {label} has no fast-fail site "
                  f"({note})\n")

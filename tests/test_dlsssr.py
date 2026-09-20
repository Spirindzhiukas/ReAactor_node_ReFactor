"""Tests for the pure-Python DLSS host (sandbox-safe parts only).

The D3D12/NGX call paths need Windows + a GPU; everything else is verified
here: shim PE structure (pefile), our parameter object semantics, NGX
constants, discovery, and the frame converters.

Usage: python tests/test_dlsssr.py
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

from smoke_import import install_stubs  # noqa: E402

PASS = 0
FAIL = 0


def raw_exec_lines(bat_bytes):
    """Executable (non-rem) lines of a CRLF bat, for paren-block checks."""
    for ln in bat_bytes.split(b"\r\n"):
        s = ln.strip()
        if s and not s.lower().startswith(b"rem"):
            yield s


def _fake_kernel32(regions, protect=0x04):
    """A fake kernel32 for the crashlog diagnostics.

    ``VirtualQuery`` reports each (base, size) in ``regions`` as committed
    and readable - anything else reads as unmapped, exactly the condition
    that killed run 28. ``VirtualProtect`` succeeds as a no-op.
    """
    import ctypes

    class _K:
        def __init__(self):
            self.regions = regions

        def VirtualQuery(self, address, mbi, length):
            addr = address.value if hasattr(address, "value") else int(address)
            for base, size in self.regions:
                if base <= addr < base + size:
                    # like the real API: the region's own base and FULL size
                    # (not the remainder from the queried address)
                    mbi.BaseAddress = base
                    mbi.AllocationBase = base
                    mbi.RegionSize = size
                    mbi.State = 0x1000                      # MEM_COMMIT
                    mbi.Protect = protect
                    return ctypes.sizeof(mbi)
            return 0

        # a staticmethod so the diagnostics can set .argtypes/.restype on it
        # exactly like they do on the real kernel32 export
        VirtualProtect = staticmethod(lambda *args: 1)

    return _K()


def _iat_tracer_fixture_check():
    """Build a flat (identity-mapped) PE whose only import is KERNEL32!
    {ExitProcess, abort}, run crashlog._patch_iat over it with a fake
    kernel32, and verify the walker finds both thunks, records them as
    seen, and rewrites each IAT slot to a fresh pinned trampoline."""
    import ctypes
    import struct

    from ants.dlsssr import crashlog

    buf = bytearray(0x2000)

    def put(off, data):
        buf[off:off + len(data)] = data

    def w32(off, v):
        put(off, struct.pack("<I", v))

    def w64(off, v):
        put(off, struct.pack("<Q", v))

    w32(0x3C, 0x80)
    put(0x80, b"PE\x00\x00")
    w32(0x80 + 144, 0x400)          # import directory RVA (dir[1])
    w32(0x400 + 0, 0x500)           # OriginalFirstThunk
    w32(0x400 + 12, 0x4C0)          # dll name
    w32(0x400 + 16, 0x5A0)          # FirstThunk (IAT)
    put(0x4C0, b"KERNEL32.dll\x00")
    names = ["ExitProcess", "abort"]
    slot = 0x540
    for k, n in enumerate(names):
        w64(0x500 + k * 8, slot)    # INT entry -> hint/name
        w64(0x5A0 + k * 8, 0xDEADBEEF + k)  # loader-'resolved' original
        put(slot, struct.pack("<H", 0) + n.encode() + b"\x00")
        slot += 2 + len(n) + 1
    w64(0x500 + len(names) * 8, 0)  # terminator
    w64(0x5A0 + len(names) * 8, 0)

    image = ctypes.create_string_buffer(bytes(buf), len(buf))
    base = ctypes.addressof(image)
    keep = []
    fake_k32 = _fake_kernel32([(base, len(buf))])
    patched, wanted = crashlog._patch_iat(fake_k32, base, keep)
    slots = [ctypes.c_uint64.from_address(
        base + 0x5A0 + i * 8).value for i in range(len(names))]
    return (patched == [f"KERNEL32.dll!{n}" for n in names]
            and wanted == set(names)
            and all(slots[i] != 0xDEADBEEF + i for i in range(len(names)))
            and len(keep) >= 2 * len(names))


def _synthetic_module(exec_sites=1, non_exec_site=False, n_sec=None,
                      opt_size=0xF0, e_lfanew=0x80, table_overflow=False,
                      exec_vsize=0x100):
    """A 3-section PE (one executable) in a real in-process buffer.

    Returns (buffer, base, exec_rva) so tests can assert on the bytes.
    ``table_overflow`` claims more sections than fit before the buffer
    ends - the exact shape that walked a scanner off the mapped page on
    the rig."""
    import ctypes
    import struct

    exec_rva, data_rva = 0x1000, 0x2000
    buf = bytearray(0x1000 + 2 * 0x1000)          # headers + 2 sections
    buf[0:2] = b"MZ"
    buf[0x3C:0x40] = struct.pack("<I", e_lfanew)
    buf[e_lfanew:e_lfanew + 4] = b"PE\x00\x00"
    count = n_sec if n_sec is not None else 3
    buf[e_lfanew + 6:e_lfanew + 8] = struct.pack("<H", count)
    buf[e_lfanew + 20:e_lfanew + 22] = struct.pack("<H", opt_size)
    sec0 = e_lfanew + 24 + opt_size
    entries = [(".text\x00\x00\x00", exec_vsize, exec_rva, 0x60000020),
               (".data\x00\x00\x00", 0x100, data_rva, 0xC0000040),
               (".rdata\x00\x00", 0x100, 0x3000, 0x40000040)]
    for index, (name, vsize, vaddr, chars) in enumerate(entries):
        at = sec0 + index * 40
        if at + 40 > len(buf):
            break
        buf[at:at + 8] = name.encode("latin1")
        buf[at + 8:at + 12] = struct.pack("<I", vsize)
        buf[at + 12:at + 16] = struct.pack("<I", vaddr)
        buf[at + 36:at + 40] = struct.pack("<I", chars)
    for k in range(exec_sites):   # contiguous: "CD 29 CD 29"
        buf[exec_rva + 0x10 + k * 2:exec_rva + 0x12 + k * 2] = b"\xcd\x29"
    if non_exec_site:
        buf[data_rva + 0x20:data_rva + 0x22] = b"\xcd\x29"
    if table_overflow:
        # 96 sections starting at 0x1A0 => the table ends at 0x10A0, i.e.
        # past the first mapped page (the caller maps only 0x1000 bytes)
        buf[e_lfanew + 6:e_lfanew + 8] = struct.pack("<H", 96)
    image = ctypes.create_string_buffer(bytes(buf), len(buf))
    return image, ctypes.addressof(image), exec_rva


def _int29_scanner_fixture_check():
    """The CD29->CC90 rewriter on a synthetic PE: converts fast-fail sites
    in EXECUTABLE sections only, and reports what it did."""
    import ctypes

    from ants.dlsssr import crashlog

    image, base, exec_rva = _synthetic_module(exec_sites=2,
                                              non_exec_site=True)
    fake_k32 = _fake_kernel32([(base, len(image))])
    lines = []
    saved_emit = crashlog._emit
    crashlog._emit = lines.append
    crashlog._state["int29_done"] = False
    try:
        crashlog.install_int29_trap([(base, "fixture.dll")], k32=fake_k32)
    finally:
        crashlog._emit = saved_emit
        crashlog._state["int29_done"] = False
    text = "".join(lines)
    exec_bytes = bytes(ctypes.string_at(base + exec_rva + 0x10, 4))
    non_exec = bytes(ctypes.string_at(base + 0x2000 + 0x20, 2))
    return (exec_bytes == b"\xcc\x90\xcc\x90"          # both converted
            and non_exec == b"\xcd\x29"                  # data section untouched
            and "2 fast-fail site(s) converted to breakpoints in "
                "fixture.dll" in text)


def _int29_scanner_safety_check():
    """HOSTILE fixtures: unmapped base, absurd headers, a section table that
    runs off the mapped image, and an executable section nobody mapped.
    Run 28's scanner KILLED the process on the last of these - every case
    must now be skipped with a logged reason."""
    from ants.dlsssr import crashlog

    results = []
    lines = []
    saved_emit = crashlog._emit
    crashlog._emit = lines.append
    try:
        # (a) a base the guard reports as unmapped
        crashlog._state["int29_done"] = False
        empty_k32 = _fake_kernel32([])
        crashlog.install_int29_trap([(0x7FFF00000000, "ghost.dll")],
                                    k32=empty_k32)
        results.append("not readable" in "".join(lines))
        # (b) absurd section count
        lines.clear()
        image, base, _ = _synthetic_module(exec_sites=1, n_sec=0xFFFF)
        crashlog._state["int29_done"] = False
        crashlog.install_int29_trap([(base, "absurd.dll")],
                                    k32=_fake_kernel32([(base, len(image))]))
        results.append("implausible section count" in "".join(lines))
        # (c) the section table runs past the end of the mapped image
        lines.clear()
        image, base, _ = _synthetic_module(exec_sites=1,
                                           table_overflow=True)
        crashlog._state["int29_done"] = False
        # only the header page is mapped; the claimed section table is not
        crashlog.install_int29_trap([(base, "overflow.dll")],
                                    k32=_fake_kernel32([(base, 0x1000)]))
        results.append("section table outside the mapped headers"
                       in "".join(lines))
        # (d) an executable section that no page backs
        import ctypes
        import struct
        lines.clear()
        image, base, _ = _synthetic_module(exec_sites=1)
        ctypes.c_uint32.from_address(base + 0x80 + 24 + 0xF0 + 12).value = \
            0x900000        # section 0's VirtualAddress, far off the image
        crashlog._state["int29_done"] = False
        crashlog.install_int29_trap([(base, "wild.dll")],
                                    k32=_fake_kernel32([(base, len(image))]))
        text = "".join(lines)
        results.append("no fast-fail site" in text
                       and "unreadable page" in text)
        struct  # imported for symmetry with the other fixtures
    finally:
        crashlog._emit = saved_emit
        crashlog._state["int29_done"] = False
    return all(results)


def _nr_staging_rule_check():
    """Run 28's confound: a folder holding BOTH the selected build and a
    same-named sibling used to load the SIBLING. The selected file must be
    the one canonicalized, and an already-canonical selection is used in
    place (nothing copied)."""
    import tempfile

    from ants.dlssnr import discovery as nr_discovery

    folder = Path(tempfile.mkdtemp(prefix="ants_nr_folder_"))
    selected = folder / "nvngx_dlssnr_RenoDX_4000_series_friendly.dll"
    sibling = folder / "nvngx_dlssnr.dll"
    selected.write_bytes(b"MZ" + b"selected-build" * 16)
    sibling.write_bytes(b"MZ" + b"sibling" * 8)
    stage = Path(nr_discovery.stage_nr_runtime(str(selected)))
    staged = stage / "nvngx_dlssnr.dll"
    ok = (stage != folder
          and staged.read_bytes() == selected.read_bytes()
          and staged.read_bytes() != sibling.read_bytes())
    # the already-canonical selection stays exactly where it lives
    ok = ok and Path(nr_discovery.stage_nr_runtime(str(sibling))) == folder
    return ok


def _mem_guard_check():
    """The guard must refuse what VirtualQuery cannot vouch for: unmapped,
    NOACCESS, GUARD and EXECUTE-only pages (PAGE_EXECUTE is not readable -
    reading it faults exactly like an unmapped page)."""
    import ctypes

    from ants.dlsssr import crashlog

    buf = ctypes.create_string_buffer(b"\xcd\x29" + b"x" * 62)
    base = ctypes.addressof(buf)
    expectations = ((0x04, True), (0x20, True), (0x02, True), (0x40, True),
                    (0x10, False), (0x01, False), (0x104, False), (0x00, False))
    ok = True
    for protect, readable in expectations:
        mem = crashlog._Mem(_fake_kernel32([(base, 64)], protect=protect))
        ok = ok and (mem.read(base, 8) is not None) == readable
    # a read spanning the end of the mapped region is refused, not clipped
    mem = crashlog._Mem(_fake_kernel32([(base, 64)]))
    ok = ok and mem.read(base + 60, 8) is None and mem.read(base + 56, 8) == \
        bytes(ctypes.string_at(base + 56, 8))
    return ok


def _own_params_name_guard_check():
    """The own-parameter object decodes parameter names the RUNTIME passed
    us: an unreadable pointer must come back as "" (VirtualQuery-guarded)
    instead of faulting inside a ctypes call."""
    import ctypes

    from ants.dlsssr import crashlog, parameters

    buf = ctypes.create_string_buffer(b"DLSSNR.Width\x00extra")
    good = parameters._read_cstring(ctypes.addressof(buf))
    saved = crashlog._kernel32
    crashlog._kernel32 = lambda: _fake_kernel32([])     # nothing is mapped
    try:
        bad = parameters._read_cstring(0x7FFF00000000)
    finally:
        crashlog._kernel32 = saved
    crashlog._kernel32 = lambda: _fake_kernel32(
        [(ctypes.addressof(buf), len(buf))])
    try:
        mapped = parameters._read_cstring(ctypes.addressof(buf))
    finally:
        crashlog._kernel32 = saved
    return (good == "DLSSNR.Width" and bad == ""
            and mapped == "DLSSNR.Width")


def _int29_partial_section_check():
    """The run-28 signature, exactly: a section whose VirtualSize claims
    more than the pages that are actually mapped (discardable/uncommitted
    tails). The mapped site must convert, the unmapped one must be REFUSED
    and reported - never read (the pre-fix scanner died in `string_at`
    here, and the AV repeated forever)."""
    import ctypes

    from ants.dlsssr import crashlog

    image, base, exec_rva = _synthetic_module(exec_sites=1,
                                              exec_vsize=0x2000)
    # a second site far inside the claimed-but-unmapped range (it exists in
    # the buffer, so a scanner that ignores VirtualQuery would read it)
    ctypes.memmove(base + exec_rva + 0x1FF0, b"\xcd\x29", 2)
    mapped_end = 0x1800          # headers + the first 0x800 bytes of .text
    lines = []
    saved_emit = crashlog._emit
    crashlog._emit = lines.append
    crashlog._state["int29_done"] = False
    try:
        crashlog.install_int29_trap([(base, "partial.dll")],
                                    k32=_fake_kernel32([(base, mapped_end)]))
    finally:
        crashlog._emit = saved_emit
        crashlog._state["int29_done"] = False
    text = "".join(lines)
    mapped_site = bytes(ctypes.string_at(base + exec_rva + 0x10, 2))
    unmapped_site = bytes(ctypes.string_at(base + exec_rva + 0x1FF0, 2))
    return ("1 fast-fail site(s) converted to breakpoints in partial.dll"
            in text
            and "unreadable page" in text
            and mapped_site == b"\xcc\x90"
            and unmapped_site == b"\xcd\x29")


def _rig_evidence_check():
    """Run the evidence collector over a synthetic rig tree.

    The owner-facing tool is only useful if it finds the artefacts it claims
    to find, so this builds a fake ComfyUI tree (staged runtime, NGX log,
    crash file, a deployed ngx.py with a build marker) and asserts the
    report names the build, the DLL sizes and copies the logs.
    """
    import contextlib
    import importlib.util
    import io
    import tempfile

    spec = importlib.util.spec_from_file_location(
        "ants_collect_rig_evidence", REPO / "tools" / "collect_rig_evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    root = Path(tempfile.mkdtemp(prefix="ants_rig_"))
    try:
        repo = root / "custom_nodes" / "ReAactor_node_ReFactor"
        (repo / "ants" / "dlsssr").mkdir(parents=True)
        (repo / "ants" / "dlsssr" / "ngx.py").write_text(
            'HOST_BUILD = "2099-01-01.1"\n')
        dlss = root / "models" / "DLSS"
        staged = (dlss / "staged"
                  / "nvngx_dlssnr_RenoDX_4000_series_friendly-165830144")
        logs = dlss / "staged" / "ANTs" / "appdata" / "logs"
        logs.mkdir(parents=True)
        (logs / "nvngx.log").write_text("NGXLoadFromPath failed: 0xBAD00000\n")
        (logs / "native-crash.log").write_text("int29 site ...\n")
        staged.mkdir()
        (staged / "nvngx_dlssnr.dll").write_bytes(b"MZ" + b"\x00" * 8192)
        out = root / "out"
        with contextlib.redirect_stdout(io.StringIO()):
            code = module.main(["--repo", str(repo), "--dlss-root", str(dlss),
                                "--out", str(out), "--comfy-root", str(root)])
        report = (out / "rig_evidence.txt").read_text()
        copied = sorted(p.name for p in (out / "files").iterdir())
        return (code == 0
                and "2099-01-01.1" in report          # deployment marker
                and "nvngx_dlssnr.dll" in report      # runtime inventory
                and "sha256(first 8)" in report
                and "nvngx.log" in copied
                and "native-crash.log" in copied
                and "TORCH / CUDA" in report)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def _collector_bat_check():
    """The collector bat follows the owner-facing conventions.

    Paren rule: a paren block that OPENS on the same line as an ``if`` is the
    classic cmd footgun (it breaks as soon as a path holds a space). ``for
    %%I in (...)`` is the sanctioned form - the loop keyword precedes the
    paren - so those lines pass.
    """
    bat = (REPO / "tools" / "collect_rig_evidence.bat")
    raw = bat.read_bytes() if bat.is_file() else b""
    risky = []
    for line in raw_exec_lines(raw):
        lowered = line.lower()
        first = lowered.find(b" (")
        if first == -1:
            continue
        if b"for " in lowered[:first]:
            continue                    # for-loop enumeration form
        risky.append(line)
    return (raw.startswith(b"@echo off\r\n")
            and b"clip <" in raw
            and b"THE ONLY BLOCK YOU MAY EDIT" in raw
            and b"READ-ONLY" in raw
            and not risky)


def _resolver_tolerance_check():
    """Build a minimal PE (one export at RVA 0x1020) and run the resolver
    over it with a junk token plus a real offset; the junk must be skipped
    and the offset resolved."""
    import struct
    import subprocess
    import tempfile

    tool = REPO / "tools" / "resolve_crash_offset.py"
    buf = bytearray(0x2000)

    def put(off, data):
        buf[off:off + len(data)] = data

    put(0, b"MZ")
    put(0x3C, struct.pack("<I", 0x80))
    put(0x80, b"PE\x00\x00")
    put(0x84, struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, 240, 0x2022))
    opt = 0x98
    put(opt, struct.pack("<HBBIIIIIQII", 0x20B, 14, 0, 0x200, 0, 0, 0x1000,
                         0x1000, 0x400000, 0x1000, 0x200))
    put(opt + 112, struct.pack("<II", 0x1000, 0x100))
    put(opt + 120, struct.pack("<II", 0x1200, 0x100))
    put(opt + 240, b".rdata\x00\x00" + struct.pack("<IIII", 0x1000, 0x1000,
                                                   0x1C00, 0x400) + b"\x00" * 20)

    def rva(r):
        return 0x400 + (r - 0x1000)

    # export dir: funcs@0x1180 (clear of the name strings), names@0x1080,
    # ordinals@0x10A0 (0-based indices into AddressOfFunctions)
    put(rva(0x1000), struct.pack("<IIHHIIIIIII", 0, 0, 1, 0, 0x1040, 1, 2, 2,
                                 0x1180, 0x1080, 0x10A0))
    put(rva(0x1040), b"probe.dll\x00")
    put(rva(0x1080), struct.pack("<II", 0x10C0, 0x10E0))
    put(rva(0x10A0), struct.pack("<HH", 0, 1))
    put(rva(0x1180), struct.pack("<II", 0x1000, 0x1020))
    put(rva(0x10C0), b"NVSDK_NGX_D3D12_Init\x00")
    put(rva(0x10E0), b"NVSDK_NGX_D3D12_EvaluateFeature_C\x00")

    with tempfile.NamedTemporaryFile(suffix=".dll", delete=False) as fh:
        fh.write(bytes(buf))
        path = fh.name
    out = subprocess.run(
        [sys.executable, str(tool), path, "resolve_offsets.bat", "0x1025"],
        capture_output=True, text=True).stdout
    return ("[skip] 'resolve_offsets.bat'" in out
            and "0x1025 -> NVSDK_NGX_D3D12_EvaluateFeature_C" in out)


def _imports_probe_check():
    """Build a PE importing KERNEL32!{ExitProcess, NVSDK_NGX_CUDA_Evaluate}
    and verify tools/list_imports.py flags the termination API statically."""
    import struct
    import subprocess
    import tempfile

    tool = REPO / "tools" / "list_imports.py"
    buf = bytearray(0x2000)

    def put(off, data):
        buf[off:off + len(data)] = data

    def w32(off, v):
        put(off, struct.pack("<I", v))

    def w64(off, v):
        put(off, struct.pack("<Q", v))

    put(0, b"MZ")
    put(0x3C, struct.pack("<I", 0x80))
    put(0x80, b"PE\x00\x00")
    put(0x84, struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, 240, 0x2022))
    opt = 0x98
    put(opt, struct.pack("<HBBIIIIIQII", 0x20B, 14, 0, 0x200, 0, 0, 0x1000,
                         0x1000, 0x400000, 0x1000, 0x200))
    put(opt + 120, struct.pack("<II", 0x1300, 0x100))
    put(opt + 240, b".rdata\x00\x00" + struct.pack("<IIII", 0x1000, 0x1000,
                                                    0x1C00, 0x400) + b"\x00" * 20)

    def rva(r):
        return 0x400 + (r - 0x1000)

    w32(rva(0x1300), 0x1400)
    w32(rva(0x130C), 0x13C0)
    w32(rva(0x1310), 0x1500)
    put(rva(0x13C0), b"KERNEL32.dll\x00")
    put(rva(0x1400), struct.pack("<QQQ", 0x1440, 0x1460, 0))
    put(rva(0x1500), struct.pack("<QQQ", 0xDEADBEEF, 0xDEADBEF0, 0))
    put(rva(0x1440), struct.pack("<H", 0) + b"ExitProcess\x00")
    put(rva(0x1460), struct.pack("<H", 0) + b"NVSDK_NGX_CUDA_EvaluateFeature_C\x00")

    with tempfile.NamedTemporaryFile(suffix=".dll", delete=False) as fh:
        fh.write(bytes(buf))
        path = fh.name
    out = subprocess.run([sys.executable, str(tool), path],
                         capture_output=True, text=True).stdout
    return ("TERMINATION API" in out
            and "NVSDK_NGX_CUDA_EvaluateFeature_C" in out
            and "termination APIs imported: ExitProcess" in out)


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name}")


def main():
    install_stubs()
    sys.path.insert(0, str(REPO))

    import numpy as np

    # ---- shim PE structure ----
    from ants.dlsssr.shim import build_shim_dll
    payload, exports = build_shim_dll()
    check("shim: MZ + PE signatures", payload[:2] == b"MZ" and payload[0x40:0x44] == b"PE\x00\x00")
    check("shim: size is file-aligned multiple", len(payload) % 0x200 == 0)
    try:
        import pefile
        pe = pefile.PE(data=payload)
        names = sorted(e.name.decode() for e in pe.DIRECTORY_ENTRY_EXPORT.symbols)
        check("shim: pefile parses + 5 exports (incl. the Init_Ext swap thunk)",
              names == ["fwd_create", "fwd_evaluate", "fwd_init_ext",
                        "fwd_release", "fwd_set_slots"])
        check("shim: entry point + DYNAMIC_BASE|NX_COMPAT",
              pe.OPTIONAL_HEADER.DllCharacteristics & 0x140 == 0x140)
        # unwind metadata for the 4 non-leaf thunks (rig run 14: an exception
        # escaping through an unwindable-less frame killed the process).
        # UNWIND_INFO = ver|flags, SizeOfProlog, CountOfCodes, FrameReg|Off,
        # then UNWIND_CODE = CodeOffset, UnwindOp<<4 | OpInfo: the thunks'
        # "sub rsp, 0x48" is UWOP_ALLOC_SMALL (2) with OpInfo 72/8-1 = 8.
        import struct as _st
        raw = pe.get_memory_mapped_image()
        exc = pe.OPTIONAL_HEADER.DATA_DIRECTORY[3]
        sizes = []
        ok = exc.Size == 48
        for i in range(exc.Size // 12):
            b, e, u = _st.unpack_from("<III", raw, exc.VirtualAddress + i * 12)
            sizes.append(e - b)
            ver, prolog, count = raw[u], raw[u + 1], raw[u + 2]
            ok = ok and ver == 1 and prolog == 4 and count == 1 \
                and raw[u + 4:u + 6] == b"\x04\x28"  # ALLOC_SMALL 72 @4
        check("shim: 4 RUNTIME_FUNCTIONs + shared UNWIND_INFO (thunks unwindable)",
              ok and sizes == [63, 63, 63, 82])
        # the swap thunk's machine code: save r9, then r9 <- first stack arg
        addr = [e.address for e in pe.DIRECTORY_ENTRY_EXPORT.symbols
                if e.name == b"fwd_init_ext"][0]
        code = raw[addr:addr + 8]
        check("shim: fwd_init_ext swaps the last two Init_Ext args in native code",
              code == bytes([0x4D, 0x89, 0xCB,             # mov r11, r9
                             0x48, 0x83, 0xEC, 0x48,       # sub rsp, 0x48
                             0x48]))                       # mov rax, [rsp+..]
    except ImportError:
        check("shim: pefile parses (pefile missing in env)", False)

    # ---- our parameter object: store semantics via its Python side ----
    from ants.dlsssr.parameters import NGX_RESULT_FEATURE_NOT_FOUND, OwnParameterObject
    p = OwnParameterObject()
    obj_vptr = ctypes.cast(p._object, ctypes.POINTER(ctypes.c_void_p)).contents.value
    check("params: object's first field points at the vtable",
          obj_vptr == ctypes_addr_of(p._vtable) and p.ptr.value != 0)
    p.set_u32("DLSSNR.Width", 1920)
    p.set_f32("DLSSNR.Intensity", 1.5)
    p.set_resource("DLSSNR.Color", 0xDEADBEEF)
    check("params: u32/f32/resource stored",
          p.store["DLSSNR.Width"] == 1920 and p.store["DLSSNR.Intensity"] == 1.5
          and p.store["DLSSNR.Color"] == 0xDEADBEEF)

    # getters through the real ctypes callbacks (the runtime's view)
    proto = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    get_u32 = proto(p._vtable[12])
    out = ctypes.c_uint32(0)
    name = ctypes.create_string_buffer(b"DLSSNR.Width\x00")
    hr = get_u32(p.ptr, name, ctypes.byref(out))
    check("params: getter returns SUCCESS + value via the real vtable",
          hr == 1 and out.value == 1920)
    name2 = ctypes.create_string_buffer(b"DLSSNR.NotSet\x00")
    hr2 = get_u32(p.ptr, name2, ctypes.byref(out))
    check("params: unset name answers FeatureNotFound",
          hr2 == (NGX_RESULT_FEATURE_NOT_FOUND & 0xFFFFFFFF) or hr2 == ctypes.c_int32(NGX_RESULT_FEATURE_NOT_FOUND).value)

    # ---- constants ----
    from ants.dlsssr import ngx
    check("ngx: API version 0x15 + feature ids 1/18",
          ngx.NGX_VERSION_API == 0x15 and ngx.FEATURE_SR == 1 and ngx.FEATURE_NR == 18)
    ngx_src = (REPO / "ants" / "dlsssr" / "ngx.py").read_text()
    crashlog_src = (REPO / "ants" / "dlsssr" / "crashlog.py").read_text()
    # ---- run 21 countermeasures (Merserk host contract) ----
    check("ngx: NR preloads the driver core into the process (run 21)",
          "NGX core preloaded" in ngx_src and "locate_ngx_core()" in ngx_src)
    check("ngx: explicit _nvngx.dll override next to the snippet wins",
          '"_nvngx.dll"' in ngx_src and "local_core" in ngx_src)
    # ---- run 28: core-owned session + snippet feature provider ----
    check("ngx: session owner inits by ProjectID -> Init_Ext -> classic Init",
          "Init_ProjectID" in ngx_src and "classic 4-arg Init accepted" in ngx_src
          and "range(0x13, 0x21)" in ngx_src)
    check("ngx: snippet builds get the SWAPPED Init_Ext ABI through the shim "
          "(common_info before version - public order hands it a version int "
          "where it expects a pointer)",
          'thunk="init_ext"' in ngx_src and "snippet ABI" in ngx_src
          and "fwd_init_ext" in (REPO / "ants" / "dlsssr" / "shim.py").read_text())
    check("ngx: caller geometry - session owner direct, snippet through the "
          "shim (ANTS_NR_CORE_VIA_SHIM=1 restores the old geometry)",
          "route_through_shim" in ngx_src
          and "ANTS_NR_CORE_VIA_SHIM" in ngx_src
          and "self._owner_is_snippet or not core_direct" in ngx_src)
    check("ngx: feature 18 runs on the CORE's capability parameter map "
          "(GetCapabilityParameters first, AllocateParameters fallback)",
          "GetCapabilityParameters" in ngx_src
          and "AllocateParameters" in ngx_src
          and "_open_core_parameters" in ngx_src)
    nr_src = (REPO / "ants" / "dlsssr" / "nr.py").read_text()
    check("nr: canonical-name staging (the snippet is only ever loaded as "
          "nvngx_dlssnr.dll by hosts that work)",
          "stage_nr_runtime" in nr_src
          and "nvngx_dlssnr.dll" in nr_src)
    check("nr: the full reference-host create contract is written",
          all(k in nr_src for k in (
              "DLSSNR.InputWidth", "DLSSNR.Output.Width",
              '"DLSSNR.{prefix}SubrectWidth"', "DLSSNR.MVec", "DLSSNR.Depth",
              "DLSSNR.ScalingRatio", "DLSSNR.DepthInverted", "DLSSNR.Upscaling",
              "DLSSNRComputeScalingRatioCallback", "DLSSNR.Backbuffer",
              "NR_POSTPASS_PERF_QUALITY"))
          and "R16G16B16A16_FLOAT" in nr_src
          and "create the neural snippet" not in nr_src.lower())
    # ---- run 29 candidate: the caller-shim MODULE NAME (ecosystem evidence:
    # ---- the working caller shim ships as nvngx.dll_comfy.dll and the bare
    # ---- nvngx.dll name survives only as a legacy fallback there) ----
    from ants.dlsssr import shim as shim_mod
    check("shim: default module name avoids the real nvngx.dll name",
          shim_mod.DEFAULT_SHIM_NAME != "nvngx.dll"
          and shim_mod.DEFAULT_SHIM_NAME.startswith("nvngx.dll")
          and shim_mod.shim_name() == shim_mod.DEFAULT_SHIM_NAME)
    import os as _os2
    _os2.environ["ANTS_NR_SHIM_NAME"] = "nvngx.dll"
    try:
        check("shim: ANTS_NR_SHIM_NAME restores the historical geometry",
              shim_mod.shim_name() == "nvngx.dll")
    finally:
        del _os2.environ["ANTS_NR_SHIM_NAME"]
    import os as _os, tempfile as _tempfile
    _tmpdir = _tempfile.mkdtemp(prefix="ants_shim_")
    _shim_path = shim_mod.write_shim(_tmpdir)
    check("shim: the written PE carries the file name as its module name",
          _os.path.basename(_shim_path) == shim_mod.DEFAULT_SHIM_NAME)
    try:
        import pefile as _pefile
        _pe = _pefile.PE(_shim_path)
        check("shim: export-directory module name follows the file name",
              _pe.DIRECTORY_ENTRY_EXPORT.name.decode()
              == shim_mod.DEFAULT_SHIM_NAME)
    except ImportError:
        pass

    # ---- run 28+: the feature-18 quality/contract rules ----
    check("nr: 1x uses the native (DLAA) quality value - 6 is only the "
          "carrier post-pass and mismatches the 1.0 scaling ratio",
          "NR_PERF_QUALITY_1X = 5" in nr_src
          and "NR_POSTPASS_PERF_QUALITY = 6" in nr_src
          and "perf_quality" in nr_src)
    check("nr: surfaces + subrects are re-applied for every frame",
          nr_src.count("self._write_surfaces(p)") >= 2)
    check("ngx: NvAPI_Initialize pre-step before the core Init (reference "
          "host's first step; ANTS_NR_NVAPI=0 opts out)",
          "_preload_nvapi" in ngx_src and "NvAPI_Initialize" in ngx_src
          and "ANTS_NR_NVAPI" in ngx_src)
    check("ngx: the NGX log callback decodes the runtime's message pointer "
          "(ctypes hands a CFUNCTYPE an int for c_void_p; the first "
          "implementation logged '<unreadable>' for every line)",
          "_read(message)" in ngx_src
          and "read_some(int(ptr), 1024)" in ngx_src
          and "message.decode(" not in ngx_src)
    check("ngx: NR sessions arm the trap + ntdll detour + int29 scan from a "
          "single instrumentation list",
          "_instrumented" in ngx_src
          and "install_termination_trap(self._instrumented)" in ngx_src
          and "install_ntdll_terminate_detour()" in ngx_src
          and "install_int29_trap(self._instrumented)" in ngx_src)
    check("ngx: snippet callbacks are an env-gated experiment, pinned",
          "ANTS_NR_RUNTIME_CALLBACKS" in ngx_src and "_cb_keep" in ngx_src
          and "SetRuntimeParamsCallback" in ngx_src
          and "ANTS_NR_CALLBACK_RET" in ngx_src
          and "ANTS_NR_CALLBACK_DUMP" in ngx_src)
    check("ngx: the callback dumper cannot fault (VirtualQuery, never "
          "IsBadReadPtr - run 23's AV was the dumper itself)",
          "VirtualQuery" in ngx_src and ".IsBadReadPtr(" not in ngx_src
          and "*([_CVOID] * 8)" in ngx_src)
    check("crashlog: silent-kill tracer ships (run 24 = death with no "
          "exception: deliberate terminate, so the trap patches NGX import "
          "tables and logs the killer's call chain before forwarding)",
          "install_termination_trap" in crashlog_src
          and "ExitProcess" in crashlog_src
          and "RaiseFailFastException" in crashlog_src
          and "VirtualProtect" in crashlog_src
          and "RtlCaptureStackBackTrace" in crashlog_src
          and "install_termination_trap" in ngx_src
          and "ANTS_NR_TERMINATION_TRAP" in ngx_src
          # run 27b: the trap install must sit OUTSIDE the callbacks env
          # block - nesting it there let an env deletion strip the nets.
          and ngx_src.index("install_termination_trap")
          < ngx_src.index('os.environ.get("ANTS_NR_RUNTIME_CALLBACKS")'))
    check("crashlog: ntdll!NtTerminateProcess detour ships (run 26: the "
          "death avoided both patched IATs, so the final gate itself is "
          "detoured - stolen syscall stub re-executed after logging)",
          "install_ntdll_terminate_detour" in crashlog_src
          and "install_ntdll_terminate_detour" in ngx_src
          and "\\x4c\\x8b\\xd1" in crashlog_src
          and "\\x0f\\x05" in crashlog_src
          and "no syscall instruction" in crashlog_src
          and "jmp rax" in crashlog_src)
    check("crashlog: int29 fast-fail trap ships (run 27c terminal verdict: "
          "kill is kernel-direct __fastfail/int 29h - CD 29 rewritten to "
          "breakpoints in executable sections so the VEH names the site)",
          "install_int29_trap" in crashlog_src
          and "install_int29_trap" in ngx_src
          and "0x80000003" in crashlog_src
          and "\\xcd\\x29" in crashlog_src
          and "ANTS_NR_INT29_TRAP" in ngx_src)
    check("ngx: E1 direct-bind gate ships (ANTS_NR_USE_SHIM=0 = no shim "
          "module in process = Merserk host geometry; VERSION-gate test)",
          "ANTS_NR_USE_SHIM" in ngx_src)
    check("discovery: the SELECTED NR build is the one canonicalized (run 28 "
          "loaded a same-named sibling instead) and a canonical selection "
          "is used in place",
          _nr_staging_rule_check())
    check("crashlog: IAT termination tracer walks imports and patches the "
          "termination APIs (flat-PE fixture, fake kernel32)",
          _iat_tracer_fixture_check())
    check("params: runtime-supplied parameter names are decoded through the "
          "guard (unreadable pointer -> empty, never a fault)",
          _own_params_name_guard_check())
    check("crashlog: the VirtualQuery guard refuses unmapped / NOACCESS / "
          "GUARD / EXECUTE-only pages and never reads past a region",
          _mem_guard_check())
    check("crashlog: int29 scanner converts CD29 sites in executable "
          "sections only (synthetic PE)",
          _int29_scanner_fixture_check())
    check("crashlog: a section claiming more bytes than are mapped is read "
          "only where the OS vouches for it (mapped site converted, unmapped "
          "site refused and reported) - run 28's second fault signature",
          _int29_partial_section_check())
    check("crashlog: int29 scanner survives hostile images (unmapped base, "
          "absurd section count, table past the mapping, unmapped section) "
          "- run 28 died in this scan, before NGX init",
          _int29_scanner_safety_check())
    check("tools: crash-offset resolver ships (names MODULE+0xRVAs)",
          (REPO / "tools" / "resolve_crash_offset.py").is_file()
          and "bisect" in (REPO / "tools" / "resolve_crash_offset.py").read_text())
    check("tools: resolver tolerates junk tokens (rig: the bat's own name "
          "reached int() and tracebacked - now skipped with [skip])",
          _resolver_tolerance_check())
    check("tools: rig evidence collector finds the deployment marker, the "
          "staged runtime and the NGX log, and copies the logs out "
          "(one folder for the owner to send)",
          _rig_evidence_check())
    check("tools: the collector bat is CRLF, clipboard-returning, marked "
          "READ-ONLY and has no parens on executable lines",
          _collector_bat_check())
    check("tools: import lister flags termination APIs + NGX backend "
          "bindings statically (engine-dll discriminator, no execution)",
          (REPO / "tools" / "list_imports.py").is_file()
          and (REPO / "tools" / "list_imports.bat").is_file()
          and _imports_probe_check())


    bat = (REPO / "tools" / "resolve_offsets.bat")
    bat_raw = bat.read_bytes() if bat.is_file() else b""
    check("tools: rig diagnostics ship as ready-to-run bat files (owner "
          "imperative) - resolver bat is CRLF, clipboard-returning, with a "
          "single marked owner-editable block and zero parens on executable "
          "lines (rem comments may)",
          bat_raw.startswith(b"@echo off\r\n")
          and b"clip <" in bat_raw
          and b"THE ONLY BLOCK YOU MAY EDIT" in bat_raw
          and b'if "' in bat_raw
          and not any(b" (" in ln for ln in raw_exec_lines(bat_raw)))
    check("ngx: session close frees the core AFTER the snippet (reverse order)",
          ngx_src.find("self.module.close()") < ngx_src.find(
              "Reverse load order"))
    from ants.dlsssr.sr import PERF_RATIO, DLSS_RENDER_PRESETS
    check("sr: mode ratios fixed (DLAA 1.0 .. UP 3.0)",
          PERF_RATIO[5] == 1.0 and PERF_RATIO[3] == 3.0 and PERF_RATIO[1] > 1.72)
    check("sr: preset letters include J/K/L/M",
          {"J": 10, "K": 11, "L": 12, "M": 13}.items() <= DLSS_RENDER_PRESETS.items())
    from ants.dlsssr.d3d12 import (_DEVICE_CREATE_COMMITTED_RESOURCE, _LIST_RESOURCE_BARRIER,
                                   _QUEUE_SIGNAL, D3D_FEATURE_LEVEL_11_0)
    check("d3d12: vtable slots match the public headers",
          _DEVICE_CREATE_COMMITTED_RESOURCE == 27 and _LIST_RESOURCE_BARRIER == 26
          and _QUEUE_SIGNAL == 14 and D3D_FEATURE_LEVEL_11_0 == 0xB000)

    # ---- discovery: flat SR dlls are their own sets (the owner's test case) ----
    import os, tempfile
    from ants.dlsssr import discovery as sr_disc
    from ants.dlssnr import discovery as nr_disc
    with tempfile.TemporaryDirectory() as td:
        sr_root = os.path.join(td, "SR")
        os.makedirs(sr_root)
        # real SR runtimes are tens of MB; the guard rejects <1MB stubs
        open(os.path.join(sr_root, "nvngx_dlss.dll"), "wb").write(b"x" * 1_100_000)
        open(os.path.join(sr_root, "nvngx_dlss_310.9.1.dll"), "wb").write(b"x" * 1_200_000)
        saved = nr_disc.DLSS_ROOT
        nr_disc.DLSS_ROOT = td
        try:
            sets = sr_disc.discover_sr_sets()
            check("sr discovery: two flat dlls = two named sets",
                  {s["name"] for s in sets} == {"nvngx_dlss", "nvngx_dlss_310.9.1"}
                  and all(s["kind"] == "dll" for s in sets))
            resolved = sr_disc.resolve_sr_dll("nvngx_dlss_310.9.1.dll".rsplit(".", 1)[0])
            check("sr discovery: resolve by set name hits the right dll",
                  resolved.endswith("nvngx_dlss_310.9.1.dll"))
            try:
                nr_disc.DLSS_ROOT = os.path.join(td, "empty")
                sr_disc.resolve_sr_dll("auto")
                check("sr discovery: loud error when SR folder empty", False)
            except RuntimeError as exc:
                check("sr discovery: loud error when SR folder empty",
                      "[ANTs]" in str(exc) and "SR" in str(exc)
                      and "redistribution" in str(exc))
        finally:
            nr_disc.DLSS_ROOT = saved

        # ---- masquerader guard: a small "nvngx_dlss.dll" is a stub ----
        nr_disc.DLSS_ROOT = td
        os.makedirs(os.path.join(td, "SR"), exist_ok=True)
        open(os.path.join(td, "SR", "nvngx_dlss.dll"), "wb").write(b"stub")
        try:
            sr_disc.resolve_sr_dll("nvngx_dlss")
            check("sr: masquerader guard rejects a small stub", False)
        except RuntimeError as exc:
            check("sr: masquerader guard rejects a small stub",
                  "under 1 MB" in str(exc) and "helper/caller stub" in str(exc))
        open(os.path.join(td, "SR", "nvngx_dlss_310.9.1.dll"), "wb").write(b"x" * 1_100_000)
        check("sr: auto skips stubs and finds the real runtime",
              sr_disc.resolve_sr_dll("auto").endswith("nvngx_dlss_310.9.1.dll"))

        # NR runtime locator
        nr_dir = os.path.join(td, "NR", "v1")
        os.makedirs(nr_dir)
        open(os.path.join(nr_dir, "nvngx_dlssnr_RenoDX_friendly.dll"), "wb").write(b"x")
        found = sr_disc.find_nr_runtime_dll(nr_dir)
        check("nr locator: any nvngx_dlssnr* filename accepted",
              found.endswith("nvngx_dlssnr_RenoDX_friendly.dll"))

    # ---- frame converter math (numpy twin of the node's torch converters;
    # ---- the sandbox has no torch, so the exact node methods run only on rig)
    rng = np.random.RandomState(3)
    frame = rng.rand(48, 32, 3).astype(np.float32)
    rgb8 = (np.clip(frame, 0.0, 1.0) * 255.0).round().astype(np.uint8)
    rgba = np.empty((48, 32, 4), dtype=np.uint8)
    rgba[:, :, :3] = rgb8
    rgba[:, :, 3] = 255
    payload = rgba.tobytes()
    arr = np.frombuffer(payload, dtype=np.uint8).reshape(48, 32, 4)
    back = arr[:, :, :3].astype(np.float32) / 255.0
    check("converters: rgba8 packing math round-trips within 8-bit error",
          float(np.max(np.abs(back - frame))) < 1.0 / 255.0 + 1e-4
          and (arr[:, :, 3] == 255).all())

    # ---- engine widget surface ----
    import importlib
    sys.path.insert(0, str(REPO.parent))
    pkg = importlib.import_module(REPO.name)
    types_def = pkg.NODE_CLASS_MAPPINGS["ANTsDLSS5Enhancer"].INPUT_TYPES()
    check("dlss5: engine widget present, native default",
          types_def["required"]["engine"][0][0] == "ANTs native NGX")
    check("dlss5: 20 nodes registered (SR upscaler added)",
          len(pkg.NODE_CLASS_MAPPINGS) == 20
          and "ANTsDLSSSRUpscaler" in pkg.NODE_CLASS_MAPPINGS)

    # ---- IID wire bytes (canonical GUID layout; reference bytes are
    # ---- hand-written literals, NOT derived from guid() - the rig hit
    # ---- E_NOINTERFACE because guid() once reversed the field order) ----
    from ants.dlsssr.com import guid as _guid
    check("iid: ID3D12Device wire bytes match the canonical literal",
          bytes(_guid("{189819f1-1db6-4b57-be54-1821339b85f7}"))
          == bytes([0xf1, 0x19, 0x98, 0x18, 0xb6, 0x1d, 0x57, 0x4b,
                    0xbe, 0x54, 0x18, 0x21, 0x33, 0x9b, 0x85, 0xf7]))
    from ants.dlsssr import d3d12 as _d3d12
    check("iid: ID3D12Fence wire bytes (base fence, rig-verified - 4a689c71 is Fence1)",
          bytes(_d3d12.IID_ID3D12Fence)
          == bytes([0xcf, 0x3d, 0x75, 0x0a, 0xd8, 0xc4, 0x91, 0x4b,
                    0xad, 0xf6, 0xbe, 0x5a, 0x60, 0xd9, 0x5a, 0x76]))
    check("iid: ID3D12Resource wire bytes (...0fad, not ...02ad)",
          bytes(_d3d12.IID_ID3D12Resource)
          == bytes([0xbe, 0x42, 0x64, 0x69, 0x2e, 0xa7, 0x59, 0x40,
                    0xbc, 0x79, 0x5b, 0x5c, 0x98, 0x04, 0x0f, 0xad]))
    check("iid: IDXGIFactory1 wire bytes match the canonical literal",
          bytes(_guid("{770aae78-f26f-4dba-a829-253c83d1b387}"))
          == bytes([0x78, 0xae, 0x0a, 0x77, 0x6f, 0xf2, 0xba, 0x4d,
                    0xa8, 0x29, 0x25, 0x3c, 0x83, 0xd1, 0xb3, 0x87]))

    # ---- win32 calling conventions (rig run 7: CFUNCTYPE rejected the
    # ---- c_void_p wrap; GetProcAddress had no restype = 64-bit truncation)
    from ants.dlsssr import win32 as _w
    proto0 = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)
    tgt = proto0(lambda v: v * 2)
    addr = ctypes.cast(tgt, ctypes.c_void_p).value
    check("win32: callable_at accepts int and c_void_p addresses",
          _w.callable_at(addr, [ctypes.c_int], ctypes.c_int)(21) == 42
          and _w.callable_at(ctypes.c_void_p(addr), [ctypes.c_int], ctypes.c_int)(21) == 42)
    check("win32: export_address handles int and c_void_p handles (no int() parse trap)",
          _w.export_address(0x1000, 0x40) == 0x1040
          and _w.export_address(ctypes.c_void_p(0x1000), 0x40) == 0x1040)
    import pathlib as _pl
    src = _pl.Path(REPO / "ants" / "dlsssr" / "win32.py").read_text()
    check("win32: pointer-returning kernel32 fns have c_void_p restypes",
          "_k32.GetProcAddress.restype = ctypes.c_void_p" in src
          and "_k32.LoadLibraryExW.restype = ctypes.c_void_p" in src)

    # ---- d3d12 struct packs (sizes/fields per d3d12.h, PE32+) ----
    from ants.dlsssr import d3d12 as d12
    tex = d12._resource_desc_texture(64, 48, d12.DXGI_FORMAT_R8G8B8A8_UNORM,
                                     d12.D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS)
    buf = d12._resource_desc_buffer(4096)
    check("d3d12: RESOURCE_DESC is 56 bytes, TEXTURE2D(3) + zero alignment",
          len(tex) == 56 and len(buf) == 56
          and tex[0:4] == (3).to_bytes(4, "little") and tex[4:12] == b"\x00" * 8)
    check("d3d12: HEAP_TYPE_DEFAULT is 1 (0 = UNKNOWN -> E_INVALIDARG, rig run 11)",
          _d3d12.D3D12_HEAP_TYPE_DEFAULT == 1 and _d3d12.D3D12_HEAP_TYPE_UNKNOWN == 0
          and _d3d12.D3D12_HEAP_TYPE_UPLOAD == 2 and _d3d12.D3D12_HEAP_TYPE_READBACK == 3)
    check("d3d12: buffer desc is BUFFER(1) + ROW_MAJOR layout (driver rule)",
          buf[0:4] == (1).to_bytes(4, "little")
          and buf[44:48] == (1).to_bytes(4, "little"))
    check("d3d12: UAV resource flag is 0x8 (0x4 = render target)",
          d12.D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS == 0x8
          and tex[-8:] == (0x8).to_bytes(8, "little"))
    d3d12_src = (REPO / "ants" / "dlsssr" / "d3d12.py").read_text()
    check("d3d12: staging heaps are never transitioned (barriers on "
          "UPLOAD/READBACK heaps are invalid commands - rig Close 0x80070057)",
          "UPLOAD_READBACK_HEAPS" in d3d12_src
          and "transition(staging" not in d3d12_src
          and "Heap not closable" not in d3d12_src)
    check("d3d12: any unconclosable command list is recovered loudly, with a "
          "strict opt-out for A/B runs",
          "ANTS_D3D12_STRICT_CLOSE" in d3d12_src
          and "not closable" in d3d12_src
          and "recorded since the last submit is LOST" in d3d12_src)
    check("d3d12: staging buffers outlive the recording that references them "
          "(freed after the GPU is done, not at record time)",
          "_pending_release" in d3d12_src
          and "staging.release()" not in d3d12_src)
    check("d3d12: heap props 20B, barrier 32B",
          len(d12._heap_properties(0)) == 20
          and len(d12._transition_barrier(0x1234, 0, 2)) == 32)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def ctypes_ptr(cvoid):
    return cvoid.value or 0


def ctypes_addr_of(vtable):
    return ctypes.cast(vtable, ctypes.c_void_p).value or 0


if __name__ == "__main__":
    import ctypes
    sys.exit(main())

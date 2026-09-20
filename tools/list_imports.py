"""List a PE dll's imported functions (every name), flagging termination/NGX APIs.

Static analysis ONLY - the target is parsed as a file, never executed:

    python list_imports.py <dll path> [--all]

Grouped by importing dll. Flagged by default (use --all to see everything):
  - termination APIs: ExitProcess, TerminateProcess, abort, exit, _exit,
    terminate, RaiseFailFastException, NtTerminateProcess (the exact APIs
    the ANTs NGX traps patch - a dll that imports none of them kills via
    statically linked CRT abort/fastfail or inline syscalls);
  - NGX surface: NVSDK_NGX_* (D3D12 vs CUDA backend split is the key
    discriminator: which backend does a host actually bind?);
  - CUDA driver: cu* from nvcuda.dll.
"""
import struct
import sys

_TERM = {"ExitProcess", "TerminateProcess", "abort", "exit", "_exit",
         "terminate", "RaiseFailFastException", "NtTerminateProcess"}


def imports(path):
    """{dll_name: [func, ...]} from the import directory (PE32+ file parse)."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:2] != b"MZ":
        raise ValueError("not a PE image")
    e = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e:e + 4] != b"PE\x00\x00":
        raise ValueError("no PE signature")
    opt = e + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic != 0x20B:
        raise ValueError("not PE32+ (64-bit dlls only here)")
    imp_rva, imp_size = struct.unpack_from("<II", data, opt + 120)
    if not imp_rva:
        return {}
    n_sec = struct.unpack_from("<H", data, e + 6)[0]
    sec0 = opt + struct.unpack_from("<H", data, e + 20)[0]
    sections = []
    for i in range(n_sec):
        b = sec0 + i * 40
        vsize, vaddr, rsize, raddr = struct.unpack_from("<IIII", data, b + 8)
        sections.append((vaddr, max(vsize, rsize), raddr))

    def r2o(rva):
        for vaddr, size, raddr in sections:
            if vaddr <= rva < vaddr + size:
                return raddr + (rva - vaddr)
        return None

    def cstr(off):
        end = data.find(b"\x00", off)
        return data[off:end].decode("ascii", "replace")

    result = {}
    d = 0
    while d * 20 < imp_size:
        base = r2o(imp_rva + d * 20)
        if base is None:
            break
        oft, _ts, _fc, name_rva, ft = struct.unpack_from("<IIIII", data, base)
        if not (oft or ft or name_rva):
            break
        dll = cstr(r2o(name_rva))
        names = []
        i = 0
        while i < 65535:
            t_off = r2o((oft or ft) + i * 8)
            if t_off is None:
                break
            thunk = struct.unpack_from("<Q", data, t_off)[0]
            if not thunk:
                break
            if not thunk >> 63:
                n_off = r2o(thunk + 2)
                if n_off is not None:
                    names.append(cstr(n_off))
            i += 1
        if oft == 0:  # bound imports: names resolved, only IAT present
            names = [f"<bound import #{k}>" for k in range(len(names))] or names
        result[dll] = names
        d += 1
    return result


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    show_all = "--all" in argv[2:]
    path = argv[1]
    try:
        table = imports(path)
    except (OSError, ValueError) as exc:
        print(f"[X] {path}: {exc}")
        return 1
    total = sum(len(v) for v in table.values())
    print(f"[ANTs] {path}: {len(table)} imported dlls, {total} functions")
    for dll, names in sorted(table.items()):
        interesting = [n for n in names
                       if n in _TERM or n.startswith("NVSDK_NGX")
                       or (dll.lower().startswith("nvcuda") and n.startswith("cu"))]
        if not (show_all or interesting):
            continue
        print(f"  {dll}: {len(names)} imports")
        for n in names:
            if show_all or n in interesting:
                mark = "  <== TERMINATION API" if n in _TERM else ""
                print(f"      {n}{mark}")
    term = sorted({n for names in table.values() for n in names if n in _TERM})
    if term:
        print(f"[ANTs] termination APIs imported: {', '.join(term)}")
    else:
        print("[ANTs] NO termination APIs imported - any kill this dll does "
              "is statically linked CRT abort/fastfail or an inline syscall.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

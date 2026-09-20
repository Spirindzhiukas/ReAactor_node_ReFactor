"""Name the function a native crash offset landed in.

The ANTs crash black box reports faults as ``MODULE.DLL+0x<offset>`` - the
offset is the RVA (module-relative) of the faulting instruction. This tool
walks the module's export table and names the export that contains (or
nearest-precedes) each offset, plus the neighboring exports for context:

    python tools\\resolve_crash_offset.py C:\\WINDOWS\\System32\\KERNEL32.DLL 0x27799
    python tools\\resolve_crash_offset.py KERNEL32.DLL 0x27799 0x12345

A bare module name is resolved against System32 (and .DLL appended if
missing). Exit code 0 with a verdict per offset. Stdlib only.
"""
import bisect
import os
import struct
import sys


def export_rvas(path):
    """[(rva, name)] of every named export, PE32+ stdlib parse."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:2] != b"MZ":
        raise ValueError("not a PE image")
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        raise ValueError("no PE signature")
    coff = e_lfanew + 4
    (_machine, n_sec, _t, _s, _n, opt_size, _c) = struct.unpack_from(
        "<HHIIIHH", data, coff)
    opt = coff + 20
    if struct.unpack_from("<H", data, opt)[0] != 0x20B:
        raise ValueError("not PE32+")
    exp_rva, _sz = struct.unpack_from("<II", data, opt + 112)
    if not exp_rva:
        raise ValueError("module has no export directory")
    sec0 = opt + opt_size
    sections = []
    for i in range(n_sec):
        base = sec0 + i * 40
        vsize, vaddr, rsize, raddr = struct.unpack_from("<IIII", data, base + 8)
        sections.append((vaddr, max(vsize, rsize), raddr))

    def rva2off(rva):
        for vaddr, size, raddr in sections:
            if vaddr <= rva < vaddr + size:
                return raddr + (rva - vaddr)
        return None

    def cstr(off):
        end = data.find(b"\x00", off)
        if end < 0:
            end = off + 256
        return data[off:end].decode("ascii", "replace")

    off = rva2off(exp_rva)
    if off is None:
        raise ValueError("export directory not mapped")
    (_f, _t, _mj, _mi, _n, _b, _nf, n_names, _af,
     addr_names, _ao) = struct.unpack_from("<IIHHIIIIIII", data, off)
    names_off = rva2off(addr_names)
    if names_off is None:
        raise ValueError("export names not mapped")
    # AddressOfFunctions + AddressOfNameOrdinals live right after
    # AddressOfNames in the export directory struct (offsets +28/+36).
    funcs_rva = struct.unpack_from("<I", data, off + 28)[0]
    ords_rva = struct.unpack_from("<I", data, off + 36)[0]
    funcs_off = rva2off(funcs_rva)
    ords_off = rva2off(ords_rva)
    result = []
    for i in range(n_names):
        name_ptr = struct.unpack_from("<I", data, names_off + i * 4)[0]
        ordinal = struct.unpack_from("<H", data, ords_off + i * 2)[0]
        func_rva = struct.unpack_from("<I", data, funcs_off + ordinal * 4)[0]
        result.append((func_rva, cstr(rva2off(name_ptr))))
    result.sort()
    return result


def resolve_module(arg):
    if os.path.isfile(arg):
        return arg
    name = arg if arg.lower().endswith(".dll") else arg + ".DLL"
    windir = os.environ.get("WINDIR", r"C:\Windows")
    cand = os.path.join(windir, "System32", name)
    if os.path.isfile(cand):
        return cand
    raise FileNotFoundError(f"no such module file: {arg}")


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    path = resolve_module(argv[1])
    entries = export_rvas(path)
    rvas = [rva for rva, _n in entries]
    size = os.path.getsize(path)
    print(f"[ANTs] {path}: {len(entries)} named exports")
    for raw in argv[2:]:
        off = int(raw, 0)
        if off >= size:
            print(f"  0x{off:X} -> past end of file (module-based dlls are "
                  "virtual; a raw file offset was likely intended)")
        idx = bisect.bisect_right(rvas, off) - 1
        if idx < 0:
            print(f"  0x{off:X} -> before the first named export")
            continue
        rva, name = entries[idx]
        lo = max(0, idx - 2)
        hi = min(len(entries), idx + 3)
        near = " | ".join(
            f"{n}@0x{r:X}" + ("  <== CONTAINS" if r == rva else "")
            for r, n in entries[lo:hi])
        print(f"  0x{off:X} -> {name} (RVA 0x{rva:X} +0x{off - rva:X})")
        print(f"      neighborhood: {near}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

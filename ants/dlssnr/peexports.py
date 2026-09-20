"""Read a DLL's EXPORT TABLE without loading it (bounded, read-only).

Why this exists: the CUDA regression the owner hit ("engine lacks CUDA
interop") is decided by two things - which helper DLL is loaded and which
entry points it actually has. Loading a 158 MB runtime (or a wrong DLL) to
find out is expensive and can run code; reading the export directory is a few
hundred bytes of file I/O and runs nothing.

Safety rules (learned the hard way on this rig - an access violation inside a
raw ctypes deref is fatal and unrecoverable):
  * never map or dereference the file - plain ``read``/``seek`` only;
  * every offset is validated against the file size before use, every count is
    capped, and any inconsistency means "no names" rather than an exception;
  * a truncated or hostile image (the run-28 lesson) returns an empty set, so
    callers fall back to their normal behaviour instead of dying.

The parser is intentionally duplicated from ``tools/resolve_crash_offset.py``
(which is a standalone owner tool and must not import the pack): keep the two
in sync when the PE logic changes.
"""

import os
import struct

__all__ = ["export_names", "has_exports", "CUDA_ENTRYPOINTS"]

# The entry points that make the zero-copy GPU path work.
CUDA_ENTRYPOINTS = ("dlss5nr_process_cuda_v6", "dlss5nr_cuda_supported")

_MAX_SECTIONS = 96
_MAX_NAMES = 20000


def export_names(path):
    """Set of exported names, or an empty set when the file cannot be read."""
    try:
        return _export_names(path)
    except (OSError, ValueError, struct.error, IndexError):
        return set()


def has_exports(path, wanted):
    """True when ALL of ``wanted`` are exported by ``path``."""
    names = export_names(path)
    return bool(names) and all(name in names for name in wanted)


def _export_names(path):
    size = os.path.getsize(path)
    with open(path, "rb") as handle:

        def read(offset, length):
            if offset < 0 or length < 0 or offset + length > size:
                raise ValueError("read outside the file")
            handle.seek(offset)
            data = handle.read(length)
            if len(data) != length:
                raise ValueError("short read")
            return data

        if read(0, 2) != b"MZ":
            raise ValueError("not a PE image")
        e_lfanew = struct.unpack("<I", read(0x3C, 4))[0]
        if read(e_lfanew, 4) != b"PE\x00\x00":
            raise ValueError("no PE signature")
        coff = e_lfanew + 4
        (_machine, n_sec, _stamp, _sym, _n_sym, opt_size,
         _chars) = struct.unpack("<HHIIIHH", read(coff, 20))
        opt = coff + 20
        if struct.unpack("<H", read(opt, 2))[0] != 0x20B:
            raise ValueError("not PE32+")
        exp_rva = struct.unpack("<I", read(opt + 112, 4))[0]
        if not exp_rva:
            raise ValueError("no export directory")
        n_sec = min(n_sec, _MAX_SECTIONS)
        sections = []
        sec0 = opt + opt_size
        for i in range(n_sec):
            base = sec0 + i * 40
            vsize, vaddr, rsize = struct.unpack("<III", read(base + 8, 12))
            raddr = struct.unpack("<I", read(base + 20, 4))[0]
            sections.append((vaddr, max(vsize, rsize), raddr))

        def rva2off(rva):
            for vaddr, vsize, raddr in sections:
                if vaddr <= rva < vaddr + vsize:
                    return raddr + (rva - vaddr)
            return None

        def cstring(offset, limit=256):
            chunk = read(offset, min(limit, size - offset))
            end = chunk.find(b"\x00")
            return chunk[:end if end >= 0 else len(chunk)].decode("ascii", "replace")

        off = rva2off(exp_rva)
        if off is None:
            raise ValueError("export directory not mapped")
        (_flags, _tstamp, _mj, _mi, _n, _base, _nfunc, n_names,
         _addr_funcs, addr_names, _addr_ords) = struct.unpack(
            "<IIHHIIIIIII", read(off, 40))
        names_off = rva2off(addr_names)
        funcs_rva, ords_rva = struct.unpack("<I", read(off + 28, 4))[0], \
            struct.unpack("<I", read(off + 36, 4))[0]
        funcs_off, ords_off = rva2off(funcs_rva), rva2off(ords_rva)
        if None in (names_off, funcs_off, ords_off):
            raise ValueError("export tables not mapped")
        out = set()
        for i in range(min(n_names, _MAX_NAMES)):
            name_rva = struct.unpack("<I", read(names_off + i * 4, 4))[0]
            name_off = rva2off(name_rva)
            if name_off is None:
                continue
            out.add(cstring(name_off))
        return out

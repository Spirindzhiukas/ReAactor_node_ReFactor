"""Extract what the PROVEN C++ NGX host actually does - from its binaries.

Rig runs 14-19 proved that Merserk's Visual Enhancer v10 hosts the very
same RenoDX-derived nvngx_dlssnr.dll build that force-terminates under our
pure-Python provider. His host lives in two small native bridges:

    runtime/dlssnr/neuroframe_engine_neural_rendering.dll  (558.5 KB)
    runtime/dlssnr/neuroframe_caller.dll                   (102.5 KB)

and the runtime itself:

    runtime/dlssnr/nvngx_dlssnr.dll                        (158.15 MB)

This tool READS those files (never modifies, never copies them out of the
leaked folder), decodes their PE export/import tables with nothing but the
stdlib, and dumps the strings that answer the only question that matters
right now: which NGX surface does the working host implement or call -
NVSDK_NGX_D3D12_Init vs Init_Ext, CreateFeature/AllocateParameters,
provider dispatch (nvngx.dll) vs direct runtime loading, logging, GPU
gates, ReShade/RenoDX markers.

Run it with ComfyUI's python (no extra packages needed):

    python tools\\probe_neuroframe_host.py "D:\\AI_STUFF\\DLSS 5 Files LEAKED\\Visual.Enhancer.v10.0"

or just drop this script INTO the Visual.Enhancer.v10.0 folder and run it
with no argument at all (it then probes the folder it sits in).

It prints a paste-sized report to the console AND writes the full version
(next to the current working directory) as probe_neuroframe_host_report.txt
- paste the console output, or upload the txt.
"""
import hashlib
import os
import re
import struct
import sys

SMALL_FILES = [
    ("engine (the proven C++ NGX host)",
     os.path.join("runtime", "dlssnr", "neuroframe_engine_neural_rendering.dll")),
    ("caller (Python -> engine bridge)",
     os.path.join("runtime", "dlssnr", "neuroframe_caller.dll")),
]
BIG_FILE = ("NR runtime (the RenoDX-derived build under test)",
            os.path.join("runtime", "dlssnr", "nvngx_dlssnr.dll"))

# String classes we mine from the 158 MB runtime (capped per class).
BIG_CLASSES = [
    ("NGX API surface names", re.compile(rb"NVSDK_NGX[A-Za-z0-9_]*"), 150),
    ("ReShade / RenoDX markers",
     re.compile(rb"[Rr]e[Ss]hade|RENODEX|RenoDX|renodx"), 80),
    ("failure / termination words",
     re.compile(rb"(?i)(unsupport[a-z]*|not support[a-z]*|incompatible|"
                rb"terminat[a-z]*|fatal[a-z ]*|abort|assert[a-z]*)"), 120),
    ("GPU / driver gates",
     re.compile(rb"(?i)(4090|4080|4070|RTX ?40|5090|5080|RTX ?50|"
                rb"driver [0-9]{3}|driver version)"), 80),
    ("provider / dispatch / logging",
     re.compile(rb"(?i)(nvngx[a-z_.0-9]*|snippet|project[_. ]?id|appdata|"
                rb"Init_?Ext|CreateFeature|EvaluateFeature|AllocateParameters|"
                rb"SetLogger|GetCaps|ReleaseFeature)"), 200),
]
CAP_SMALL_STRINGS = 600  # per small file, full-ish but paste-able


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def md5(data):
    return hashlib.md5(data).hexdigest()


def pe_tables(data):
    """(machine, [export names], [(dll, [func names])]) - PE32+ only."""
    if data[:2] != b"MZ":
        raise ValueError("not a PE image (no MZ)")
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        raise ValueError("no PE signature")
    coff = e_lfanew + 4
    (machine, n_sec, _ts, _sym, _nsym,
     opt_size, _chars) = struct.unpack_from("<HHIIIHH", data, coff)
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic != 0x20B:
        raise ValueError(f"not PE32+ (magic 0x{magic:04X}) - adapt needed")
    ddir = opt + 112
    exp_rva, _exp_size = struct.unpack_from("<II", data, ddir)
    imp_rva, _imp_size = struct.unpack_from("<II", data, ddir + 8)
    sec0 = opt + opt_size
    sections = []
    for i in range(n_sec):
        base = sec0 + i * 40
        _name = data[base:base + 8].rstrip(b"\x00")
        vsize, vaddr, rsize, raddr = struct.unpack_from("<IIII", data, base + 8)
        sections.append((vaddr, max(vsize, rsize), raddr))

    def rva2off(rva):
        for vaddr, size, raddr in sections:
            if vaddr <= rva < vaddr + size:
                return raddr + (rva - vaddr)
        return None

    def cstr(off):
        if off is None or off >= len(data):
            return "<unmapped>"
        end = data.find(b"\x00", off)
        if end < 0:
            end = min(off + 512, len(data))
        return data[off:end].decode("ascii", "replace")

    exports = []
    if exp_rva:
        off = rva2off(exp_rva)
        if off is not None:
            fmt = "<IIHHIIIIIII"  # IMAGE_EXPORT_DIRECTORY (40 bytes)
            (_c, _ts, _mj, _mi, _name, _base, _nf, n_names,
             _af, addr_names, _ao) = struct.unpack_from(fmt, data, off)
            names_off = rva2off(addr_names)
            if names_off is not None:
                for i in range(n_names):
                    ptr = struct.unpack_from("<I", data, names_off + i * 4)[0]
                    exports.append(cstr(rva2off(ptr)))

    imports = []
    if imp_rva:
        off = rva2off(imp_rva)
        while off is not None and off + 20 <= len(data):
            oft, _ts, _fc, name_rva, ft = struct.unpack_from("<IIIII", data, off)
            if not name_rva and not oft:
                break
            dll = cstr(rva2off(name_rva))
            funcs = []
            thunk = rva2off(oft or ft)
            while thunk is not None and thunk + 8 <= len(data):
                val = struct.unpack_from("<Q", data, thunk)[0]
                if val == 0:
                    break
                if val & (1 << 63):
                    funcs.append(f"#{val & 0xFFFF}")
                else:
                    name_off = rva2off(val & 0x7FFFFFFF)
                    if name_off is None or name_off + 2 >= len(data):
                        funcs.append("<unmapped>")
                    else:
                        funcs.append(cstr(name_off + 2))
                thunk += 8
                if len(funcs) >= 400:  # sanity cap per dll
                    funcs.append("...")
                    break
            imports.append((dll, funcs))
            off += 20
    return machine, exports, imports


def mine_strings(data, classes):
    found = {}
    for label, pattern, cap in classes:
        seen, hits = set(), []
        for match in pattern.finditer(data):
            text = match.group().decode("ascii", "replace")
            key = text.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            hits.append(text.strip())
            if len(hits) >= cap:
                break
        found[label] = hits
    return found


def section(out, title):
    out.append("")
    out.append(f"---- {title} " + "-" * max(1, 62 - len(title)))


def probe_file(label, path, big=False, full_strings_hook=None):
    out = []
    section(out, f"{label}: {os.path.basename(path)}")
    try:
        data = read(path)
    except OSError as exc:
        out.append(f"  !! cannot read: {exc}")
        return out
    out.append(f"  size: {len(data):,} bytes   md5: {md5(data)}")
    try:
        machine, exports, imports = pe_tables(data)
        out.append(f"  machine: 0x{machine:04X}   exports: {len(exports)}   "
                   f"import dlls: {len(imports)}")
        if exports:
            section(out, "EXPORTS (what this module IMPLEMENTS)")
            for name in exports:
                out.append(f"    {name}")
        if imports:
            section(out, "IMPORTS (what this module NEEDS)")
            for dll, funcs in imports:
                out.append(f"    {dll}: {', '.join(funcs[:60])}"
                           + (" ..." if len(funcs) > 60 else ""))
        prov = [e for e in exports if e.upper().startswith("NVSDK_NGX")]
        if prov:
            out.append("  >> VERDICT LEAD: this module IMPLEMENTS the "
                       f"NGX provider surface ({len(prov)} NVSDK_NGX_* "
                       "exports) - it plays our shim's role in C++.")
        blob = b"\n".join(s.encode("ascii", "replace") for s in exports)
        blob += b"\n" + b"\n".join(
            f"{dll}|{fn}".encode("ascii", "replace")
            for dll, funcs in imports for fn in funcs)
    except (ValueError, struct.error) as exc:
        out.append(f"  !! PE parse failed: {exc}")
        blob = b""

    if big:
        section(out, "STRINGS OF INTEREST (capped per class)")
        mined = mine_strings(data, BIG_CLASSES)
        for label2, hits in mined.items():
            out.append(f"  [{label2}] {len(hits)} unique:")
            for text in hits:
                out.append(f"    {text}")
    else:
        text = [m.group().decode("ascii", "replace")
                for m in re.finditer(rb"[\x20-\x7e]{5,}", data)]
        interesting = []
        for line in text:
            if re.search(rb"NVSDK|NGX|nvngx|D3D12|dxgi|DXGI|Feature|"
                         rb"Evaluat|Snip|Log|logger|reshade|Reshade|ReShade|"
                         rb"renodx|RenoDX|Init|appdata|AppData|nvapi|nvml|"
                         rb"version|Version", line.encode("ascii", "replace")):
                interesting.append(line)
        section(out, f"NGX-RELEVANT STRINGS ({len(interesting)} of "
                     f"{len(text)} total)")
        for line in interesting[:CAP_SMALL_STRINGS]:
            out.append(f"    {line}")
        if full_strings_hook is not None:
            full_strings_hook(path, text)
    return out


def main(argv):
    root = argv[1] if len(argv) > 1 else None
    if root is None:
        # No argument: probe the folder this script sits in, if it looks
        # like a Visual Enhancer root (runtime/dlssnr/... present).
        here = os.path.dirname(os.path.abspath(__file__))
        if os.path.isfile(os.path.join(here, BIG_FILE[1])):
            root = here
        else:
            print(__doc__)
            return 2
    if os.path.isfile(root):  # convenience: probe one dll directly
        targets = [(os.path.basename(root), root)]
        small = targets
        big = None
    else:
        small = [(label, os.path.join(root, rel))
                 for label, rel in SMALL_FILES]
        big = (BIG_FILE[0], os.path.join(root, BIG_FILE[1]))

    report = []
    report.append("ANTs reference-host probe - what the proven C++ NGX host does")
    report.append(f"source: {root}")

    full_sink = []

    def sink(path, text):
        full_sink.append((path, text))

    for label, path in small:
        report += probe_file(label, path, full_strings_hook=sink)
    if big:
        report += probe_file(big[0], big[1], big=True)

    out_path = os.path.join(os.getcwd(),
                            "probe_neuroframe_host_report.txt")
    with open(out_path, "w", encoding="utf-8", errors="replace") as fh:
        fh.write("\n".join(report) + "\n")
        for path, text in full_sink:
            fh.write(f"\n\n==== FULL STRINGS: {path} ====\n")
            fh.write("\n".join(text))
    text = "\n".join(report)
    print(text)
    print()
    print(f"[ANTs] full report (incl. complete bridge strings) written to:")
    print(f"       {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""Probe the owner rig for DLSS Super Resolution feasibility (read-only).

Run on the ComfyUI machine (Windows):
    python tools/probe_dlss_rig.py [path-to-ComfyUI]

Reports:
  1. NGX core (_nvngx.dll + nvngx.dll loader) - System32, then DriverStore
     (newest wins). This is part of the NVIDIA display driver; never shipped.
  2. Any nvngx_dlss*.dll (the SR/DLAA feature DLL with the J/K/L/M presets)
     under <ComfyUI>/models/DLSS/ - with version resources where available.
  3. The neuroframe engine's exports (frame-path / upscale capability) for
     every discovered dlssnr_* set.

Stdlib only; `pefile` (pip install pefile) unlocks version-resource details.
Nothing is loaded for execution except the engine export scan, which uses
pefile when present and never runs DLL code.
"""

import glob
import os
import sys

NGX_CORE = "_nvngx.dll"
NGX_LOADER = "nvngx.dll"
SR_DLL_HINTS = ("nvngx_dlss.dll",)          # SR/DLAA feature dll
NR_HINTS = ("nvngx_dlssnr.dll",)            # NR runtime (already in use)
ENGINE_HINTS = ("neuroframe_engine.dll", "dlss5nr_bridge.dll")

KNOWN_ENGINE_EXPORTS = (
    "dlss5nr_init", "dlss5nr_rebind", "dlss5nr_process_v6",
    "dlss5nr_process_cuda_v6", "dlss5nr_process_frame_v6",
    "dlss5nr_cuda_supported", "dlss5nr_cuda_status", "dlss5nr_gpu_name",
    "dlss5nr_scene_score_v1", "dlss5nr_surface_create",
    "dlss5nr_frame_abi_version", "dlss5nr_version", "dlss5nr_release_session",
)


def file_version(path):
    try:
        import pefile
    except ImportError:
        return "version info unavailable (pip install pefile for details)"
    try:
        pe = pefile.PE(path, fast_load=True)
    except Exception:
        return "no version resource"
    try:
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        for vs in getattr(pe, "FileInfo", []) or []:
            for entry in vs:
                if entry.Key == b"StringFileInfo":
                    for st in entry.StringTable:
                        get = lambda k: st.entries.get(k.encode(), b"?").decode(errors="ignore")
                        return f"{get('FileVersion')} (product: {get('ProductName')})"
    except Exception:
        pass
    return "no version resource"


def exports(path):
    try:
        import pefile
    except ImportError:
        return None
    try:
        pe = pefile.PE(path, fast_load=True)
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
        table = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
        if not table:
            return []
        return sorted((e.name or b"?").decode(errors="ignore")
                      for e in table.symbols)
    except Exception:
        return None


def find_ngx_core(diagnose=None):
    """Locate _nvngx.dll / nvngx.dll; when missing, `diagnose` (a list)
    receives the folders scanned so a rerun report is actionable."""
    hits = []
    system32 = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32")
    for name in (NGX_CORE, NGX_LOADER):
        p = os.path.join(system32, name)
        if os.path.isfile(p):
            hits.append(("System32", p))
    repo = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                        "System32", "DriverStore", "FileRepository")
    folders = sorted(glob.glob(os.path.join(repo, "nv*.inf_amd64_*")))
    if diagnose is not None:
        diagnose.append(f"scanned {len(folders)} DriverStore folder(s) under {repo}")
        for folder in folders[:5]:
            diagnose.append(f"  e.g. {os.path.basename(folder)}")
        if not folders:
            diagnose.append("  no nv*.inf_amd64_* folders - is the NVIDIA driver installed?")
    candidates = {}
    for folder in folders:
        for name in (NGX_CORE, NGX_LOADER):
            p = os.path.join(folder, name)
            if os.path.isfile(p):
                candidates.setdefault(name, []).append(p)
    for name, paths in candidates.items():
        paths.sort(key=os.path.getmtime, reverse=True)  # newest driver store first
        hits.append(("DriverStore (newest)", paths[0]))
        if len(paths) > 1:
            hits.append((f"DriverStore (+{len(paths) - 1} older)", paths[1]))
    return hits


def find_models_root(argv):
    """Locate ComfyUI's models/DLSS from argv or common layouts.

    Accepts, per argument: a ComfyUI root, a models folder, or the DLSS
    folder itself.
    """
    roots = []
    for arg in argv[1:]:
        arg = os.path.normpath(arg)
        direct = arg if os.path.basename(arg).lower() == "dlss" else None
        via_models = os.path.join(arg, "DLSS")
        via_root = os.path.join(arg, "models", "DLSS")
        for candidate in (direct, via_models, via_root):
            if candidate and os.path.isdir(candidate) and candidate not in roots:
                roots.append(candidate)
                break
    here = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    for marker in (os.path.join(os.pardir, os.pardir), "."):
        candidate = os.path.normpath(os.path.join(here, marker, "models", "DLSS"))
        if os.path.isdir(candidate) and candidate not in roots:
            roots.append(candidate)
    return roots


def dll_scan(folder, hints):
    found = []
    for root, _, files in os.walk(folder):
        for name in files:
            if name.lower() in hints or (name.lower().endswith(".dll")
                                         and "dlss" in name.lower()):
                found.append(os.path.join(root, name))
    return sorted(found)


def main():
    print("=== ANTs DLSS rig probe (read-only) ===\n")

    print("[1] NGX core (driver-shipped; SR hosting needs this):")
    diagnosis = []
    cores = find_ngx_core(diagnose=diagnosis)
    if not cores:
        print("    NOT FOUND - install/update the NVIDIA display driver")
        for line in diagnosis:
            print(f"    [scan] {line}")
    for where, path in cores:
        print(f"    {where}: {path}")
        print(f"      {file_version(path)}")

    print("\n[2] models/DLSS contents (user-supplied; never bundled):")
    roots = find_models_root(sys.argv)
    if not roots:
        print("    models/DLSS not found - pass ONE of these as the argument:")
        print(f"      the ComfyUI root :  python {sys.argv[0]} C:\\ComfyUI_PORTABLE\\ComfyUI")
        print(f"      or the DLSS dir  :  python {sys.argv[0]} C:\\ComfyUI_PORTABLE\\ComfyUI\\models\\DLSS")
    for root in roots:
        print(f"    root: {root}")
        for path in dll_scan(root, SR_DLL_HINTS + NR_HINTS + ENGINE_HINTS):
            name = os.path.basename(path)
            role = ("SR/DLAA feature (J/K/L/M presets)"
                    if name.lower() in SR_DLL_HINTS else
                    "NR runtime (in use by the current node)"
                    if name.lower() in NR_HINTS else
                    "engine/bridge helper" if name.lower() in ENGINE_HINTS else "other")
            print(f"    - {os.path.relpath(path, root)}  [{role}]")
            print(f"      {file_version(path)}")

    print("\n[3] neuroframe engine exports (frame-path/upscale capability):")
    for root in roots:
        for path in dll_scan(root, ENGINE_HINTS):
            if os.path.basename(path).lower() not in ENGINE_HINTS:
                continue
            names = exports(path)
            print(f"    - {os.path.relpath(path, root)}:")
            if names is None:
                print("      export scan needs pefile (pip install pefile)")
                continue
            known = [n for n in names if n in KNOWN_ENGINE_EXPORTS]
            extra = [n for n in names if n not in KNOWN_ENGINE_EXPORTS
                     and n.startswith("dlss5nr")]
            print(f"      known: {', '.join(known) or 'none'}")
            if extra:
                print(f"      additional dlss5nr exports: {', '.join(sorted(extra))}")
            if "dlss5nr_process_frame_v6" in names:
                print("      frame-descriptor path present: separate output dims "
                      "(NR-side upscaling) is worth probing")
            if "dlss5nr_process_cuda_v6" in names:
                print("      CUDA device-pointer path present (GPU acceleration OK)")

    print("\nVerdict hints:")
    print("  - SR/DLAA node needs [1] NGX core + nvngx_dlss.dll from [2].")
    print("  - nvngx_dlss.dll version tells you which preset letters actually exist.")
    print("  - Everything here is user-procured; nothing is auto-downloaded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""SR/FG DLL-set discovery for the pure-Python host (models/DLSS layout).

The DLSS5 NR node uses ``ants.dlssnr.discovery`` (NR sets). This module adds
the SR view the owner asked to test: **flat ``.dll`` files directly inside
``models/DLSS/SR/`` each appear as their own set** (named after the file),
so coexisting builds like ``nvngx_dlss.dll`` and
``nvngx_dlss_310.9.1.dll`` are individually selectable. Folder sets
(``SR/<version>/``) work as anywhere else. FG is reserved (future video
feature).
"""

import os

from ..dlssnr import discovery as _nr_discovery


def _dlss_root():
    # dynamic: respects runtime rebinding (tests, ComfyUI folder overrides)
    return _nr_discovery.DLSS_ROOT


def _dll_files(path):
    return _nr_discovery.dll_files(path)


def discover_sr_sets():
    """Selectable SR sets: [{"name", "path", "kind"}], kind = "dll" | "dir"."""
    sets = []
    sr_root = os.path.join(_dlss_root(), "SR")
    if not os.path.isdir(sr_root):
        return sets
    for entry in sorted(os.listdir(sr_root)):
        candidate = os.path.join(sr_root, entry)
        if os.path.isdir(candidate):
            if _dll_files(candidate):
                sets.append({"name": entry, "path": candidate, "kind": "dir"})
        elif entry.lower().endswith(".dll"):
            sets.append({"name": os.path.splitext(entry)[0], "path": candidate, "kind": "dll"})
    return sets


def sr_set_choices():
    sets = discover_sr_sets()
    return (["auto"] + [s["name"] for s in sets] + ["refresh"]) if sets else ["auto", "refresh"]


def resolve_sr_dll(choice):
    """Absolute path of the chosen nvngx_dlss*.dll (or the first found)."""
    sets = discover_sr_sets()
    if not sets:
        raise RuntimeError(
            "[ANTs] No DLSS SR dll set found. Place nvngx_dlss*.dll builds into\n"
            f"    {os.path.join(_dlss_root(), 'SR')}\\  (each flat .dll = its own set,\n"
            "    or one dll per SR/<version>/ subfolder).\n"
            "    The SR runtime (nvngx_dlss.dll) is user-procured - its redistribution\n"
            "    is prohibited by NVIDIA (DLSS Swapper / driver packages are sources).")
    if choice in ("auto", "refresh"):
        chosen = sets[0]
    else:
        chosen = next((s for s in sets if s["name"] == choice), None)
        if chosen is None:
            chosen = sets[0]
    if chosen["kind"] == "dll":
        return chosen["path"]
    dlls = [d for d in _dll_files(chosen["path"]) if d.lower().startswith("nvngx_dlss")]
    if not dlls:
        dlls = _dll_files(chosen["path"])
    return os.path.join(chosen["path"], dlls[0])


def find_nr_runtime_dll(nr_dir):
    """The nvngx_dlssnr*.dll inside a chosen NR set (any filename suffix)."""
    for f in _dll_files(nr_dir):
        if f.lower().startswith("nvngx_dlssnr"):
            return os.path.join(nr_dir, f)
    raise RuntimeError(
        "[ANTs] The chosen NR set folder contains no nvngx_dlssnr*.dll:\n"
        f"    {nr_dir}\n"
        "    Rule 1 of the layout guide: every NR set must contain the NR runtime "
        "(any filename starting with 'nvngx_dlssnr'). See ants/dlssnr/dll_README.md.")


def stage_sr_dll(dll_path):
    """A SEARCH-PATH DIR for the chosen SR dll.

    The NGX core looks for the literal file name "nvngx_dlss.dll" inside the
    search paths, so a build under any other name (nvngx_dlss_310.9.1.dll,
    ...) is copied to a writable staging dir under that name. Returns the
    directory to hand to NgxSession(search_paths=[...]). The copy is refreshed
    when the source file changes (size mismatch)."""
    import shutil
    dll_path = os.path.abspath(dll_path)
    if os.path.basename(dll_path).lower() == "nvngx_dlss.dll":
        return os.path.dirname(dll_path)
    from .ngx import writable_cache_dir
    staged_dir = os.path.join(
        writable_cache_dir("sr_staged"),
        os.path.splitext(os.path.basename(dll_path))[0])
    os.makedirs(staged_dir, exist_ok=True)
    target = os.path.join(staged_dir, "nvngx_dlss.dll")
    if (not os.path.isfile(target)
            or os.path.getsize(target) != os.path.getsize(dll_path)):
        shutil.copyfile(dll_path, target)
    return staged_dir

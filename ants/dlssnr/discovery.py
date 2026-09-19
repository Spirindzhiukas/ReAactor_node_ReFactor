"""DLSS-NR DLL set discovery (models/DLSS layout, any filenames).

The DLLs themselves (the neuroframe helper pair and/or NVIDIA's
``nvngx_dlssnr.dll``) are and remain **3rd-party, manually installed** files —
this nodepack never downloads them (NVIDIA's license prohibits redistributing
``nvngx_dlssnr.dll``; the neuroframe bridge belongs to its authors).

Owner-decided layout (DLLs are models, so they live with the models):

1. ``ComfyUI/models/DLSS/dlssnr_<version_name>/`` — user sets, one folder per
   version. **Any .dll filenames are accepted**: the engine is identified by
   probing its exports at load time, not by file name (OreX's single-file
   practice works too — whatever the folder contains is the set).
2. ``ComfyUI/models/DLSS/`` (flat .dll files)              — one unnamed set
3. ``ComfyUI/models/dlssnr/<version>/`` + flat              — legacy fallback
4. ``.../custom_nodes/<this>/ants/dlssnr/dll``           — legacy package dir
"""

import os

from .. import model_paths
from ..log import logger

_REQUIRED_HINTS = ("nvngx_dlssnr.dll",)  # name HINTS only - never required by name
LEGACY_DLSSNR_PATH = model_paths.DLSSNR_MODELS_PATH
DLSS_ROOT = model_paths.DLSS_MODELS_PATH

PACKAGE_DLL_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "dll")


def dll_files(path: str):
    """All .dll filenames in a directory (any naming)."""
    if not os.path.isdir(path):
        return []
    return sorted(f for f in os.listdir(path) if f.lower().endswith(".dll"))


def validate_set(path: str):
    """Return a description of a candidate DLL dir; None if it has no DLLs.

    'complete' means a non-empty set of DLLs (engine identity is verified by
    export probing at load time - filenames are deliberately irrelevant).
    'has_nvngx' is informational: the NGX runtime is usually recognizable by
    name and the engine needs it next to itself at load time.
    """
    dlls = dll_files(path)
    if not dlls:
        return None
    return {
        "path": path,
        "complete": True,
        "missing": [],
        "dlls": dlls,
        "has_nvngx": any("nvngx" in d.lower() for d in dlls),
    }


def discover_dll_sets():
    """List usable DLL sets: [{"name", "path", "complete", "missing", ...}]."""
    found = []
    seen = set()

    def add(label, candidate):
        if os.path.realpath(candidate) in seen:
            return
        info = validate_set(candidate)
        if info:
            found.append({"name": label, **info})
            seen.add(os.path.realpath(candidate))

    # 1. models/DLSS/dlssnr_<version_name>/  (enforced location)
    if os.path.isdir(DLSS_ROOT):
        for entry in sorted(os.listdir(DLSS_ROOT)):
            candidate = os.path.join(DLSS_ROOT, entry)
            if os.path.isdir(candidate):
                add(entry, candidate)
        # 2. flat DLLs directly in models/DLSS
        add("(models/DLSS)", DLSS_ROOT)

    # 3-4. legacy locations, kept as graceful fallbacks
    if os.path.isdir(LEGACY_DLSSNR_PATH):
        for entry in sorted(os.listdir(LEGACY_DLSSNR_PATH)):
            candidate = os.path.join(LEGACY_DLSSNR_PATH, entry)
            if os.path.isdir(candidate):
                add(f"{entry} (legacy models/dlssnr)", candidate)
        add("(legacy models/dlssnr)", LEGACY_DLSSNR_PATH)
    add("built-in (package)", PACKAGE_DLL_DIR)

    return found


def default_dll_dir():
    sets = discover_dll_sets()
    if not sets:
        raise RuntimeError(
            "[ANTs] No DLSS-NR DLL set found.\n"
            "    Place the 3rd-party DLLs into\n"
            f"    {os.path.join(DLSS_ROOT, 'dlssnr_<version_name>')}/\n"
            "    ANY .dll filenames are accepted (the engine is found by its exports,\n"
            "    not by name). That folder must contain the bridge/helper DLLs and the\n"
            "    NVIDIA nvngx_dlssnr.dll runtime (whose public redistribution is\n"
            "    prohibited - obtain it yourself). Sources: ants/dlssnr/dll_README.md"
        )
    return sets[0]["path"]


def resolve_dll_dir(choice: str):
    """Map a combo choice ('auto' / a discovered name / 'refresh') to a dir."""
    sets = discover_dll_sets()
    if not sets:
        return default_dll_dir()
    if choice in ("auto", "refresh"):
        chosen = sets[0]
    else:
        chosen = next((s for s in sets if s["name"] == choice), None)
        if chosen is None:
            logger.warning(f"DLSS-NR DLL set '{choice}' no longer exists, falling back to auto.")
            chosen = sets[0]
    return chosen["path"]


def combo_choices():
    names = [s["name"] for s in discover_dll_sets()]
    return (["auto"] + names + ["refresh"]) if names else ["auto", "refresh"]

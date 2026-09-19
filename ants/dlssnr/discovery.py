"""DLSS-NR DLL set discovery (models/DLSS layout, any filenames).

The DLLs themselves (NR runtime, SR runtime, neuroframe helpers, ...) are and
remain **3rd-party, manually installed** files — this nodepack never downloads
them (NVIDIA's license prohibits redistributing the nvngx_dlss* runtimes; the
neuroframe bridge belongs to its authors).

Owner-decided layout (DLLs are models, so they live with the models), one
folder per version under a category:

1. ``ComfyUI/models/DLSS/NR/<version_name>/``    — Neural Rendering sets
   (the DLSS5 enhancer's ``dll_version`` selector). **Any .dll filenames are
   accepted**: the engine is identified by probing its exports at load time,
   not by file name.
2. ``ComfyUI/models/DLSS/SR/<version_name>/``    — Super Resolution sets
   (``nvngx_dlss.dll`` builds; reserved for the upcoming SR feature).
3. ``ComfyUI/models/DLSS/FG/<version_name>/``    — Frame Generation sets
   (reserved for a future video feature).
4. ``ComfyUI/models/DLSS/dlssnr_<version>/``     — legacy convention, still
   scanned (category NR).
5. ``ComfyUI/models/DLSS/`` (flat .dll files)    — one unnamed set, labeled
   after the NV NR runtime found inside (e.g. ``(models/DLSS -
   nvngx_dlssnr_RenoDX_4000_series_friendly)``) so mixed flat folders stay
   identifiable in the selector.
6. ``ComfyUI/models/dlssnr/<version>/`` + flat   — legacy fallback
7. ``.../custom_nodes/<this>/ants/dlssnr/dll``   — legacy package dir
"""

import os

from .. import model_paths
from ..log import logger

LEGACY_DLSSNR_PATH = model_paths.DLSSNR_MODELS_PATH
DLSS_ROOT = model_paths.DLSS_MODELS_PATH

# category subfolders (owner layout): name -> human description
CATEGORY_SUBFOLDERS = {"NR": "Neural Rendering", "SR": "Super Resolution", "FG": "Frame Generation"}


def _is_helper_dir(name: str) -> bool:
    """The 3rd-party helper stash (neuroframe pair masters): models/DLSS/HELPERS/
    (recommended) or any folder whose name starts with 'merserk' / 'hlp'
    (apostrophes/hyphens ignored, so the owner's "Merserk's_DLLS" works)."""
    n = name.lower().replace("'", "").replace("-", "_")
    return n.startswith(("helpers", "hlp", "merserk"))


def _has_nr_runtime(files) -> bool:
    return any(f.lower().startswith("nvngx_dlssnr") for f in files)


def helper_dll_dirs():
    """Folders that may hold the neuroframe helper pair (search order)."""
    dirs = []
    if os.path.isdir(DLSS_ROOT):
        for entry in sorted(os.listdir(DLSS_ROOT)):
            candidate = os.path.join(DLSS_ROOT, entry)
            if os.path.isdir(candidate) and _is_helper_dir(entry):
                dirs.append(candidate)
    dirs.append(PACKAGE_DLL_DIR)
    return dirs

PACKAGE_DLL_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "dll")


def dll_files(path: str):
    """All .dll filenames in a directory (any naming)."""
    if not os.path.isdir(path):
        return []
    return sorted(f for f in os.listdir(path) if f.lower().endswith(".dll"))


def _nr_runtime_label(files):
    """Human label of the NV NR runtime inside a set (for flat folders)."""
    for f in files:
        if f.lower().startswith("nvngx_dlssnr"):
            return os.path.splitext(f)[0]
    return None


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


def discover_dll_sets(category=None):
    """List usable DLL sets.

    category: "NR" | "SR" | "FG" | None. NR selectors see NR-tagged and
    uncategorized (legacy/flat) sets; pass an explicit category for the
    SR/FG selectors (future nodes); None returns everything.
    Each set: {"name", "path", "complete", "missing", "dlls", "has_nvngx",
    "category"}.
    """
    found = []
    seen = set()

    def add(label, candidate, cat):
        if os.path.realpath(candidate) in seen:
            return
        info = validate_set(candidate)
        if info:
            found.append({"name": label, "category": cat, **info})
            seen.add(os.path.realpath(candidate))

    if os.path.isdir(DLSS_ROOT):
        # 1. category subfolders: models/DLSS/<NR|SR|FG>/<version>/
        for cat_name in CATEGORY_SUBFOLDERS:
            cat_dir = os.path.join(DLSS_ROOT, cat_name)
            if not os.path.isdir(cat_dir):
                continue
            for entry in sorted(os.listdir(cat_dir)):
                candidate = os.path.join(cat_dir, entry)
                if os.path.isdir(candidate):
                    add(entry, candidate, cat_name)
            # flat DLLs directly inside the category folder
            add(f"({cat_name})", cat_dir, cat_name)

        # 2. legacy convention: models/DLSS/dlssnr_<version>/
        for entry in sorted(os.listdir(DLSS_ROOT)):
            candidate = os.path.join(DLSS_ROOT, entry)
            if os.path.isdir(candidate) and entry.lower().startswith("dlssnr"):
                add(entry, candidate, "NR")

        # 3. generic subfolders: with an NR runtime -> uncategorized (NR
        #    selector); helper-only stash -> category HLP; anything else ->
        #    category OTHER. The flat files in models/DLSS itself stay
        #    uncategorized (the owner's current live layout).
        for entry in sorted(os.listdir(DLSS_ROOT)):
            candidate = os.path.join(DLSS_ROOT, entry)
            if os.path.isdir(candidate) and entry not in CATEGORY_SUBFOLDERS \
                    and not entry.lower().startswith("dlssnr") \
                    and not _is_helper_dir(entry):
                files = dll_files(candidate)
                cat = None if _has_nr_runtime(files) else "OTHER"
                label = entry if _nr_runtime_label(files) is None \
                    else f"{entry} - {_nr_runtime_label(files)}"
                add(label, candidate, cat)
        flat = dll_files(DLSS_ROOT)
        if flat:
            nr = _nr_runtime_label(flat)
            label = "(models/DLSS)" if nr is None else f"(models/DLSS - {nr})"
            add(label, DLSS_ROOT, None)

    # 6-7. legacy locations, kept as graceful fallbacks (category NR)
    if os.path.isdir(LEGACY_DLSSNR_PATH):
        for entry in sorted(os.listdir(LEGACY_DLSSNR_PATH)):
            candidate = os.path.join(LEGACY_DLSSNR_PATH, entry)
            if os.path.isdir(candidate):
                add(f"{entry} (legacy models/dlssnr)", candidate, "NR")
        add("(legacy models/dlssnr)", LEGACY_DLSSNR_PATH, "NR")
    add("built-in (package)", PACKAGE_DLL_DIR, "NR")

    if category is None:
        return found
    return [s for s in found if s["category"] in (category, None)]


def default_dll_dir():
    sets = discover_dll_sets("NR")
    if not sets:
        raise RuntimeError(
            "[ANTs] No DLSS-NR DLL set found.\n"
            "    Place the 3rd-party DLLs into\n"
            f"    {os.path.join(DLSS_ROOT, 'NR', 'dlssnr_<version_name>')}/\n"
            "    (legacy locations models/DLSS/dlssnr_<version_name>/ and flat\n"
            "    models/DLSS are still scanned).\n"
            "    ANY .dll filenames are accepted (the engine is found by its exports,\n"
            "    not by name). That folder must contain the bridge/helper DLLs and the\n"
            "    NVIDIA nvngx_dlssnr.dll runtime (whose public redistribution is\n"
            "    prohibited - obtain it yourself). Sources: ants/dlssnr/dll_README.md"
        )
    return sets[0]["path"]


def resolve_dll_dir(choice: str, category: str = "NR"):
    """Map a combo choice ('auto' / a discovered name / 'refresh') to a dir."""
    sets = discover_dll_sets(category)
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


def combo_choices(category: str = "NR"):
    names = [s["name"] for s in discover_dll_sets(category)]
    return (["auto"] + names + ["refresh"]) if names else ["auto", "refresh"]

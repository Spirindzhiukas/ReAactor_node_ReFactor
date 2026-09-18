"""DLSS-NR DLL set discovery.

The DLLs themselves (``neuroframe_caller.dll``, ``neuroframe_engine.dll``,
``nvngx_dlssnr.dll``) are and remain **3rd-party, manually installed** files —
this nodepack never downloads them (NVIDIA's license prohibits redistributing
``nvngx_dlssnr.dll``; the neuroframe bridge belongs to its author).

What is added on top of upstream: users can keep **several independent DLL
versions** side by side and switch between them per workflow.

Scanned locations (in order):
1. ``ComfyUI/models/dlssnr/<version_name>/``   — user sets, one folder per version
2. ``ComfyUI/models/dlssnr/`` (flat files)     — a single unnamed user set
3. ``.../custom_nodes/<this>/rfactor/dlssnr/dll`` — legacy built-in flat folder
  (kept for compatibility with the upstream manual-install instructions;
   also accepts DLLs dropped next to the old ``r_dlssnr/dll`` path)
"""

import os

from .. import model_paths
from ..log import logger

_ENGINE_DLL = "neuroframe_engine.dll"
_REQUIRED_DLLS = ("neuroframe_caller.dll", "neuroframe_engine.dll", "nvngx_dlssnr.dll")

PACKAGE_DLL_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "dll")


def validate_set(path: str):
    """Return a description of a candidate DLL dir; None if it can't work."""
    if not os.path.isdir(path):
        return None
    present = {f.lower() for f in os.listdir(path) if f.lower().endswith(".dll")}
    if not present:
        return None
    missing = [d for d in _REQUIRED_DLLS if d not in present]
    return {"path": path, "complete": not missing, "missing": missing}


def discover_dll_sets():
    """List usable DLL sets: [{"name": label, "path": dir, "complete": bool, "missing": [...]}]."""
    found = []
    seen = set()

    root = model_paths.DLSSNR_MODELS_PATH
    if os.path.isdir(root):
        for entry in sorted(os.listdir(root)):
            candidate = os.path.join(root, entry)
            if os.path.isdir(candidate):
                info = validate_set(candidate)
                if info:
                    found.append({"name": entry, **info})
                    seen.add(os.path.realpath(candidate))

    info = validate_set(root)
    if info and os.path.realpath(root) not in seen:
        found.append({"name": "(models/dlssnr)", **info})
        seen.add(os.path.realpath(root))

    info = validate_set(PACKAGE_DLL_DIR)
    if info:
        found.insert(0, {"name": "built-in (package)", **info})

    return found


def default_dll_dir():
    sets = discover_dll_sets()
    complete = [s for s in sets if s["complete"]]
    chosen = (complete or sets or [None])[0]
    if chosen is None:
        raise RuntimeError(
            "[ReFactor] No DLSS-NR DLL set found.\n"
            f"    Place the 3rd-party DLLs ({', '.join(_REQUIRED_DLLS)}) into\n"
            f"    {model_paths.DLSSNR_MODELS_PATH}<version_name>/\n"
            "    See rfactor/dlssnr/dll_README.md for sources and licensing."
        )
    if not chosen["complete"]:
        logger.warning(
            f"DLSS-NR DLL set '{chosen['name']}' is incomplete, missing: {chosen['missing']}. "
            "The node will most likely fail to initialize."
        )
    return chosen["path"]


def resolve_dll_dir(choice: str):
    """Map a combo choice ('auto' / a discovered name / 'refresh') to a dir."""
    sets = discover_dll_sets()
    if not sets:
        return default_dll_dir()
    if choice == "auto" or choice == "refresh" or choice == "(models/dlssnr)":
        complete = [s for s in sets if s["complete"]]
        chosen = (complete or sets)[0]
    else:
        chosen = next((s for s in sets if s["name"] == choice), None)
        if chosen is None:
            logger.warning(f"DLSS-NR DLL set '{choice}' no longer exists, falling back to auto.")
            chosen = sets[0]
    return chosen["path"]


def combo_choices():
    names = [s["name"] for s in discover_dll_sets()]
    return (["auto"] + names + ["refresh"]) if names else ["auto", "refresh"]

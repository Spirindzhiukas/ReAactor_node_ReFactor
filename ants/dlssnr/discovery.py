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
    """Folders that hold the neuroframe helper pair, best candidate FIRST.

    A folder only counts when it actually CONTAINS .dll files - the owner's
    tree has an empty ``HELPERS/`` folder, and an empty stash must never win
    over the populated ``Merserk_DLLS/`` (that would silently fall back to
    the old "copy everything next to the runtime" behaviour).
    """
    dirs = []
    if os.path.isdir(DLSS_ROOT):
        for entry in sorted(os.listdir(DLSS_ROOT)):
            candidate = os.path.join(DLSS_ROOT, entry)
            if os.path.isdir(candidate) and _is_helper_dir(entry) \
                    and dll_files(candidate):
                dirs.append(candidate)
    # Merserk_DLLS wins over HELPERS/HLP* even when both are populated: the
    # owner's ruling is that the pair has exactly ONE home.
    dirs.sort(key=lambda d: (0 if "merserk" in os.path.basename(d).lower()
                             else 1, os.path.basename(d).lower()))
    if dll_files(PACKAGE_DLL_DIR):
        dirs.append(PACKAGE_DLL_DIR)
    return dirs


def helper_stash_dir():
    """The one folder the helper pair is expected to live in (or None).

    ``models/DLSS/Merserk_DLLS/`` in the owner's tree; any ``Merserk*`` /
    ``HELPERS`` / ``HLP*`` folder works, and the package ``dll/`` folder is
    the last resort. This is what makes the per-category copies unnecessary.
    """
    dirs = helper_dll_dirs()
    return dirs[0] if dirs else None

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


def _is_helper_dll_name(name: str):
    """True for the neuroframe helper pair and friends (never an NR runtime)."""
    lower = name.lower()
    if lower.startswith("nvngx"):
        return False
    return "neuroframe" in lower or "merserk" in lower


def category_entries(category: str):
    """Selector entries for one category: **each flat .dll directly inside
    models/DLSS/<category>/ is its own entry** (named after the file - the
    owner's duplicate-dll requirement, e.g. nvngx_dlss.dll AND
    nvngx_dlss_310.9.1.dll listed separately), plus one entry per
    <version>/ subfolder. [{"name", "path", "kind": "dll"|"dir"}]."""
    root = os.path.join(DLSS_ROOT, category)
    out = []
    if os.path.isdir(root):
        for entry in sorted(os.listdir(root)):
            candidate = os.path.join(root, entry)
            if os.path.isdir(candidate):
                if dll_files(candidate):
                    out.append({"name": entry, "path": candidate, "kind": "dir"})
            elif entry.lower().endswith(".dll"):
                out.append({"name": entry, "path": candidate, "kind": "dll"})
    return out


def category_choices(category: str):
    """Combo values for a per-category selector: 'auto' + entry names.

    No 'refresh' entry - the DLSS5 node's dedicated refresh button re-reads
    models/DLSS via object_info."""
    return ["auto"] + [e["name"] for e in category_entries(category)]


# Rig-proven (runs 14-19): RenoDX-derived NR builds force-terminate the
# whole process inside their first EvaluateFeature on OUR pure-Python
# provider - no exception is raised (a first-in-process vectored handler
# sees nothing) and no NGX log is written (runs 14-19). CAVEAT (run 20):
# Merserk's plain C++ host runs the SAME build fine, so the earlier
# "these builds need ReShade" theory is DISPROVEN - the gap is in our
# provider and is under active analysis (rig probes + his engine's
# binaries). Until that closes we treat these builds as session-loss
# risks: 'auto' selection SKIPS them (a queue run must not die), and an
# EXPLICIT pick is honored with a loud warning (owner consent).
# Provenance/credit: the RenoDX project (clshortfuse) and the community
# "4000 series friendly" repack.
KNOWN_FORCE_TERMINATOR_MARKERS = ("renodx",)


def is_known_force_terminator(dll_path):
    """True when the dll filename matches a rig-proven force-terminator."""
    name = os.path.basename(str(dll_path)).lower()
    return any(marker in name for marker in KNOWN_FORCE_TERMINATOR_MARKERS)


def resolve_nr_runtime_path(choice: str, skip_known_bad: bool = False):
    """The NR runtime .dll for the native host: a chosen flat dll directly,
    a chosen set's nvngx_dlssnr*.dll, or (auto/vanished) the first found.

    skip_known_bad=True (native engine, auto selection) passes over
    rig-proven force-terminator builds so a queue run cannot lose the
    session; an EXPLICIT choice is always honored (the caller warns).
    """
    from ..dlsssr.discovery import find_nr_runtime_dll  # lazy: no import cycle
    for entry in category_entries("NR"):
        if choice and entry["name"] == choice:
            if entry["kind"] == "dll":
                return entry["path"]
            return find_nr_runtime_dll(entry["path"])
    # AUTO selection: only files that ARE an NR runtime count. The helper
    # pair (neuroframe_caller/engine) may sit in the same folder, and picking
    # "the first flat dll" used to hand the legacy engine a caller DLL as the
    # "runtime" (rig 2026-09-20 07:29: the stage was named
    # "neuroframe_caller-104960"). Runtimes are named nvngx_dlssnr* by every
    # producer; the export probe remains the final authority at load time.
    saw_bad = False
    helpers, fallback, set_errors = [], [], []
    for entry in category_entries("NR"):
        if entry["kind"] != "dll":
            continue
        if os.path.basename(entry["path"]).lower().startswith("nvngx_dlssnr"):
            if skip_known_bad and is_known_force_terminator(entry["path"]):
                saw_bad = True
                continue
            return entry["path"]
        if _is_helper_dll_name(entry["name"]):
            helpers.append(entry["name"])
        else:
            fallback.append(entry)
    for candidate in discover_dll_sets("NR"):
        try:
            path = find_nr_runtime_dll(candidate["path"])
        except RuntimeError as exc:
            set_errors.append(str(exc))   # e.g. helper DLLs only - keep looking
            continue
        if path and skip_known_bad and is_known_force_terminator(path):
            saw_bad = True
            continue
        if path:
            return path
    if saw_bad:
        raise RuntimeError(
            "[ANTs] Every NR build in models/DLSS/NR matches the rig-proven "
            "force-terminator list (RenoDX-derived builds that kill the whole "
            "process at the first NGX evaluate on a plain D3D12 host - runs "
            "14-19). Select one EXPLICITLY in the engine dropdown to accept "
            "the risk, or add a stock nvngx_dlssnr build (e.g. from DLSS "
            "Swapper) so 'auto' has a safe pick.")
    if fallback:
        logger.warning(
            "[ANTs] No nvngx_dlssnr* file in models/DLSS/NR - falling back to "
            "'%s' by guess: it is not named like an NR runtime, and the "
            "export probe before load is the final authority. Helper DLLs in "
            "that folder (found: %s) are never selected.",
            fallback[0]["name"], ", ".join(helpers) or "none")
        return fallback[0]["path"]
    if helpers:
        raise RuntimeError(
            "[ANTs] models/DLSS/NR contains helper DLL(s) only ("
            + ", ".join(helpers) + ")."
            + ((" " + " ".join(e.strip() for e in set_errors)) if set_errors else "")
            + " The neuroframe helper pair belongs in models/DLSS/Merserk_DLLS - "
            "it is not an NR runtime and is never loaded as one. Install an "
            f"nvngx_dlssnr build into {os.path.join(DLSS_ROOT, 'NR')} (any "
            "filename; the pack never downloads it because NVIDIA's licence "
            "makes it yours to procure).")
    # Nothing in the category folder: legacy roots, then the loud set error.
    dir_path = default_dll_dir()
    path = find_nr_runtime_dll(dir_path)
    if path:
        return path
    raise RuntimeError(
        "[ANTs] No neural-rendering runtime (anything exporting the DLSS-NR "
        f"entry points) found in '{dir_path}'. Install an nvngx_dlssnr build "
        "there - the pack never downloads one for you.")


def resolve_legacy_dir(choice: str):
    """Set DIR for the legacy helper engine (helpers sit next to the runtime)."""
    for entry in category_entries("NR"):
        if choice and entry["name"] == choice:
            return entry["path"] if entry["kind"] == "dir" else os.path.dirname(entry["path"])
    return resolve_dll_dir("auto")


def stage_nr_runtime(dll_path):
    """A dir with the CHOSEN NR runtime under its CANONICAL name.

    Both consumers need the canonical name: the legacy helper engine locates
    its files by literal name, and the snippet is only ever loaded as
    ``nvngx_dlssnr.dll`` by every host that works (the community hosts load
    it from their runtime folder by that exact name).

    - the chosen file already IS ``nvngx_dlssnr.dll`` -> its folder is used
      IN PLACE (nothing copied, nothing leaves models/DLSS);
    - a build under any other name (nvngx_dlssnr_RenoDX_..., ...) is staged
      under ``models/DLSS/staged/<dll_name>-<size>/nvngx_dlssnr.dll`` as a
      copy of the CHOSEN file (content-addressed: an existing stage is
      reused, so loaded DLL files are never rewritten and never get locked).

    Run 28 taught the important half of this rule the hard way: the owner's
    NR folder ALSO contains a file named ``nvngx_dlssnr.dll`` (a different
    build than the RenoDX one selected in the node), and the old rule
    ("this folder carries a canonical name -> use it") silently loaded THAT
    file instead of the selection - a confounded experiment. Only the
    chosen file is ever canonicalized now, and a sibling canonical file
    that is not the selection is called out loudly.
    """
    dll_path = os.path.abspath(dll_path)
    if os.path.isdir(dll_path):          # a set dir: canonicalise the runtime
        from ..dlsssr.discovery import find_nr_runtime_dll   # lazy: no cycle
        dll_path = find_nr_runtime_dll(dll_path)
    src_dir = os.path.dirname(dll_path)
    chosen = os.path.basename(dll_path)
    if chosen.lower() == "nvngx_dlssnr.dll":
        return src_dir  # already canonical - use exactly where it lives
    sibling = [f for f in dll_files(src_dir)
               if f.lower() == "nvngx_dlssnr.dll"]
    if sibling:
        logger.warning(
            "[ANTs] The NR folder also contains a file literally named "
            "nvngx_dlssnr.dll, but '%s' is the build you selected - staging "
            "the selected one and ignoring the sibling (run 28 loaded the "
            "sibling by mistake).", chosen)
    stage = os.path.join(DLSS_ROOT, "staged",
                         f"{os.path.splitext(chosen)[0]}"
                         f"-{os.path.getsize(dll_path)}")
    os.makedirs(stage, exist_ok=True)
    canonical = os.path.join(stage, "nvngx_dlssnr.dll")
    if not os.path.exists(canonical) or \
            os.path.getsize(canonical) != os.path.getsize(dll_path):
        import shutil
        shutil.copyfile(dll_path, canonical)

    # Only the runtime plus the HELPER PAIR belongs in the stage folder.
    #
    # What used to happen: every .dll next to the selected build was copied
    # in, so the stage of a legacy run ended up carrying a 158 MB NR build and
    # vice versa, and each category folder needed its own copy of the helper
    # pair to feed that loop. The pair has ONE home now
    # (models/DLSS/Merserk_DLLS by default); the stage stays self-contained
    # because the snippet loads its dependencies from its own directory.
    import shutil
    stash = helper_stash_dir()
    copied, kept = [], []
    if stash:
        for name in dll_files(stash):
            source = os.path.join(stash, name)
            target = os.path.join(stage, name)
            if os.path.exists(target):
                kept.append(name)
                continue
            try:
                shutil.copyfile(source, target)
                copied.append(name)
            except (PermissionError, OSError):
                pass  # a locked leftover from a previous run; not needed
        logger.status(
            "[ANTs] NR stage %s: runtime + helper pair from %s%s",
            os.path.basename(stage), stash,
            f" ({len(copied)} copied)" if copied else "")
    else:
        # Compat: no helper stash on disk, so the older layout (helpers next
        # to the runtime) is honoured - copy the siblings as before. Loud,
        # because that layout is what makes the models tree unmanageable.
        logger.warning(
            "[ANTs] No helper stash found (looked for models/DLSS/Merserk_DLLS, "
            "HELPERS, HLP* and the package dll folder) - falling back to "
            "copying every .dll next to the selected runtime into the stage. "
            "Move the neuroframe pair into models/DLSS/Merserk_DLLS once and "
            "that stops.")
        for name in dll_files(src_dir):
            target = os.path.join(stage, name)
            if os.path.exists(target):
                continue
            try:
                shutil.copyfile(os.path.join(src_dir, name), target)
            except (PermissionError, OSError):
                pass
    return stage


# Historical name (the legacy helper engine was the first consumer).
stage_legacy_runtime = stage_nr_runtime

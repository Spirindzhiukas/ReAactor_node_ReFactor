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

from . import versions
from ..dlssnr import discovery as _nr_discovery   # pack-local, lazy by design


class _FallbackLogger:
    """Plain output for runs without the pack's logging bootstrap (tests, the
    rig tools)."""

    def _emit(self, message, args):
        try:
            print((message % args) if args else message)
        except Exception:
            print(message)

    def status(self, message, *args):
        self._emit(message, args)

    def warning(self, message, *args):
        self._emit(message, args)


def logger_for():
    """The pack logger (ants.log), or a plain fallback - never fatal."""
    try:
        from ..log import dlss_logger
        return dlss_logger
    except Exception:
        return _FallbackLogger()


logger = logger_for()


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


# A real nvngx_dlss SR runtime is tens of MB; helper/caller stubs (e.g.
# neuroframe_caller.dll renamed or misplaced) are ~100 KB. Anything under
# this size is rejected as a masquerader, not an SR runtime.
SR_RUNTIME_MIN_BYTES = 1_000_000


def _is_sr_runtime(path):
    try:
        return os.path.getsize(path) >= SR_RUNTIME_MIN_BYTES
    except OSError:
        return False


def _sr_set_dll(chosen):
    """The NEWEST nvngx_dlss*.dll of a chosen set (the naming rule).

    A set folder can hold several builds (that is the point of the version
    subfolders); the version in the file name decides, not the list order -
    see ants/dlsssr/versions.py.
    """
    if chosen["kind"] == "dll":
        return chosen["path"]
    dlls = [d for d in _dll_files(chosen["path"]) if d.lower().startswith("nvngx_dlss")]
    if not dlls:
        dlls = _dll_files(chosen["path"])
    paths = [os.path.join(chosen["path"], d) for d in dlls]
    pick = versions.newest(paths)
    return pick if pick else paths[0]


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
        # THE BUILD NAMING RULE: newest by version in the file name, flat
        # files and version subfolders ranked together (ants/dlsssr/versions.py).
        ranked = versions.newest_first([_sr_set_dll(s) for s in sets])
        stubs = []
        for path in ranked:
            if _is_sr_runtime(path):
                if stubs:
                    logger.warning(
                        "[ANTs] SR auto skipped %d stub(s) under 1 MB (%s) and "
                        "picked %s (%s) - the pack never loads a helper/caller "
                        "stub as an SR runtime.",
                        len(stubs), ", ".join(os.path.basename(p) for p in stubs),
                        os.path.basename(path), versions.describe(
                            os.path.basename(path)))
                newer_unversioned = versions.unversioned_newer(ranked, path)
                if newer_unversioned:
                    logger.warning(
                        "[ANTs] SR auto picked '%s' (%s) by the naming rule, "
                        "but %s has a NEWER file date and no version in the "
                        "name - a name without a version always sorts last. "
                        "Rename that file with its version "
                        "(nvngx_dlss_<version>.dll) if you want auto to load it.",
                        os.path.basename(path),
                        versions.describe(os.path.basename(path)),
                        ", ".join(newer_unversioned))
                return path
            stubs.append(path)
        raise RuntimeError(
            "[ANTs] No DLSS SR dll set found. Place nvngx_dlss*.dll builds into\n"
            f"    {os.path.join(_dlss_root(), 'SR')}\\\n"
            "    (every candidate there was under 1 MB - helper/caller stubs are\n"
            "    NOT SR runtimes.)")
    chosen = next((s for s in sets if s["name"] == choice), None)
    if chosen is None:
        # The widget is stale (files changed since the node was created):
        # fall back to the NEWEST build per the naming rule, not to whichever
        # entry happens to be first.
        picked_path = versions.newest([_sr_set_dll(s) for s in sets])
        chosen = next((s for s in sets
                       if os.path.normcase(_sr_set_dll(s))
                       == os.path.normcase(picked_path or "")), sets[0])
        logger.warning(
            "[ANTs] The selected SR build '%s' is gone from models/DLSS/SR - "
            "using the newest build '%s' instead (press refresh after "
            "changing DLL files).", choice, chosen["name"])
    path = _sr_set_dll(chosen)
    if not _is_sr_runtime(path):
        raise RuntimeError(
            f"[ANTs] '{os.path.basename(path)}' is under 1 MB - it is not an SR "
            "runtime but a helper/caller stub. Pick the real nvngx_dlss*.dll build "
            "(tens of MB) in the SR selector.")
    return path


def find_nr_runtime_dll(nr_dir):
    """The NEWEST nvngx_dlssnr*.dll inside a chosen NR set (any suffix).

    A set folder may hold several builds; the naming rule decides which one
    is 'the' runtime of that set (ants/dlsssr/versions.py).
    """
    found = [os.path.join(nr_dir, f) for f in _dll_files(nr_dir)
             if f.lower().startswith("nvngx_dlssnr")]
    if found:
        return versions.newest(found)
    raise RuntimeError(
        "[ANTs] The chosen NR set folder contains no nvngx_dlssnr*.dll:\n"
        f"    {nr_dir}\n"
        "    Rule 1 of the layout guide: every NR set must contain the NR runtime "
        "(any filename starting with 'nvngx_dlssnr'). See ants/dlssnr/dll_README.md.")


def stage_nr_runtime(dll_path):
    """Staging dir holding the chosen NR build as ``nvngx_dlssnr.dll``.

    The snippet is loaded from there: see ants.dlssnr.discovery.stage_nr_runtime
    for why the canonical name matters.
    """
    return _nr_discovery.stage_nr_runtime(dll_path)


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


_SR_CHOICE = {"value": "auto"}
_SR_STAGE_LOGGED = {"done": False}


def remember_sr_choice(choice):
    """The process's SR selector value (the newest node construction wins).

    The FIRST NGX init in a process fixes the core's feature-library search
    paths (rig 02:48 + Claude Sonnet 5), so the NR stage has to know the SR
    build the owner actually selected BEFORE that init - and the selection
    lives in a node widget. This is the one place that carries it across
    stages.
    """
    _SR_CHOICE["value"] = choice or "auto"


def ensure_staged_sr_dir():
    """The staged folder of the SELECTED SR build, staged on demand.

    Called by the search-path union before every NGX init, so the process's
    first init can already list ``nvngx_dlss.dll``. None when no SR build can
    be resolved/staged - logged once per process at status level (an owner
    without an SR build must not see a warning on every prompt; the SR stage
    itself fails loudly when it runs).
    """
    try:
        return stage_sr_dll(resolve_sr_dll(_SR_CHOICE["value"]))
    except Exception as exc:
        if not _SR_STAGE_LOGGED["done"]:
            _SR_STAGE_LOGGED["done"] = True
            logger.status(
                "[ANTs] SR build not staged for the NGX search-path union: %s "
                "(only matters if an SR stage runs this session)",
                str(exc).splitlines()[0])
        return None

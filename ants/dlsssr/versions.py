"""THE BUILD NAMING RULE — how the pack decides which DLL is the newest.

Owner directive (2026-09-20): put a DATE or a VERSION into the file name and
the node loads the newest build it can find, unless a specific build is picked
in the ``dll_version`` widget. This module is that rule; it is deliberately
shared by the NR and the SR/ FG selectors so both speak the same language.

Naming scheme
-------------

    models/DLSS/NR/nvngx_dlssnr_<version>[_<tag>].dll
    models/DLSS/SR/nvngx_dlss_<version>[_<tag>].dll
    models/DLSS/FG/nvngx_dlssg_<version>[_<tag>].dll

``<version>`` is either

* a **date** — ``2026-09-14``, ``2026_09_14`` or ``20260914`` (preferred for
  the community NR builds, which have no official numbering), or
* a **dotted build number** — ``310.9.1``, ``310.9``, ``v10.0`` (NVIDIA's own
  numbering, used by the driver-supplied SR/FG runtimes), or
* a single number — ``2`` (accepted, lowest priority).

Everything after the version is free text and never affects ordering
(``_renodx4000``, ``_RenoDX_4000_series_friendly``, ``_merserk``, ...), and
GPU/hardware tags (``4000``, ``3090``, ``series``, ``friendly``, ...) are
never mistaken for a version. Ordering between schemes: a DATE outranks a
dotted number, which outranks a bare number, which outranks a name with no
version at all (an unversioned name is ordered by file modification time).

Why this exists: the owner keeps several builds of the same runtime on disk
to test the picker (the rig audit found `nvngx_dlssnr.dll` and
`nvngx_dlssnr_RenoDX_4000_series_friendly.dll` to be the SAME 165,830,144-byte
build — two names, one build). A name is not proof of anything, so:

* **identification is still export-based** — the loader probes exports and
  refuses a file that is not a runtime whatever its name says;
* the version rule only ORDERS the candidates, and every automatic pick is
  logged with the version it read out of the name, so nothing is a mystery.

Related rules: ``docs/MODELS_DLSS_LAYOUT.md`` (paths + keep/delete),
``CLAUDE.md`` (standing rules), ``ants/dlssnr/discovery.py`` (the NR picker).
"""

import os
import re

# Numbers that are hardware/vendor tags, never build versions. Kept as
# strings because they are compared against digit tokens.
GPU_TAGS = frozenset({
    "1000", "1600", "2000", "2050", "2060", "2070", "2080", "2080ti",
    "3000", "3050", "3060", "3070", "3080", "3090", "3090ti",
    "4000", "4050", "4060", "4070", "4080", "4090",
    "5000", "5050", "5060", "5070", "5080", "5090",
    "1660", "1650", "1630", "1080", "1070", "1060", "1050",
})

RANK_NONE, RANK_NUMBER, RANK_DOTTED, RANK_DATE = -1, 0, 1, 2

_DATE_RX = re.compile(r"(?<![0-9])(20\d{2})[-_.]?([0-9]{1,2})[-_.]?([0-9]{1,2})(?![0-9])")
_DOTTED_RX = re.compile(r"^v?[0-9]{1,4}(?:\.[0-9]{1,4}){1,3}$")
_BARE_RX = re.compile(r"^v?[0-9]{1,6}$")
_TOKEN_RX = re.compile(r"[^0-9A-Za-z.]+")


def _valid_date(year, month, day):
    return 1 <= month <= 12 and 1 <= day <= 31 and 2000 <= year <= 2999


def build_version(name):
    """Comparable version tuple ``(rank, *numbers)`` for a file/folder name.

    ``(RANK_NONE,)`` when the name carries no readable version — which sorts
    below every versioned name on purpose. The tuples are compared directly
    (Python compares elementwise), so ``(2, 2026, 9, 14)`` > ``(1, 310, 9, 1)``
    > ``(0, 2)`` > ``(-1,)``.
    """
    stem = os.path.splitext(os.path.basename(str(name)))[0].lower()

    for match in _DATE_RX.finditer(stem):
        year, month, day = (int(part) for part in match.groups())
        if _valid_date(year, month, day):
            return (RANK_DATE, year, month, day)

    tokens = [t for t in _TOKEN_RX.split(stem) if t]
    for token in tokens:
        if _DOTTED_RX.match(token) and token.lstrip("v") not in GPU_TAGS:
            return (RANK_DOTTED,) + tuple(int(p) for p in token.lstrip("v").split("."))

    for token in tokens:
        if token.isdigit() and token not in GPU_TAGS and len(token) <= 6:
            return (RANK_NUMBER, int(token))
        if _BARE_RX.match(token) and token.lstrip("v") not in GPU_TAGS:
            return (RANK_NUMBER, int(token.lstrip("v")))

    return (RANK_NONE,)


def describe(name):
    """Human-readable version, for logs and the rig report."""
    version = build_version(name)
    if version[0] == RANK_DATE:
        return "date %04d-%02d-%02d" % version[1:4]
    if version[0] == RANK_DOTTED:
        return "version " + ".".join(str(p) for p in version[1:])
    if version[0] == RANK_NUMBER:
        return "build number %d" % version[1]
    return "no version in the name (ordered by file date)"


def order_key(path):
    """Sort key: version, then modification time, then name (all ascending)."""
    name = os.path.basename(str(path))
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return (build_version(name), mtime, name.lower())


def newest_first(paths):
    """Paths ordered NEWEST FIRST (version, then file date, then name)."""
    return sorted(paths, key=order_key, reverse=True)


def newest(paths):
    """The newest path, or None for an empty list."""
    ranked = newest_first(paths)
    return ranked[0] if ranked else None


def newest_name(names):
    """Newest name out of plain name strings (for selector ordering)."""
    return newest_first(list(names))


def unversioned_newer(paths, pick):
    """Unversioned candidates whose FILE DATE is newer than the picked build.

    The rule is "the name decides", so `nvngx_dlss.dll` (a stock drop-in with
    no version in its name) sorts below every versioned name even when it is
    the freshest file on disk. That is easy to trip over, so both pickers warn
    with this list instead of silently loading an older build.
    """
    names = []
    try:
        picked_time = os.path.getmtime(pick)
    except OSError:
        return names
    for path in paths:
        name = os.path.basename(str(path))
        if build_version(name)[0] != RANK_NONE:
            continue
        try:
            if os.path.getmtime(path) > picked_time:
                names.append(name)
        except OSError:
            continue
    return names


def is_newest(path, paths):
    """True when `path` is the newest candidate of `paths`."""
    return bool(paths) and os.path.normcase(os.path.abspath(str(path))) == \
        os.path.normcase(os.path.abspath(str(newest(paths))))

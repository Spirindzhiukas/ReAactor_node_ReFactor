"""Collect everything a rig run leaves behind, into ONE folder - READ-ONLY.

Why this exists: every rig run so far needed a second message ("send the
console") or a third ("also send the NGX log"). The artefacts that matter are
scattered across the ComfyUI tree, and two of them are easy to forget:

  * ``models/DLSS/staged/ANTs/appdata/logs/nvngx.log`` - the NGX core's own
    log. It is the only instrument that survives a hard kill of the node, and
    it is what identified the 0xBAD00000 snippet loader refusal;
  * the deployment marker ``HOST_BUILD`` in the DEPLOYED ``ngx.py`` - run 28
    attempt #1 was void because an older file than we believed was running.

Nothing here writes to ComfyUI, to the models tree, or to the staging folder.
Files are only READ and copied into the output folder this tool creates
(``tools/rig_evidence/<timestamp>/`` unless ``--out`` says otherwise).
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import sys
import time

MAX_COPY_BYTES = 32 * 1024 * 1024
HOST_BUILD_RX = re.compile(r"""HOST_BUILD\s*=\s*["']([^"']+)["']""")


def sha256_head(path, limit=8):
    """First ``limit`` hex chars of the SHA-256 - enough to compare copies."""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()[:limit]
    except OSError as exc:
        return f"<unreadable: {exc.__class__.__name__}>"


def human(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size / 1:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def size_of(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return -1


def mtime_of(path):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S",
                             time.localtime(os.path.getmtime(path)))
    except OSError:
        return "?"


def find_out_root(comfy_root, repo):
    r"""Where the report folder goes: NEVER inside the checkout.

    GitHub Desktop watches the pack folder; a run dump that lands in
    ``tools/rig_evidence/`` is one careless "Commit all" away from a pull
    request. The rig keeps diagnostics next to ComfyUI instead:
    ``<portable>\NODE_CODING\RIG_EVIDENCE``.
    """
    candidates = [os.environ.get("ANTS_EVIDENCE_OUT", "")]
    bases = []
    if comfy_root:
        parent = os.path.dirname(os.path.abspath(comfy_root))
        bases += [parent, os.path.abspath(comfy_root)]
    portable = os.environ.get("COMFYUI_PORTABLE")
    if portable:
        bases.append(portable)
    bases.append(os.path.abspath(repo) if repo else "")
    for base in bases:
        if base:
            candidates.append(os.path.join(base, "NODE_CODING", "RIG_EVIDENCE"))
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":   # no A:/B: (floppy probing)
        candidates.append(f"{letter}:\\ComfyUI_PORTABLE\\NODE_CODING\\RIG_EVIDENCE")
    temp = os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir()
    candidates.append(os.path.join(temp, "ANTs_RIG_EVIDENCE"))
    for candidate in candidates:
        path = clean_path(candidate)
        if not path:
            continue
        try:
            os.makedirs(path, exist_ok=True)
            return path
        except OSError:
            continue                     # drive not present / not writable
    return os.path.join(temp, "ANTs_RIG_EVIDENCE")


def clean_path(value):
    """A path handed over by cmd can arrive mangled (a trailing backslash
    inside quotes escapes the quote - ``--repo "C:\"`` - and the argument
    parser then swallows the NEXT option into the value). Anything with a
    stray quote in it is not a path."""
    if not value:
        return ""
    value = value.strip().strip('"')
    if '"' in value:
        return ""
    return value


def find_comfy_root(explicit, repo):
    """<ComfyUI> root - the parent of custom_nodes, or a portable install."""
    candidates = [explicit, os.environ.get("ANTS_EVIDENCE_COMFY", "")]
    portable = os.environ.get("COMFYUI_PORTABLE")
    if portable:
        candidates.append(os.path.join(portable, "ComfyUI"))
        candidates.append(portable)
    here = os.path.realpath(repo) if repo else ""
    if here:
        # <comfy>/custom_nodes/<pack> -> <comfy>
        candidates.append(os.path.dirname(os.path.dirname(here)))
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":   # no A:/B: (floppy probing)
        candidates.append(f"{letter}:\\ComfyUI_PORTABLE\\ComfyUI")
        candidates.append(f"{letter}:\\ComfyUI")
    for candidate in candidates:
        if not candidate:
            continue
        if os.path.isdir(os.path.join(candidate, "models")):
            return os.path.abspath(candidate)
    return None


def find_pack(explicit, script_dir):
    """The nodepack checkout: a folder holding ants/dlsssr/ngx.py."""
    def looks_like_pack(path):
        return bool(path) and os.path.isfile(
            os.path.join(path, "ants", "dlsssr", "ngx.py"))

    candidates = [explicit, os.environ.get("ANTS_EVIDENCE_REPO", "")]
    candidates += [script_dir, os.path.dirname(script_dir), os.getcwd(),
                   os.path.dirname(os.getcwd())]
    portable = os.environ.get("COMFYUI_PORTABLE")
    if portable:
        for root in (os.path.join(portable, "ComfyUI"), portable):
            nodes = os.path.join(root, "custom_nodes")
            if os.path.isdir(nodes):
                for entry in sorted(os.listdir(nodes)):
                    candidates.append(os.path.join(nodes, entry))
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":   # no A:/B: (floppy probing)
        for root in (f"{letter}:\\ComfyUI_PORTABLE\\ComfyUI", f"{letter}:\\ComfyUI"):
            nodes = os.path.join(root, "custom_nodes")
            if os.path.isdir(nodes):
                for entry in sorted(os.listdir(nodes)):
                    candidates.append(os.path.join(nodes, entry))
    for candidate in candidates:
        if looks_like_pack(candidate):
            return os.path.abspath(candidate)
    return None


def find_dlss_root(explicit, repo):
    """Locate ``<ComfyUI>/models/DLSS`` without importing ComfyUI itself."""
    candidates = []
    if explicit:
        candidates.append(explicit)
    candidates.append(os.environ.get("ANTS_EVIDENCE_DLSS", ""))
    portable = os.environ.get("COMFYUI_PORTABLE")
    if portable:
        candidates.append(os.path.join(portable, "ComfyUI", "models", "DLSS"))
    try:                        # when run with ComfyUI's own embedded python
        import folder_paths     # type: ignore
        for root in list(folder_paths.get_folder_paths("models")):
            candidates.append(os.path.join(root, "DLSS"))
    except Exception:
        pass
    # the pack lives in <comfy>/custom_nodes/<name> (possibly via a symlink)
    here = os.path.realpath(repo)
    parent = os.path.dirname(os.path.dirname(here))
    candidates.append(os.path.join(parent, "models", "DLSS"))
    candidates.append(os.path.join(os.path.dirname(parent), "models", "DLSS"))
    candidates.append(os.path.join(here, "models", "DLSS"))
    for candidate in candidates:
        if candidate and os.path.isdir(candidate):
            return os.path.abspath(candidate)
    return None


def git_facts(repo):
    """(head, branch, dirty-lines) - all read-only; graceful when no git."""
    def run(*args):
        try:
            out = subprocess.run(["git", "-C", repo] + list(args),
                                 capture_output=True, text=True, timeout=30)
        except Exception as exc:
            return None, f"<git failed: {exc.__class__.__name__}>"
        if out.returncode != 0:
            return None, (out.stderr or out.stdout).strip().splitlines()[0] \
                if (out.stderr or out.stdout).strip() else "<git failed>"
        return out.stdout.strip(), None

    head, err = run("log", "-1", "--format=%h %cI %s")
    if err:
        return err, None, None
    branch, _ = run("rev-parse", "--abbrev-ref", "HEAD")
    dirty, _ = run("status", "--porcelain")
    return head, branch or "?", dirty or ""


def deployment_report(repo, dlss_root, out_lines):
    """The recurring failure: a DIFFERENT file than we think is running."""
    out_lines.append("DEPLOYMENT CHECK  (a run whose build marker is not the "
                     "newest commit is VOID - stop and re-sync)")
    copies = []
    here = os.path.realpath(repo)
    copies.append((os.path.join(here, "ants", "dlsssr", "ngx.py"), "repo copy"))
    if dlss_root:
        comfy = os.path.dirname(os.path.dirname(dlss_root))
        nodes = os.path.join(comfy, "custom_nodes")
        if os.path.isdir(nodes):
            for entry in sorted(os.listdir(nodes)):
                candidate = os.path.join(nodes, entry, "ants", "dlsssr", "ngx.py")
                if os.path.isfile(candidate):
                    copies.append((candidate, f"custom_nodes/{entry}"))
    seen = set()
    for path, label in copies:
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        if not os.path.isfile(path):
            out_lines.append(f"  MISSING ({label}): {path}")
            continue
        try:
            text = open(path, "r", encoding="utf-8", errors="replace").read()
        except OSError as exc:
            out_lines.append(f"  unreadable ({label}): {path} ({exc})")
            continue
        match = HOST_BUILD_RX.search(text)
        build = match.group(1) if match else "<no HOST_BUILD line>"
        out_lines.append(f"  {label}:")
        out_lines.append(f"    file : {path}")
        if real != os.path.abspath(path):
            out_lines.append(f"    real : {real}")
        out_lines.append(f"    build: {build}")
        out_lines.append(f"    sha256(first 8): {sha256_head(path)}  "
                         f"size {size_of(path)}  mtime {mtime_of(path)}")


def dll_inventory(root, out_lines, prefix=""):
    if not root or not os.path.isdir(root):
        out_lines.append("  (folder not found)")
        return []
    found = []
    listing = []
    try:
        for entry in sorted(os.listdir(root)):
            listing.append(entry)
    except OSError as exc:
        out_lines.append(f"  (cannot list {root}: {exc})")
        return []
    for entry in listing:
        path = os.path.join(root, entry)
        if os.path.isdir(path):
            out_lines.append(f"  {prefix}{entry}/")
            try:
                children = sorted(os.listdir(path))
            except OSError as exc:
                out_lines.append(f"    (cannot list: {exc})")
                continue
            for sub in children:
                spath = os.path.join(path, sub)
                if os.path.isdir(spath):
                    out_lines.append(f"    {prefix}{entry}/{sub}/")
                    continue
                if sub.lower().endswith(".dll"):
                    found.append(spath)
                    out_lines.append(
                        f"    {prefix}{entry}/{sub}  {size_of(spath)} bytes  "
                        f"mtime {mtime_of(spath)}  sha256(first 8) "
                        f"{sha256_head(spath)}")
        elif entry.lower().endswith(".dll"):
            found.append(path)
            out_lines.append(f"  {prefix}{entry}  {size_of(path)} bytes  "
                             f"mtime {mtime_of(path)}  sha256(first 8) "
                             f"{sha256_head(path)}")
    return found


def walk_logs(root, limit=64):
    """Every *.log under root, newest first, capped."""
    hits = []
    if not root or not os.path.isdir(root):
        return hits
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d.lower() not in ("__pycache__",)]
        for name in files:
            lower = name.lower()
            if lower.endswith(".log") or "crash" in lower:
                path = os.path.join(base, name)
                hits.append((mtime_of(path), path))
    hits.sort(reverse=True)
    return [path for _, path in hits[:limit]]


def copy_out(path, dest_dir, out_lines):
    if not os.path.isfile(path):
        return False
    size = size_of(path)
    name = os.path.basename(path)
    target = os.path.join(dest_dir, name)
    base, ext = os.path.splitext(name)
    counter = 1
    while os.path.exists(target):
        target = os.path.join(dest_dir, f"{base}__{counter}{ext}")
        counter += 1
    if size > MAX_COPY_BYTES:
        out_lines.append(f"  SKIPPED (too big, {human(size)}): {path}")
        return False
    try:
        shutil.copy2(path, target)
    except OSError as exc:
        out_lines.append(f"  COPY FAILED: {path} ({exc})")
        return False
    out_lines.append(f"  copied -> {os.path.basename(target)}  "
                     f"({size} bytes, {mtime_of(path)})")
    return True


def env_report(out_lines):
    keys = sorted(k for k in os.environ
                  if k.upper().startswith(("ANTS_", "NVSDK_", "DLSS5NR_",
                                           "CUDA_VISIBLE")))
    if not keys:
        out_lines.append("  (no ANTS_/NVSDK_/DLSS5NR_ variables set in THIS "
                         "window - the run's own env is only visible if this "
                         "bat is started from the same console)")
    for key in keys:
        out_lines.append(f"  {key}={os.environ[key]}")


def torch_report(out_lines):
    try:
        import torch
    except Exception as exc:
        out_lines.append(f"  torch not importable here ({exc.__class__.__name__})")
        return
    out_lines.append(f"  torch {torch.__version__}")
    try:
        available = torch.cuda.is_available()
        out_lines.append(f"  cuda available: {available}")
        if available:
            out_lines.append(f"  device: {torch.cuda.get_device_name(0)}")
            free, total = torch.cuda.mem_get_info()
            out_lines.append(f"  vram free/total: {human(free)} / {human(total)}")
    except Exception as exc:
        out_lines.append(f"  cuda query failed ({exc.__class__.__name__})")


def load_resolver():
    """The sibling offset resolver's PE reader (stdlib only)."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "resolve_crash_offset.py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location("ants_resolve_crash_offset",
                                                  path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


# case-insensitive: crash lines write KERNEL32.DLL, the module on disk is
# kernel32.dll (run 30's report: "KERNEL32.DLL+0x27799" matched nothing)
OFFSET_RX = re.compile(
    r"([A-Za-z0-9_.\\:$-]+?\.(?:dll|exe|pyd))\+(0x[0-9A-Fa-f]+)",
    re.IGNORECASE)


def module_search_dirs(comfy_root=""):
    """Where a bare module name from a crash line may live."""
    dirs = []
    windir = os.environ.get("WINDIR", r"C:\Windows")
    dirs.append(os.path.join(windir, "System32"))
    for prefix in {sys.prefix, getattr(sys, "base_prefix", ""), 
                   os.path.dirname(sys.executable)}:
        if prefix and prefix not in dirs:
            dirs.append(prefix)
            dirs.append(os.path.join(prefix, "DLLs"))
    if comfy_root:
        dirs.append(os.path.abspath(comfy_root))
    return [d for d in dirs if d and os.path.isdir(d)]


def resolve_offsets(files, out_lines, extra_dirs=()):
    """Name the function behind every MODULE+0xOFFSET in the crash logs.

    The black box reports faults as ``MODULE.DLL+0x<rva>``; the resolver next
    to this file already knows how to turn that into an export name. Doing it
    HERE means the report answers "what was the CPU executing" without a
    second round trip - the offsets are the whole point of sending it.
    """
    resolver = load_resolver()
    if resolver is None:
        out_lines.append("  (resolve_crash_offset.py not next to this tool - "
                         "offsets reported unresolved)")
        return
    seen = {}
    for path in files:
        try:
            text = open(path, "r", encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for module, offset in OFFSET_RX.findall(text):
            seen.setdefault((os.path.basename(module), int(offset, 16)), path)
    if not seen:
        out_lines.append("  (no MODULE+0xOFFSET lines in the collected logs)")
        return
    search = list(extra_dirs) or module_search_dirs()
    cache = {}
    for (module, offset), source in sorted(seen.items()):
        if True:
            path = module if os.path.isfile(module) else None
            if path is None:
                # a crash line may carry a full Windows path; split on both
                # separators so this behaves the same on any host
                leaf = module.replace("\\", "/").rsplit("/", 1)[-1]
                for folder in search:
                    candidate = os.path.join(folder, leaf)
                    if os.path.isfile(candidate):
                        path = candidate
                        break
                if path is None:
                    out_lines.append(f"  {module}+0x{offset:X}: module file "
                                     "not found (searched System32 and the "
                                     "python folder)")
                    continue
            if path not in cache:
                try:
                    cache[path] = resolver.export_rvas(path)
                except Exception as exc:
                    cache[path] = None
                    out_lines.append(f"  {module}+0x{offset:X}: cannot read "
                                     f"the export table ({exc})")
            entries = cache[path]
            if not entries:
                continue
            rvas = [rva for rva, _n in entries]
            idx = bisect.bisect_right(rvas, offset) - 1
            if idx < 0:
                out_lines.append(f"  {module}+0x{offset:X}: before the first "
                                 "named export (internal helper?)")
                continue
            rva, name = entries[idx]
            lo, hi = max(0, idx - 2), min(len(entries), idx + 3)
            near = " | ".join(f"{n}@0x{r:X}" for r, n in entries[lo:hi])
            out_lines.append(f"  {module}+0x{offset:X} -> {name} "
                             f"(RVA 0x{rva:X} +0x{offset - rva:X})  "
                             f"[from {os.path.basename(source)}]")
            out_lines.append(f"      neighborhood: {near}")


HELPER_HINTS = ("merserk", "helpers", "hlp")
HELPER_PAIR_HINTS = ("neuroframe", "caller", "engine")


def layout_audit(dlss_root, out_lines):
    """Read-only KEEP/DELETE audit of the models/DLSS tree.

    The owner asked for one thing: know what the code actually needs, so the
    hand-made duplicates can go. This prints exactly that - which paths the
    pack reads, which are duplicates of the helper pair, and which are the
    staging leftovers that rebuild themselves.
    """
    if not dlss_root or not os.path.isdir(dlss_root):
        out_lines.append("  (models/DLSS not found)")
        return
    lines_ = out_lines
    keep, delete, review = [], [], []

    def walk(folder, depth=0):
        for entry in sorted(os.listdir(folder)):
            path = os.path.join(folder, entry)
            if os.path.isdir(path):
                if entry.lower() == "staged":
                    delete.append(f"{path}  (staging area - rebuilt on demand; "
                                  "delete freely while ComfyUI is CLOSED)")
                    continue
                walk(path, depth + 1)
            elif entry.lower().endswith(".dll") and entry.lower() != "dlss_map.txt":
                keep_or_flag(path, entry)

    def keep_or_flag(path, name):
        lower = name.lower()
        parent = os.path.basename(os.path.dirname(path))
        in_stash = any(h in parent.lower().replace("'", "") for h in HELPER_HINTS)
        looks_helper = any(h in lower for h in HELPER_PAIR_HINTS) and \
            not lower.startswith("nvngx_")
        if looks_helper:
            if in_stash:
                keep.append(f"{path}  (helper pair - the ONE home)")
            else:
                delete.append(f"{path}  (helper duplicate; the pair belongs in "
                              "Merserk_DLLS only)")
            return
        if lower.startswith("nvngx_dlssnr"):
            keep.append(f"{path}  (NR runtime)")
        elif lower.startswith("nvngx_dlss"):
            keep.append(f"{path}  (SR/FG runtime)")
        else:
            review.append(f"{path}  (unknown dll - not named like a runtime)")

    walk(dlss_root)
    for name in sorted(os.listdir(dlss_root)):
        if name.lower().endswith(".txt"):
            review.append(f"{os.path.join(dlss_root, name)}  (not read by this "
                          "nodepack - keep as a reference or delete)")

    # Same-size files inside one category are usually the same build under two
    # names (the owner has nvngx_dlss.dll AND nvngx_dlss_310.9.1.dll). Only
    # the size collision is cheap to detect; the hash settles it.
    by_size = {}
    for row in keep:
        path = row.split("  (")[0]
        folder = os.path.dirname(path)
        try:
            by_size.setdefault((folder, os.path.getsize(path)), []).append(path)
        except OSError:
            pass
    for (folder, size), paths in sorted(by_size.items()):
        if len(paths) > 1:
            hashes = {sha256_head(p) for p in paths}
            verdict = ("identical content" if len(hashes) == 1
                       else "different content despite equal size")
            review.append(f"{' and '.join(os.path.basename(p) for p in paths)}"
                          f" in {folder} have the same size ({size} bytes): "
                          f"{verdict} - keep one if you do not need both")

    lines_.append("  KEEP (the pack reads these):")
    for row in keep or ["    (nothing)"]:
        lines_.append(f"    {row}")
    lines_.append("  SAFE TO DELETE (duplicates / staging leftovers):")
    for row in delete or ["    (nothing)"]:
        lines_.append(f"    {row}")
    if review:
        lines_.append("  YOUR CALL:")
        for row in review:
            lines_.append(f"    {row}")
    lines_.append("")
    lines_.append("  Expected layout (see docs/MODELS_DLSS_LAYOUT.md):")
    lines_.append("    models/DLSS/NR/            the NR builds you select")
    lines_.append("    models/DLSS/SR/            nvngx_dlss.dll builds (SR node)")
    lines_.append("    models/DLSS/FG/            nvngx_dlssg_*.dll (reserved)")
    lines_.append("    models/DLSS/Merserk_DLLS/  neuroframe_caller.dll + "
                  "neuroframe_engine.dll (ONLY here)")
    lines_.append("    models/DLSS/staged/        working copies this pack "
                  "creates (safe to delete when ComfyUI is closed)")


def nvidia_smi(out_lines):
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            for line in out.stdout.strip().splitlines():
                out_lines.append(f"  {line.strip()}")
        else:
            out_lines.append("  (nvidia-smi gave no answer)")
    except Exception:
        out_lines.append("  (nvidia-smi not on PATH)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default="",
                        help="the nodepack checkout (contains ants/); "
                             "auto-detected when omitted")
    parser.add_argument("--dlss-root", default="",
                        help="override: <ComfyUI>/models/DLSS")
    parser.add_argument("--out", default="",
                        help="output folder (default: tools/rig_evidence/<ts>)")
    parser.add_argument("--comfy-root", default="",
                        help="<ComfyUI> root, used to find custom_nodes copies")
    args = parser.parse_args(argv)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    notes = []
    raw_repo = args.repo or os.environ.get("ANTS_EVIDENCE_REPO", "")
    if raw_repo and clean_path(raw_repo) != raw_repo:
        notes.append(f"ignoring a mangled --repo value ({raw_repo!r}); "
                     "pass paths as environment variables instead")
    repo = find_pack(clean_path(raw_repo), script_dir)
    if repo is None:
        repo = clean_path(raw_repo) or os.getcwd()
        notes.append(f"NOT FOUND: the nodepack checkout (searched {script_dir} "
                     "and its parent, the cwd, and every custom_nodes folder "
                     "on every drive) - put this tool in the pack's tools\\ "
                     "folder, or set ANTS_EVIDENCE_REPO")
    comfy_root = find_comfy_root(clean_path(args.comfy_root), repo)
    dlss_root = find_dlss_root(clean_path(args.dlss_root), repo)
    if dlss_root is None and comfy_root:
        guess = os.path.join(comfy_root, "models", "DLSS")
        dlss_root = guess if os.path.isdir(guess) else None
    if dlss_root is None:
        notes.append("NOT FOUND: <ComfyUI>/models/DLSS (set ANTS_EVIDENCE_DLSS "
                     "or COMFYUI_PORTABLE if the install lives somewhere "
                     "unusual)")

    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    out_root = clean_path(args.out) or find_out_root(comfy_root, repo)
    inside_repo = bool(repo) and os.path.abspath(out_root).lower().startswith(
        os.path.abspath(repo).lower())
    if inside_repo:
        notes.append("the output folder is INSIDE the pack - GitHub Desktop "
                     "can pick it up; set ANTS_EVIDENCE_OUT to somewhere "
                     "outside the checkout")
    out_dir = os.path.join(out_root, stamp)
    logs_dir = os.path.join(out_dir, "files")
    try:
        os.makedirs(logs_dir, exist_ok=True)
    except OSError as exc:
        print(f"[X] cannot create {logs_dir}: {exc}")
        print("    set ANTS_EVIDENCE_REPO to the pack folder, or run this "
              "from a folder you can write to")
        return 1

    lines = []
    lines.append("=" * 74)
    lines.append("[ANTs] RIG EVIDENCE - collected READ-ONLY, nothing was "
                 "modified")
    lines.append(f"collected   : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"python      : {sys.version.split()[0]}  ({sys.executable})")
    lines.append(f"cwd         : {os.getcwd()}")
    lines.append(f"nodepack    : {repo}")
    lines.append(f"comfy root  : {comfy_root or '<not found>'}")
    lines.append(f"models/DLSS : {dlss_root or '<NOT FOUND>'}")
    if notes:
        lines.append("-" * 74)
        for note in notes:
            lines.append(f"[!] {note}")
    lines.append("=" * 74)

    lines.append("")
    lines.append("--- GIT (read-only) " + "-" * 55)
    head, branch, dirty = git_facts(repo)
    if branch is None:
        lines.append(f"  {head}")
    else:
        lines.append(f"  HEAD   : {head}")
        lines.append(f"  branch : {branch}")
        changed = [ln for ln in (dirty or "").splitlines() if ln.strip()]
        lines.append(f"  uncommitted/untracked entries: {len(changed)}")
        for entry in changed[:20]:
            lines.append(f"    {entry}")

    lines.append("")
    lines.append("--- " + "-" * 66)
    deployment_report(repo, dlss_root, lines)

    lines.append("")
    lines.append("--- MODELS/DLSS TREE (top level) " + "-" * 40)
    if dlss_root:
        for entry in sorted(os.listdir(dlss_root)):
            path = os.path.join(dlss_root, entry)
            if os.path.isdir(path):
                lines.append(f"  {entry}/")
            else:
                lines.append(f"  {entry}  {size_of(path)} bytes")
    else:
        lines.append("  (not found)")

    lines.append("")
    lines.append("--- RUNTIME INVENTORY " + "-" * 50)
    for label, sub in (("staged", "staged"), ("selected runtime folders", "")):
        if not dlss_root or not sub:
            continue
        lines.append(f"  [{label}] {os.path.join(dlss_root, sub)}")
        dll_inventory(os.path.join(dlss_root, sub), lines, prefix="  ")
    if dlss_root and not os.path.isdir(os.path.join(dlss_root, "staged")):
        lines.append("  (no staged/ folder - the run never staged a runtime)")

    lines.append("")
    lines.append("--- MODELS/DLSS LAYOUT AUDIT (keep / delete) " + "-" * 32)
    layout_audit(dlss_root, lines)

    lines.append("")
    lines.append("--- LOGS (copied into ./files) " + "-" * 41)
    log_sources = []
    if dlss_root:
        log_sources.append(os.path.join(dlss_root, "staged"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        log_sources.append(os.path.join(local, "NVIDIA", "NGX"))
    if args.comfy_root:
        log_sources.append(os.path.join(args.comfy_root, "user"))
    copied = 0
    # ComfyUI's own console capture, wherever the portable install keeps it
    # (top level only - the tree below that is huge and holds no logs).
    extra_logs = []
    for folder in (args.comfy_root,
                   os.path.dirname(args.comfy_root) if args.comfy_root else ""):
        if folder and os.path.isdir(folder):
            try:
                extra_logs += [os.path.join(folder, name)
                               for name in sorted(os.listdir(folder))
                               if name.lower().endswith(".log")]
            except OSError:
                pass
    for source in log_sources:
        lines.append(f"  scanning {source}")
        for path in walk_logs(source):
            copied += 1 if copy_out(path, logs_dir, lines) else 0
    lines.append("  scanning top-level *.log of the ComfyUI folders")
    for path in extra_logs:
        copied += 1 if copy_out(path, logs_dir, lines) else 0
    if not copied:
        lines.append("  (nothing copied - the node may never have reached NGX "
                     "init, or the logs sit outside the scanned folders)")

    lines.append("")
    lines.append("--- CRASH BLACK BOX (native-crash.log, last 60 lines) " + "-" * 20)
    crash_files = [os.path.join(logs_dir, name)
                   for name in sorted(os.listdir(logs_dir))
                   if "crash" in name.lower()]
    if not crash_files:
        lines.append("  (no crash file was collected - the black box writes to "
                     "staged/ANTs/appdata/logs/native-crash.log)")
    for path in crash_files:
        lines.append(f"  --- {os.path.basename(path)} ---")
        try:
            text = open(path, "r", encoding="utf-8", errors="replace").read()
        except OSError as exc:
            lines.append(f"  (unreadable: {exc})")
            continue
        rows = text.splitlines()
        # The file appends across runs; the newest session header marks the
        # run we care about (stale lines above it fooled us once already).
        starts = [i for i, row in enumerate(rows)
                  if "crash black box: session" in row]
        session = rows[starts[-1]:] if starts else rows[-60:]
        if not starts:
            lines.append("  [!] no session header in the file - written by an "
                         "older build; only the tail is shown")
        for row in session[-60:]:
            lines.append(f"  {row}")

    lines.append("")
    lines.append("--- CRASH OFFSETS RESOLVED (module+0xrva -> export) " + "-" * 20)
    copied_files = [os.path.join(logs_dir, name)
                    for name in sorted(os.listdir(logs_dir))]
    search = module_search_dirs(comfy_root)
    if dlss_root:
        staged = os.path.join(dlss_root, "staged")
        for entry in (sorted(os.listdir(staged)) if os.path.isdir(staged) else []):
            folder = os.path.join(staged, entry)
            if os.path.isdir(folder):
                search.append(folder)
    resolve_offsets(copied_files, lines, extra_dirs=search)

    lines.append("")
    lines.append("--- ENVIRONMENT (this window) " + "-" * 45)
    env_report(lines)

    lines.append("")
    lines.append("--- GPU " + "-" * 65)
    nvidia_smi(lines)

    lines.append("")
    lines.append("--- TORCH / CUDA " + "-" * 56)
    torch_report(lines)

    lines.append("")
    lines.append("--- HOW TO SEND " + "-" * 58)
    lines.append("  Send this whole text file. The 'files/' folder next to it")
    lines.append("  holds the raw logs; nvngx.log is the NGX core's own log")
    lines.append("  (the only instrument that survives a hard node kill).")
    lines.append("")

    report = os.path.join(out_dir, "rig_evidence.txt")
    with open(report, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write("\n".join(lines))

    print("\n".join(lines))
    # machine-readable markers the bat parses (folder to open in Explorer)
    print(f"[ANTs] OUTDIR={out_dir}")
    print(f"[ANTs] report  : {report}")
    print(f"[ANTs] raw logs: {logs_dir}")
    if any(note.startswith("NOT FOUND") for note in notes):
        print("[X] the report is incomplete - fix the NOT FOUND lines above "
              "and run again (the report is still worth sending: it says what "
              "was searched)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

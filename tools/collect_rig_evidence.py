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
import hashlib
import os
import re
import shutil
import subprocess
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


def find_dlss_root(explicit, repo):
    """Locate ``<ComfyUI>/models/DLSS`` without importing ComfyUI itself."""
    candidates = []
    if explicit:
        candidates.append(explicit)
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
    parser.add_argument("--repo", default=os.getcwd(),
                        help="the nodepack checkout (contains ants/)")
    parser.add_argument("--dlss-root", default="",
                        help="override: <ComfyUI>/models/DLSS")
    parser.add_argument("--out", default="",
                        help="output folder (default: tools/rig_evidence/<ts>)")
    parser.add_argument("--comfy-root", default="",
                        help="<ComfyUI> root, used to find custom_nodes copies")
    args = parser.parse_args(argv)

    repo = os.path.abspath(args.repo)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    out_dir = args.out or os.path.join(repo, "tools", "rig_evidence", stamp)
    logs_dir = os.path.join(out_dir, "files")
    os.makedirs(logs_dir, exist_ok=True)

    dlss_root = find_dlss_root(args.dlss_root, repo)
    if dlss_root is None and args.comfy_root:
        guess = os.path.join(args.comfy_root, "models", "DLSS")
        dlss_root = guess if os.path.isdir(guess) else None

    lines = []
    lines.append("=" * 74)
    lines.append("[ANTs] RIG EVIDENCE - collected READ-ONLY, nothing was "
                 "modified")
    lines.append(f"collected   : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"python      : {sys.version.split()[0]}  ({sys.executable})")
    lines.append(f"cwd         : {os.getcwd()}")
    lines.append(f"nodepack    : {repo}")
    lines.append(f"models/DLSS : {dlss_root or '<NOT FOUND - pass --dlss-root>'}")
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
    print(f"[ANTs] report  : {report}")
    print(f"[ANTs] raw logs: {logs_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

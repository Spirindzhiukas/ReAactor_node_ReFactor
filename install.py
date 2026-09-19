"""ReActor ReFactor — safe, transparent dependency bootstrap.

Replaces the upstream install script that pip-installed packages behind the
user's back (with broken version-string comparisons and forced
``onnxruntime-gpu`` upgrades from an extra index — a guaranteed source of
environment clashes with other custom nodes).

Principles
----------
1. **Never** upgrade or downgrade anything that already works.
   Torch / numpy / opencv / onnxruntime are snapshotted first; if an action
   would change any of them, it is refused with a clear message instead.
2. Flavor-aware onnxruntime handling: if ANY flavor (``onnxruntime`` or
   ``onnxruntime-gpu``) is already installed, nothing is installed.
3. PEP-440 comparisons only (never lexicographic string compares).
4. Explicit control:

       python install.py             # report + install only what is missing
       python install.py --dry-run   # show what would happen, change nothing
       python install.py --skip-deps # models only, no pip at all
       python install.py --ort-gpu   # prefer installing onnxruntime-gpu when
                                     # neither flavor is present (default: CPU)
       python install.py --no-models # don't download inswapper_128.onnx
       python install.py --yes       # don't ask for confirmation

5. Environment switches honored at all times:
       REFACTOR_SKIP_INSTALL=1       # make this script a no-op report
       REFACTOR_NO_AUTO_DOWNLOAD=1   # never download model files
"""

import argparse
import importlib.metadata as importlib_metadata
import os
import subprocess
import sys

REPO_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, REPO_DIR)  # only for the ants modules used below (this is a script, not a package import)


# --------------------------------------------------------------------- utils

def log(msg=""):
    print(f"[ReFactor] {msg}")


def get_version(distribution: str):
    try:
        return importlib_metadata.version(distribution)
    except importlib_metadata.PackageNotFoundError:
        return None


def is_importable(module: str) -> bool:
    try:
        __import__(module)
        return True
    except Exception:
        return False


def parse_pep440(version: str):
    """Best-effort PEP-440 parse without a hard 'packaging' dependency."""
    try:
        from packaging.version import parse

        return parse(version)
    except Exception:
        pass
    import re

    nums = re.findall(r"\d+", version or "0")
    return tuple(int(n) for n in nums[:3]) if nums else (0,)


def pip_install(args, dry_run: bool):
    cmd = [sys.executable, "-m", "pip", "install", "--no-warn-script-location"] + args
    if dry_run:
        log(f"DRY-RUN would run: {' '.join(cmd)}")
        return True
    log(f"Running: {' '.join(cmd)}")
    return subprocess.run(cmd).returncode == 0


def ask_continue(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


# ------------------------------------------------------------ environment ops

def snapshot(keepers):
    return {dist: get_version(dist) for dist in keepers}


def verify_snapshot(before, label):
    changed = {d: (before[d], get_version(d)) for d in before if before[d] != get_version(d)}
    if changed:
        log("WARNING: the following package versions changed during install — "
            "this should never happen, please report it:")
        for dist, (old, new) in changed.items():
            log(f"    {dist}: {old} -> {new}")
    return not changed


def torch_info():
    try:
        import torch

        cuda = torch.version.cuda if torch.cuda.is_available() else None
        return torch.__version__, cuda
    except Exception:
        return None, None


# ----------------------------------------------------------------- main flow

def main() -> int:
    parser = argparse.ArgumentParser(description="ReActor ReFactor dependency bootstrap")
    parser.add_argument("--dry-run", action="store_true", help="report only; change nothing")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--skip-deps", action="store_true", help="do not touch pip packages at all")
    parser.add_argument("--no-models", action="store_true", help="do not download inswapper_128.onnx")
    parser.add_argument("--ort-gpu", action="store_true",
                        help="when no onnxruntime flavor is installed, install onnxruntime-gpu "
                             "(default: CPU build; you can always switch manually later)")
    args = parser.parse_args()

    if os.environ.get("REFACTOR_SKIP_INSTALL", "").strip().lower() in ("1", "true", "yes", "on"):
        log("REFACTOR_SKIP_INSTALL is set — skipping all install actions.")
        return 0

    log("=" * 62)
    log("ReActor ReFactor — dependency check")
    log("=" * 62)

    py = sys.version_info
    log(f"Python: {py.major}.{py.minor}.{py.micro}")
    if (py.major, py.minor) < (3, 10):
        log("WARNING: Python below 3.10 is not supported by modern ComfyUI; continuing anyway.")
    torch_v, cuda_v = torch_info()
    log(f"PyTorch: {torch_v or 'NOT INSTALLED'}" + (f" (CUDA {cuda_v})" if cuda_v else ""))

    # We must never move the packages other nodes depend on.
    keepers = ["torch", "numpy", "pillow", "opencv-python", "opencv-python-headless",
               "opencv-contrib-python", "onnxruntime", "onnxruntime-gpu"]
    before = snapshot(keepers)

    plans = []  # (description, action)
    ok = True

    if not args.skip_deps:
        # ---- onnx (pure-python, clash-free; needed to read swap-model emaps)
        if get_version("onnx") is None:
            plans.append(("install 'onnx' (pure-python ONNX model reader)",
                          lambda: pip_install(["onnx>=1.14.0"], args.dry_run)))
        else:
            log(f"onnx {get_version('onnx')} — OK")

        # ---- opencv (flavor-aware: skip entirely if cv2 is importable)
        if is_importable("cv2"):
            found = get_version("opencv-python") or get_version("opencv-python-headless") \
                    or get_version("opencv-contrib-python") or get_version("opencv-contrib-python-headless")
            log(f"cv2 already importable ({found or 'unknown flavor'}) — not installing opencv")
        else:
            plans.append(("install 'opencv-python' (cv2 is missing entirely)",
                          lambda: pip_install(["opencv-python>=4.8.0"], args.dry_run)))

        # ---- onnxruntime (THE clash-sensitive one)
        ort_cpu = get_version("onnxruntime")
        ort_gpu = get_version("onnxruntime-gpu")
        if ort_cpu or ort_gpu:
            log(f"onnxruntime already present "
                f"({ 'onnxruntime ' + ort_cpu if ort_cpu else '' }"
                f"{ ' | ' if ort_cpu and ort_gpu else '' }"
                f"{ 'onnxruntime-gpu ' + ort_gpu if ort_gpu else '' }) — left untouched")
        else:
            pkg = "onnxruntime-gpu" if args.ort_gpu else "onnxruntime"
            plans.append((f"install '{pkg}' (no onnxruntime flavor present; "
                          f"you can install the other flavor manually at any time)",
                          lambda: pip_install([pkg], args.dry_run)))

    # ---- model files (plain data from pinned HTTPS URLs; opt-out supported)
    if not args.no_models and os.environ.get("REFACTOR_NO_AUTO_DOWNLOAD", "") not in ("1", "true"):
        from ants.download import safe_download

        model_url = "https://huggingface.co/datasets/Gourieff/ReActor/resolve/main/models/inswapper_128.onnx"
        try:
            import folder_paths

            base = folder_paths.models_dir
        except Exception:
            base = os.path.join(os.path.dirname(REPO_DIR), "..", "models")
        dest = os.path.join(base, "insightface", "inswapper_128.onnx")
        if os.path.exists(dest):
            log(f"inswapper_128.onnx — present ({dest})")
        else:
            plans.append((f"download inswapper_128.onnx ({dest})",
                          lambda: safe_download(model_url, dest, "inswapper_128.onnx",
                                                min_bytes=500 * 1024 * 1024 - 1) and True))
    else:
        log("Model download skipped (flag/env). Get inswapper_128.onnx from "
            "https://huggingface.co/datasets/Gourieff/ReActor/tree/main/models "
            "into models/insightface/ if you don't have it yet.")

    if not plans:
        log("Nothing to do — environment already satisfies the nodepack.")
        verify_snapshot(before, "final")
        return 0

    log("")
    log("Planned actions:")
    for i, (desc, _) in enumerate(plans, 1):
        log(f"  {i}. {desc}")
    log("")

    if not args.dry_run and not ask_continue("Proceed?", args.yes):
        log("Aborted by user. Nothing was changed.")
        log("You can always re-run this later, or install the pieces manually:")
        log("    pip install onnx opencv-python onnxruntime")
        return 1

    for desc, action in plans:
        log(f"- {desc}")
        try:
            if not action():
                ok = False
                log(f"  FAILED: {desc}")
        except Exception as e:
            ok = False
            log(f"  FAILED: {desc} ({e})")

    verify_snapshot(before, "final")

    if ok:
        log("Done. Enjoy!")
    else:
        log("Finished with errors — see the log above. The nodepack will still load; "
            "only the nodes that need the missing pieces will report how to fix them.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

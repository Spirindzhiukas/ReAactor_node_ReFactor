"""Tests for the DLSS-NR hybrid: HDR Colour Bridge math + models/DLSS discovery.

Usage: python tests/test_dlssnr_bridge.py
"""

import os
import sys
import tempfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

from smoke_import import install_stubs  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name}")


def main():
    install_stubs()
    sys.path.insert(0, str(REPO))

    from ants.dlssnr.hdr_bridge import (
        anchored_bridge,
        apply_bridge,
        classic_bridge,
        classic_bridge as cb,
    )
    from ants.dlssnr import discovery

    from ants.dlssnr.hdr_bridge import (
        DIFFUSE_WHITE_NITS_DEFAULT,
        PAPER_WHITE_SCALE_DEFAULT,
    )
    check("defaults: owner-tuned 220 nits / 1.0 scale",
          DIFFUSE_WHITE_NITS_DEFAULT == 220.0 and PAPER_WHITE_SCALE_DEFAULT == 1.0)

    rng = np.random.RandomState(7)
    frame = rng.rand(64, 64, 3).astype(np.float32) * 0.8
    check("classic: neutral at owner defaults (gain 1, knee 1 => identity)",
          np.allclose(classic_bridge(frame), frame, atol=1e-5))

    # ---- Classic bridge ----
    out = classic_bridge(frame)
    check("classic: finite [0,1] output", out.shape == frame.shape and np.isfinite(out).all()
          and out.min() >= 0 and out.max() <= 1)
    check("classic: zero transfer strength = pass-through",
          np.allclose(classic_bridge(frame, transfer_strength=0.0), frame))
    check("classic: paper-white gain brightens midtones",
          classic_bridge(frame, paper_white_scale=2.537).mean() > frame.mean())
    check("classic: shoulder keeps highlights <= 1", classic_bridge(frame * 0 + 1.0).max() <= 1.0)
    check("classic: diffuse white scales the knee",
          not np.allclose(classic_bridge(frame, diffuse_white_nits=120.0),
                          classic_bridge(frame, diffuse_white_nits=400.0)))
    check("classic: color strength changes chroma",
          not np.allclose(classic_bridge(frame, color_strength=0.2),
                          classic_bridge(frame, color_strength=1.8)))
    check("classic: transfer strength 0.5 sits between 0 and 1",
          abs(classic_bridge(frame, transfer_strength=0.5).mean()
              - (frame.mean() + classic_bridge(frame).mean()) / 2) < 0.05)

    # ---- Anchored bridge ----
    out_a = anchored_bridge(frame)
    check("anchored: finite [0,1] output", np.isfinite(out_a).all() and out_a.max() <= 1.0)
    check("anchored: pass-through at zero strength",
          np.allclose(anchored_bridge(frame, transfer_strength=0.0), frame))
    dark = (frame * 0.05).astype(np.float32)
    check("anchored: dark frame with lever 0 == classic (same knee, nothing measured above it)",
          np.allclose(anchored_bridge(dark, black_lever=0.0), cb(dark), atol=2e-3))
    # bright frame + paper-white gain 2: classic's fixed knee saturates every
    # gain-lifted highlight at the sRGB ceiling; the anchored knee rides up
    # with the measured highlight, so far fewer pixels clip
    bright = frame.copy()
    bright[:3, :] = 1.0
    clipped = lambda img: int((img >= 1.0 - 1e-6).sum())
    check("anchored: highlight-anchored knee clips fewer highlight pixels than classic",
          clipped(anchored_bridge(bright, paper_white_scale=2.0, black_lever=0.0))
          < clipped(cb(bright, paper_white_scale=2.0)))
    bl0 = anchored_bridge(frame, paper_white_scale=2.0, black_lever=0.0)
    bl1 = anchored_bridge(frame, paper_white_scale=2.0, black_lever=1.0)
    shadow_mask = frame.mean(axis=2) < 0.1
    check("anchored: black lever restores shadows (dark pixels closer to input)",
          abs(bl1[..., 0][shadow_mask].mean() - frame[..., 0][shadow_mask].mean())
          < abs(bl0[..., 0][shadow_mask].mean() - frame[..., 0][shadow_mask].mean()))

    # ---- GPU acceleration decision (pure) ----
    from ants.dlssnr.node import GPU_AUTO, GPU_FORCE, GPU_OFF, decide_cuda_acceleration
    ok, _ = decide_cuda_acceleration(GPU_AUTO, True, True)
    check("cuda decision: auto + torch cuda + engine ok -> CUDA", ok)
    ok, why = decide_cuda_acceleration(GPU_AUTO, True, False)
    check("cuda decision: engine lacking CUDA -> CPU with reason", not ok and "CUDA interop" in why)
    ok, why = decide_cuda_acceleration(GPU_FORCE, False, True)
    check("cuda decision: force gpu without torch cuda -> CPU", not ok and "no CUDA device" in why)
    ok, why = decide_cuda_acceleration(GPU_OFF, True, True)
    check("cuda decision: explicit CPU mode -> CPU", not ok and "CPU mode" in why)
    ok, _ = decide_cuda_acceleration(GPU_FORCE, True, True)
    check("cuda decision: force gpu available -> CUDA", ok)

    # ---- pre-SR denoise blend (pure) ----
    from ants.dlssnr.node import blend_frames
    orig = frame.copy()
    denoised = np.clip(frame + 0.2, 0.0, 1.0)
    check("blend: amount 1 == processed", np.allclose(blend_frames(orig, denoised, 1.0), denoised))
    check("blend: amount 0 == original", np.allclose(blend_frames(orig, denoised, 0.0), orig))
    check("blend: 0.5 sits halfway",
          np.allclose(blend_frames(orig, denoised, 0.5), (orig + denoised) / 2, atol=1e-6))

    # ---- dispatch ----
    check("apply_bridge dispatch + off",
          np.allclose(apply_bridge(frame, "off"), frame)
          and np.allclose(apply_bridge(frame, "nonsense"), frame)
          and np.allclose(apply_bridge(frame, "classic"), cb(frame)))

    # ---- discovery: models/DLSS layout, ANY filenames ----
    with tempfile.TemporaryDirectory() as td:
        root = os.path.join(td, "DLSS")
        v1 = os.path.join(root, "dlssnr_testA")
        os.makedirs(v1)
        # arbitrary filenames: OreX-style single bridge + nvidia runtime renamed
        open(os.path.join(v1, "my_bridge.dll"), "wb").write(b"x")
        open(os.path.join(v1, "runtime_thing.dll"), "wb").write(b"x")
        saved = (discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, discovery.PACKAGE_DLL_DIR)
        discovery.DLSS_ROOT = root
        discovery.LEGACY_DLSSNR_PATH = os.path.join(td, "legacy_dlssnr")
        discovery.PACKAGE_DLL_DIR = os.path.join(td, "nope")
        try:
            sets = discovery.discover_dll_sets()
            check("discovery: dlssnr_<name> folder found with ANY filenames",
                  any(s["name"] == "dlssnr_testA" and s["complete"] and len(s["dlls"]) == 2
                      for s in sets))
            check("discovery: nvngx hint detected by name",
                  any(s.get("has_nvngx") is False for s in sets))
            check("discovery: resolve by set name",
                  discovery.resolve_dll_dir("dlssnr_testA") == v1)
            check("discovery: combo = auto + names + refresh",
                  discovery.combo_choices() == ["auto", "dlssnr_testA", "refresh"])

            # flat files in models/DLSS = an unnamed set
            open(os.path.join(root, "loose_engine.dll"), "wb").write(b"x")
            sets2 = discovery.discover_dll_sets()
            check("discovery: flat models/DLSS dlls form an unnamed set",
                  any(s["name"] == "(models/DLSS)" for s in sets2))
            open(os.path.join(root, "loose_engine.dll"), "wb").close()
            os.remove(os.path.join(root, "loose_engine.dll"))

            # category subfolders: models/DLSS/<NR|SR|FG>/<version>/
            nr_v = os.path.join(root, "NR", "nvngx_v1")
            sr_v = os.path.join(root, "SR", "dlss_310_5")
            fg_v = os.path.join(root, "FG", "fg_330")
            for d in (nr_v, sr_v, fg_v):
                os.makedirs(d)
                open(os.path.join(d, "x.dll"), "wb").write(b"x")
            nr_sets = discovery.discover_dll_sets("NR")
            check("discovery: NR category set found via NR/<version>",
                  any(x["name"] == "nvngx_v1" and x["category"] == "NR" for x in nr_sets))
            check("discovery: NR selector excludes SR/FG sets",
                  not any(x["name"] in ("dlss_310_5", "fg_330") for x in nr_sets))
            sr_sets = discovery.discover_dll_sets("SR")
            check("discovery: SR category discovered for the future SR selector",
                  any(x["name"] == "dlss_310_5" and x["category"] == "SR" for x in sr_sets)
                  and not any(x["name"] == "nvngx_v1" for x in sr_sets))
            check("discovery: no filter returns every category",
                  {"nvngx_v1", "dlss_310_5", "fg_330"} <=
                  {x["name"] for x in discovery.discover_dll_sets()})
            check("discovery: NR combo has no SR entries",
                  "dlss_310_5" not in discovery.combo_choices("NR"))

            # flat dll named like an NR runtime -> label carries it
            open(os.path.join(root, "nvngx_dlssnr_RenoDX_friendly.dll"), "wb").write(b"x")
            sets3 = discovery.discover_dll_sets()
            check("discovery: flat set label names the NR runtime found inside",
                  any(x["name"] == "(models/DLSS - nvngx_dlssnr_RenoDX_friendly)"
                      for x in sets3))

            # flat dll directly inside a category folder
            open(os.path.join(root, "SR", "nvngx_dlss.dll"), "wb").write(b"x")
            check("discovery: flat files inside a category folder form that category's set",
                  any(x["name"] == "(SR)" and x["category"] == "SR"
                      for x in discovery.discover_dll_sets("SR")))

            # helper stash (owner's "Merserk's_DLLS" spelling included):
            # excluded from the NR selector, exposed via helper_dll_dirs()
            stash = os.path.join(root, "Merserk's_DLLS")
            os.makedirs(stash)
            open(os.path.join(stash, "neuroframe_engine.dll"), "wb").write(b"x")
            open(os.path.join(stash, "neuroframe_caller.dll"), "wb").write(b"x")
            check("discovery: helper stash is NOT an NR selector entry",
                  not any("Merserk" in x["name"] for x in discovery.discover_dll_sets("NR")))
            saved_helpers = discovery.PACKAGE_DLL_DIR
            discovery.PACKAGE_DLL_DIR = os.path.join(td, "no_pkg")
            try:
                dirs = discovery.helper_dll_dirs()
                check("discovery: helper stash discovered by name",
                      any("Merserk" in d for d in dirs))
            finally:
                discovery.PACKAGE_DLL_DIR = saved_helpers

            # a generic subfolder WITHOUT an NR runtime -> OTHER, not in NR selector
            other_v = os.path.join(root, "misc", )
            os.makedirs(other_v)
            open(os.path.join(other_v, "something.dll"), "wb").write(b"x")
            check("discovery: generic folder without NR runtime -> OTHER",
                  any(x["name"] == "misc" and x["category"] == "OTHER"
                      for x in discovery.discover_dll_sets())
                  and not any(x["name"] == "misc" for x in discovery.discover_dll_sets("NR")))
        finally:
            discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, discovery.PACKAGE_DLL_DIR = saved

    # ---- no sets at all -> the loud owner-specified error ----
    saved = (discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, discovery.PACKAGE_DLL_DIR)
    discovery.DLSS_ROOT = os.path.join(tempfile.gettempdir(), "definitely_missing_dlss")
    discovery.LEGACY_DLSSNR_PATH = os.path.join(tempfile.gettempdir(), "definitely_missing_legacy")
    discovery.PACKAGE_DLL_DIR = os.path.join(tempfile.gettempdir(), "definitely_missing_pkg")
    try:
        try:
            discovery.default_dll_dir()
            check("discovery: loud error when empty", False)
        except RuntimeError as e:
            check("discovery: loud error names dlssnr_<version_name> + any-filenames rule",
                  "dlssnr_<version_name>" in str(e) and "ANY .dll filenames" in str(e))
    finally:
        discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, discovery.PACKAGE_DLL_DIR = saved

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

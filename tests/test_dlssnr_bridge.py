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

    # ---- helper stash + staging: the pair lives in ONE folder ----
    import shutil as _shutil
    root = tempfile.mkdtemp()
    saved = (discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH,
             discovery.PACKAGE_DLL_DIR)
    discovery.DLSS_ROOT = root
    discovery.LEGACY_DLSSNR_PATH = os.path.join(root, "_nolegacy")
    discovery.PACKAGE_DLL_DIR = os.path.join(root, "_nopkg")
    try:
        for cat in ("NR", "SR", "FG"):
            os.makedirs(os.path.join(root, cat))
            open(os.path.join(root, cat, "neuroframe_caller.dll"), "wb").write(b"c" * 8)
            open(os.path.join(root, cat, "neuroframe_engine.dll"), "wb").write(b"e" * 8)
        open(os.path.join(root, "NR", "nvngx_dlssnr.dll"), "wb").write(b"n" * 16)
        open(os.path.join(root, "NR", "nvngx_dlssnr_alt.dll"), "wb").write(b"a" * 32)
        os.makedirs(os.path.join(root, "HELPERS"))            # empty stash
        os.makedirs(os.path.join(root, "Merserk_DLLS"))
        for name in ("neuroframe_caller.dll", "neuroframe_engine.dll"):
            open(os.path.join(root, "Merserk_DLLS", name), "wb").write(b"h" * 4)

        check("stash: an EMPTY helper folder never wins over a populated one",
              discovery.helper_dll_dirs()[0].endswith("Merserk_DLLS"))
        # both populated: Merserk_DLLS still wins (owner's one-home ruling)
        open(os.path.join(root, "HELPERS", "neuroframe_caller.dll"), "wb").write(b"x")
        check("stash: Merserk_DLLS beats a populated HELPERS folder",
              discovery.helper_dll_dirs()[0].endswith("Merserk_DLLS"))
        check("stash: helper_stash_dir points at the populated stash",
              (discovery.helper_stash_dir() or "").endswith("Merserk_DLLS"))
        check("stash: helper-named files are recognised",
              discovery._is_helper_dll_name("neuroframe_caller.dll")
              and not discovery._is_helper_dll_name("nvngx_dlssnr.dll"))

        stage = discovery.stage_legacy_runtime(
            os.path.join(root, "NR", "nvngx_dlssnr.dll"))
        staged_files = sorted(os.listdir(stage))
        check("stage: canonical name keeps the folder IN PLACE",
              os.path.normcase(stage) == os.path.normcase(os.path.join(root, "NR")))
        stage2 = discovery.stage_legacy_runtime(
            os.path.join(root, "NR", "nvngx_dlssnr_alt.dll"))
        staged_files = sorted(os.listdir(stage2))
        check("stage: only the runtime + the helper pair are copied",
              staged_files == ["neuroframe_caller.dll", "neuroframe_engine.dll",
                               "nvngx_dlssnr.dll"], )
        check("stage: the aliased runtime is the selected BYTES",
              open(os.path.join(stage2, "nvngx_dlssnr.dll"), "rb").read() == b"a" * 32)
        check("stage: folder is content-addressed <stem>-<size>",
              os.path.basename(stage2) == "nvngx_dlssnr_alt-32")

        # no stash at all -> old behaviour, loudly
        _shutil.rmtree(os.path.join(root, "Merserk_DLLS"))
        os.remove(os.path.join(root, "HELPERS", "neuroframe_caller.dll"))
        os.makedirs(os.path.join(root, "SR", "set"))
        open(os.path.join(root, "SR", "set", "nvngx_dlssnr_b.dll"), "wb").write(b"b" * 4)
        open(os.path.join(root, "SR", "set", "some_other.dll"), "wb").write(b"o" * 4)
        stage3 = discovery.stage_legacy_runtime(
            os.path.join(root, "SR", "set", "nvngx_dlssnr_b.dll"))
        check("stage: no stash -> compat sibling copy (all siblings, loudly)",
              sorted(os.listdir(stage3)) == ["nvngx_dlssnr.dll",
                                             "nvngx_dlssnr_b.dll",
                                             "some_other.dll"]
              and open(os.path.join(stage3, "nvngx_dlssnr.dll"), "rb").read() == b"b" * 4)

        # auto selection never returns a helper dll (both NR candidates are
        # unversioned here, so the file date decides - and either way it is an
        # nvngx_dlssnr runtime, not the helper pair)
        picked = discovery.resolve_nr_runtime_path("auto")
        check("auto: picks an nvngx_dlssnr* file, never the helper pair",
              os.path.basename(picked) in ("nvngx_dlssnr.dll",
                                           "nvngx_dlssnr_alt.dll"))
        _shutil.rmtree(os.path.join(root, "Merserk_DLLS"), ignore_errors=True)
        os.remove(os.path.join(root, "NR", "nvngx_dlssnr.dll"))
        os.remove(os.path.join(root, "NR", "nvngx_dlssnr_alt.dll"))
        try:
            discovery.resolve_nr_runtime_path("auto")
            check("auto: only helper dlls left -> loud [ANTs] error", False)
        except RuntimeError as exc:
            check("auto: only helper dlls left -> loud [ANTs] error",
                  "[ANTs]" in str(exc) and "Merserk_DLLS" in str(exc))
        # a non-canonical name that is not a helper is still usable (with a warning)
        open(os.path.join(root, "NR", "dlssnr_custom.dll"), "wb").write(b"q" * 4)
        check("auto: non-canonical non-helper dll is used by guess",
              discovery.resolve_nr_runtime_path("auto")
              .endswith("dlssnr_custom.dll"))
    finally:
        discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, \
            discovery.PACKAGE_DLL_DIR = saved

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

    # ---- per-category selectors (owner restructure) ----
    import importlib as _il
    import pathlib as _pl
    sys.path.insert(0, str(REPO.parent))
    _pkg = _il.import_module(REPO.name)
    types_def = _pkg.NODE_CLASS_MAPPINGS["ANTsDLSS5Enhancer"].INPUT_TYPES()
    req = types_def["required"]
    check("dlss5: dll_version replaced by per-category selectors",
          "dll_version" not in req
          and "nr_dll_version" in req and "sr_dll_version" in req
          and "fg_dll_version" in req)
    check("dlss5: SR model preset defaults to L (newest highest quality)",
          req["sr_model"][1]["default"] == "L - Transformer II Quality"
          and "L - Transformer II Quality" in req["sr_model"][0])
    check("dlss5: NR preset widget present, defaults to driver Default",
          req["nr_model_preset"][0][0] == "Default"
          and req["nr_model_preset"][1]["default"] == "Default")
    check("dlss5: pre_denoise_mode present, defaults to SR, model choice named like the input",
          req["pre_denoise_mode"][1]["default"] == "SR (DLSS denoise)"
          and "SR (DLSS denoise)" in req["pre_denoise_mode"][0]
          and "Denoise Model" in req["pre_denoise_mode"][0])
    node_src = _pl.Path(REPO / "ants" / "dlssnr" / "node.py").read_text()
    check("dlss5: NR + SR session factories share _ensure_native_gpu (run-8 ordering fix)",
          node_src.count("self._ensure_native_gpu()") >= 2
          and "def _ensure_native_gpu" in node_src
          and node_src.count("if self.native_gpu is None:") == 1)
    check("dlss5: no 'refresh' entries in the category combos",
          all("refresh" not in req[w][0]
              for w in ("nr_dll_version", "sr_dll_version", "fg_dll_version")))

    saved = (discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, discovery.PACKAGE_DLL_DIR)
    root = tempfile.mkdtemp(prefix="ants_cat_")
    discovery.DLSS_ROOT = root
    discovery.LEGACY_DLSSNR_PATH = os.path.join(root, "missing_legacy")
    discovery.PACKAGE_DLL_DIR = os.path.join(root, "missing_pkg")
    try:
        os.makedirs(os.path.join(root, "NR"))
        open(os.path.join(root, "NR", "nvngx_dlssnr_RenoDX.dll"), "wb").write(b"x")
        open(os.path.join(root, "NR", "nvngx_dlssnr_320.dll"), "wb").write(b"x")
        os.makedirs(os.path.join(root, "SR", "310.9.1"))
        open(os.path.join(root, "SR", "310.9.1", "nvngx_dlss.dll"), "wb").write(b"c")
        open(os.path.join(root, "SR", "nvngx_dlss.dll"), "wb").write(b"a" * 3)
        open(os.path.join(root, "SR", "nvngx_dlss_310.9.1.dll"), "wb").write(b"b" * 5)
        check("discovery: flat NR dlls listed individually",
              discovery.category_choices("NR") == ["auto", "nvngx_dlssnr_320.dll",
                                                   "nvngx_dlssnr_RenoDX.dll"])
        check("discovery: flat SR dlls listed individually (owner duplicate test)",
              set(discovery.category_choices("SR")) == {"auto", "310.9.1",
                                                        "nvngx_dlss.dll",
                                                        "nvngx_dlss_310.9.1.dll"})
        check("naming: the SR selector lists the NEWEST build first",
              discovery.category_choices("SR")[1] == "nvngx_dlss_310.9.1.dll"
              and discovery.category_choices("SR")[-1] == "nvngx_dlss.dll")
        check("discovery: SR version subfolder + FG reserved-empty",
              "310.9.1" in discovery.category_choices("SR")
              and discovery.category_choices("FG") == ["auto"])
        check("discovery: NR runtime path resolves a chosen flat dll",
              discovery.resolve_nr_runtime_path("nvngx_dlssnr_320.dll")
              == os.path.join(root, "NR", "nvngx_dlssnr_320.dll"))
        check("discovery: NR auto -> first flat dll",
              discovery.resolve_nr_runtime_path("auto")
              == os.path.join(root, "NR", "nvngx_dlssnr_320.dll"))
        # staging: a same-named dll passes through; others are copied to the
        # writable cache as nvngx_dlss.dll (what the NGX core searches for)
        from ants.dlsssr.discovery import stage_sr_dll
        direct = stage_sr_dll(os.path.join(root, "SR", "nvngx_dlss.dll"))
        check("staging: nvngx_dlss.dll passes through to its own dir",
              direct == os.path.join(root, "SR"))
        staged = stage_sr_dll(os.path.join(root, "SR", "nvngx_dlss_310.9.1.dll"))
        check("staging: renamed build staged as nvngx_dlss.dll in a writable dir",
              os.path.isfile(os.path.join(staged, "nvngx_dlss.dll"))
              and os.path.getsize(os.path.join(staged, "nvngx_dlss.dll")) == 5
              and "sr_staged" in staged)
    finally:
        discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, discovery.PACKAGE_DLL_DIR = saved

    # ---- regression: pre-denoise frames must reach the dlls C-contiguous.
    # A planar movedim view (tiled upscale mirror) handed to the raw pointer
    # produced the rig's "9 gray tiles" output: planar RGB decoded as
    # interleaved (3 wrapped bands per plane, R then G then B). ----
    node_src = (REPO / "ants" / "dlssnr" / "node.py").read_text()
    core_src = (REPO / "ants" / "dlssnr" / "core.py").read_text()
    check("denoise: blend_frames contiguous-izes the processed view",
          "processed = processed.contiguous()" in node_src
          and "np.ascontiguousarray(processed)" in node_src)
    check("denoise: _pre_denoise_frame returns a contiguous tensor",
          ".to(device=frame.device, dtype=torch.float32).contiguous()" in node_src)
    check("denoise: CUDA legacy path does not empty_like a movedim view",
          "dest = torch.empty(tuple(frame.shape)" in node_src
          and "torch.empty_like(frame)" not in node_src)
    check("denoise: CUDA legacy path contiguousizes frame before data_ptr",
          "if not frame.is_contiguous():" in node_src)
    check("process_host normalizes a non-contiguous source before ctypes",
          "np.ascontiguousarray(source, dtype=np.float32)" in core_src)
    check("process_host rejects a non-contiguous destination loudly",
          "destination buffer must be a" in core_src)

    # ---- THE BUILD NAMING RULE (owner directive 2026-09-20) ----
    # The node loads the NEWEST build it can find, version read from the file
    # name; an explicit pick always wins. See ants/dlsssr/versions.py.
    from ants.dlsssr import versions
    check("naming: a date in the name is the version",
          versions.build_version("nvngx_dlssnr_2026-09-14.dll") == (2, 2026, 9, 14)
          and versions.build_version("nvngx_dlssnr_20260914.dll") == (2, 2026, 9, 14)
          and versions.build_version("nvngx_dlssnr_2026_09_14_renodx4000.dll")
          == (2, 2026, 9, 14))
    check("naming: NVIDIA-style dotted versions and bare build numbers work",
          versions.build_version("nvngx_dlss_310.9.1.dll") == (1, 310, 9, 1)
          and versions.build_version("nvngx_dlssnr_v10.0.dll") == (1, 10, 0)
          and versions.build_version("nvngx_dlssnr_2.dll") == (0, 2))
    check("naming: hardware/vendor tags are never mistaken for a version",
          versions.build_version(
              "nvngx_dlssnr_RenoDX_4000_series_friendly.dll") == (-1,)
          and versions.build_version("nvngx_dlssnr_4090.dll") == (-1,)
          and versions.build_version("nvngx_dlssnr.dll") == (-1,))
    check("naming: date > dotted version > bare number > no version",
          versions.build_version("x_2026-09-14.dll")
          > versions.build_version("x_310.9.1.dll")
          > versions.build_version("x_2.dll")
          > versions.build_version("x.dll"))
    check("naming: free text after the version does not change the order",
          versions.build_version("nvngx_dlssnr_2026-09-14_beta2.dll")
          == versions.build_version("nvngx_dlssnr_2026-09-14.dll"))
    check("naming: describe() names what was read out of the file name",
          versions.describe("nvngx_dlssnr_2026-09-14.dll") == "date 2026-09-14"
          and versions.describe("nvngx_dlss_310.9.1.dll") == "version 310.9.1"
          and "no version" in versions.describe("nvngx_dlssnr.dll"))

    saved = (discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH,
             discovery.PACKAGE_DLL_DIR)
    try:
        with tempfile.TemporaryDirectory() as td:
            discovery.DLSS_ROOT = td
            discovery.LEGACY_DLSSNR_PATH = os.path.join(td, "_nolegacy")
            discovery.PACKAGE_DLL_DIR = os.path.join(td, "_nopkg")
            nr_dir = os.path.join(td, "NR")
            os.makedirs(nr_dir)
            # the newest build carries a force-terminator NAME; the older one
            # is safe. Auto (native) must take the newest SAFE build.
            open(os.path.join(nr_dir, "nvngx_dlssnr_2026-01-01.dll"),
                 "wb").write(b"safe-older")
            open(os.path.join(nr_dir, "nvngx_dlssnr_2026-09-14_renodx4000.dll"),
                 "wb").write(b"risky-newest")
            check("auto: picks by the naming rule (date beats nothing)",
                  discovery.resolve_nr_runtime_path("auto", skip_known_bad=True)
                  .endswith("nvngx_dlssnr_2026-01-01.dll"))
            check("auto: without the queue-safety rule it takes the newest",
                  discovery.resolve_nr_runtime_path("auto")
                  .endswith("nvngx_dlssnr_2026-09-14_renodx4000.dll"))
            check("auto: an explicit pick of a risky build is honored",
                  discovery.resolve_nr_runtime_path(
                      "nvngx_dlssnr_2026-09-14_renodx4000.dll",
                      skip_known_bad=True)
                  .endswith("nvngx_dlssnr_2026-09-14_renodx4000.dll"))
            os.remove(os.path.join(nr_dir, "nvngx_dlssnr_2026-01-01.dll"))
            try:
                picked = discovery.resolve_nr_runtime_path(
                    "auto", skip_known_bad=True)
                check("auto: when EVERY build is risky it uses the newest "
                      "instead of refusing (the owner's own rig)",
                      picked.endswith("nvngx_dlssnr_2026-09-14_renodx4000.dll"))
            except RuntimeError:
                check("auto: when EVERY build is risky it uses the newest "
                      "instead of refusing (the owner's own rig)", False)
            # a set FOLDER resolves to its newest build, not to its first file
            set_dir = os.path.join(nr_dir, "2026-05")
            os.makedirs(set_dir)
            open(os.path.join(set_dir, "nvngx_dlssnr_2026-01-01.dll"),
                 "wb").write(b"set-old")
            open(os.path.join(set_dir, "nvngx_dlssnr_2026-05-05.dll"),
                 "wb").write(b"set-new")
            from ants.dlsssr.discovery import find_nr_runtime_dll
            check("naming: a set folder resolves to its NEWEST runtime",
                  find_nr_runtime_dll(set_dir)
                  .endswith("nvngx_dlssnr_2026-05-05.dll"))
            check("naming: an explicit set pick resolves to its newest runtime",
                  discovery.resolve_nr_runtime_path("2026-05")
                  .endswith("nvngx_dlssnr_2026-05-05.dll"))
            check("naming: newest_first ranks by version, then file date",
                  versions.newest_first(["nvngx_dlssnr_2026-01-01.dll",
                                         "nvngx_dlssnr_310.9.1.dll",
                                         "nvngx_dlssnr.dll"])
                  == ["nvngx_dlssnr_2026-01-01.dll", "nvngx_dlssnr_310.9.1.dll",
                      "nvngx_dlssnr.dll"])
            # an unversioned file that is newer ON DISK than the picked build
            # is reported, so nobody has to guess why it lost
            import time as _time
            versioned = os.path.join(nr_dir, "nvngx_dlssnr_2026-09-14_v2.dll")
            open(versioned, "wb").write(b"versioned")
            _time.sleep(0.01)
            plain = os.path.join(nr_dir, "nvngx_dlssnr.dll")
            open(plain, "wb").write(b"stock-drop-in-newer-on-disk")
            check("naming: an unversioned file that is newer on disk is "
                  "reported as the loser (nobody has to guess why it lost)",
                  versions.unversioned_newer([versioned, plain], versioned)
                  == ["nvngx_dlssnr.dll"]
                  and versions.unversioned_newer([versioned], versioned) == [])
            os.remove(plain)
            os.remove(versioned)
            check("native: RenoDX-named builds match the force-terminator matcher",
                  discovery.is_known_force_terminator(
                      r"C:\m\DLSS\NR\nvngx_dlssnr_RenoDX_4000_series_friendly.dll")
                  and not discovery.is_known_force_terminator(
                      r"C:\m\DLSS\NR\nvngx_dlssnr_2026-09-14.dll"))
            node_src_rule = (REPO / "ants" / "dlssnr" / "node.py").read_text()
            check("naming: the widget says the rule out loud (newest first, "
                  "auto loads the newest, explicit pick wins)",
                  "NEWEST FIRST" in node_src_rule
                  and "naming rule" in node_src_rule
                  and "docs/MODELS_DLSS_LAYOUT.md" in node_src_rule)
    finally:
        discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, \
            discovery.PACKAGE_DLL_DIR = saved

    # ---- rig 2026-09-20 14:02: the "stock" file IS the RenoDX build ----
    # The owner keeps two names of the SAME build on purpose (testing the
    # picker), so a rename must not be mistaken for a safe build - and the
    # picker must keep working, not refuse.
    saved = (discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH,
             discovery.PACKAGE_DLL_DIR)
    try:
        with tempfile.TemporaryDirectory() as td:
            discovery.DLSS_ROOT = td
            discovery.LEGACY_DLSSNR_PATH = os.path.join(td, "_nolegacy")
            discovery.PACKAGE_DLL_DIR = os.path.join(td, "_nopkg")
            nr_dir = os.path.join(td, "NR")
            os.makedirs(nr_dir)
            body = b"renodx-build" * 4096          # same bytes, two names
            open(os.path.join(nr_dir, "nvngx_dlssnr_RenoDX_4000.dll"),
                 "wb").write(body)
            open(os.path.join(nr_dir, "nvngx_dlssnr.dll"), "wb").write(body)
            check("rename: same_bytes() resolves identical files",
                  discovery.same_bytes(
                      os.path.join(nr_dir, "nvngx_dlssnr.dll"),
                      os.path.join(nr_dir, "nvngx_dlssnr_RenoDX_4000.dll"))
                  and not discovery.same_bytes(
                      os.path.join(nr_dir, "nvngx_dlssnr.dll"),
                      os.path.join(nr_dir, "missing.dll")))
            check("rename: the rename is recognised as the same risky build",
                  "byte-identical" in discovery.risk_reason(
                      os.path.join(nr_dir, "nvngx_dlssnr.dll"))
                  and "force-terminator list" in discovery.risk_reason(
                      os.path.join(nr_dir, "nvngx_dlssnr_RenoDX_4000.dll")))
            # a genuinely different (safe) build wins over BOTH names
            safe = os.path.join(nr_dir, "nvngx_dlssnr_2025-01-01.dll")
            open(safe, "wb").write(b"stock-build")
            check("rename: auto avoids the renamed pair when a safe build exists",
                  discovery.resolve_nr_runtime_path("auto", skip_known_bad=True)
                  .endswith("nvngx_dlssnr_2025-01-01.dll"))
            # ... and with only the two risky names left, auto still works
            os.remove(safe)
            picked = discovery.resolve_nr_runtime_path("auto", skip_known_bad=True)
            check("rename: with nothing safe left auto uses the newest "
                  "instead of refusing (a rename is not a different build)",
                  os.path.basename(picked) in ("nvngx_dlssnr.dll",
                                               "nvngx_dlssnr_RenoDX_4000.dll"))
            check("rename: an explicit pick still resolves exactly",
                  discovery.resolve_nr_runtime_path("nvngx_dlssnr.dll")
                  .endswith("nvngx_dlssnr.dll")
                  and discovery.resolve_nr_runtime_path(
                      "nvngx_dlssnr_RenoDX_4000.dll")
                  .endswith("nvngx_dlssnr_RenoDX_4000.dll"))
    finally:
        discovery.DLSS_ROOT, discovery.LEGACY_DLSSNR_PATH, \
            discovery.PACKAGE_DLL_DIR = saved
    check("native: the engine warns instead of refusing explicit picks",
          "is_known_force_terminator(dll_path)" in node_src
          and "force-terminator" in node_src
          and "ANTS_ALLOW_KNOWN_BAD_NR" not in node_src)
    check("native: the force-terminator list credits RenoDX provenance",
          '"renodx"' in (REPO / "ants" / "dlssnr" / "discovery.py").read_text()
          and "clshortfuse" in (REPO / "ants" / "dlssnr" / "discovery.py").read_text())

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

"""Behavioral test for rfactor.faceboost.restorer.ensure_facerestore_model.

Regression for the reported crash:
    AttributeError: module '...rfactor.faceboost.restorer' has no attribute
    'ensure_facerestore_model'
(the function was referenced by nodes.py but a patch that should have created
it silently didn't apply; nothing at import time referenced it, so the
import smoke test could not catch it).

Usage: python tests/test_runtime_surface.py
"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
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
    sys.path.insert(0, str(REPO / "tests"))
    from smoke_import import install_stubs

    install_stubs()
    sys.path.insert(0, str(REPO))

    from rfactor.faceboost import restorer

    # --- the attribute exists at all (the reported crash) -------------------
    check("ensure_facerestore_model exists", hasattr(restorer, "ensure_facerestore_model"))
    check("FACE_RESTORE_MODEL_URLS exists", hasattr(restorer, "FACE_RESTORE_MODEL_URLS"))
    check("get_restored_face exists", hasattr(restorer, "get_restored_face"))

    # --- behavior ------------------------------------------------------------
    check("'none' resolves to None", restorer.ensure_facerestore_model("none") is None)
    check("empty resolves to None", restorer.ensure_facerestore_model("") is None)
    check("None resolves to None", restorer.ensure_facerestore_model(None) is None)
    check("unknown model returns None (no download attempt)",
          restorer.ensure_facerestore_model("definitely-not-a-model.pth") is None)

    # known model present on disk -> returned as-is
    import folder_paths

    with tempfile.TemporaryDirectory() as td:
        existing = os.path.join(td, "GFPGANv1.4.pth")
        open(existing, "wb").write(b"x" * 16)
        original_get_full_path = folder_paths.get_full_path
        folder_paths.get_full_path = lambda folder, name: existing if name == "GFPGANv1.4.pth" else None
        try:
            resolved = restorer.ensure_facerestore_model("GFPGANv1.4.pth")
        finally:
            folder_paths.get_full_path = original_get_full_path
        check("present model resolves to its path", resolved == existing)

    # --- other runtime-referenced surface (functions nodes.py calls mid-run) --
    import rfactor.swapper as swapper

    for fn in ("swap_face", "swap_face_many", "getFaceSwapModel", "getAnalysisModel",
               "analyze_faces", "unload_all_models", "get_current_faces_model"):
        check(f"swapper.{fn} exists", hasattr(swapper, fn))

    from rfactor.faceboost import swapper as fb_swapper

    check("faceboost.swapper.in_swap exists", hasattr(fb_swapper, "in_swap"))

    from rfactor.dlssnr import discovery

    for fn in ("discover_dll_sets", "resolve_dll_dir", "combo_choices", "default_dll_dir"):
        check(f"dlssnr.discovery.{fn} exists", hasattr(discovery, fn))

    # --- model loader nodes ---------------------------------------------------
    from rfactor.loaders import (
        ReFactorFaceSwapModelLoader,
        ReFactorFaceRestoreModelLoader,
        ReFactorFaceDetectionModelLoader,
    )

    for cls in (ReFactorFaceSwapModelLoader, ReFactorFaceRestoreModelLoader, ReFactorFaceDetectionModelLoader):
        types_def = cls.INPUT_TYPES()
        check(f"{cls.__name__}.INPUT_TYPES", "required" in types_def)

    # 'none' swap model -> None passthrough (swap skipped, no crash)
    inst = ReFactorFaceSwapModelLoader()
    out = inst.load_model("none")
    check("swap loader 'none' -> None model", out == (None,))

    det = ReFactorFaceDetectionModelLoader()
    info = det.load_model("retinaface_resnet50")[0]
    check("detection loader returns name info", info == {"name": "retinaface_resnet50"})

    # --- main node socket contract (owner spec) --------------------------------
    sys.path.insert(0, str(REPO.parent))
    pkg = __import__("ReAactor_node_ReFactor", fromlist=["NODE_CLASS_MAPPINGS"])
    swap_node = pkg.NODE_CLASS_MAPPINGS["ANTsFaceDancer"]
    ti = swap_node.INPUT_TYPES()
    for socket in ("original_image",):
        check(f"required socket '{socket}'", socket in ti["required"])
    for socket in ("FaceSwap_model", "FaceRestore_model", "FaceDetection_model",
                   "UpscaleModel", "target_face_image", "target_face_model"):
        check(f"optional socket '{socket}'", socket in ti["optional"])
    for gone in ("input_image", "swap_model", "facedetection", "face_restore_model", "source_image", "face_model"):
        check(f"old socket '{gone}' removed", gone not in ti["required"] and gone not in ti["optional"])
    check("codeformer_fidelity renamed", "codeformer_fidelity" in ti["required"]
          and "codeformer_weight" not in ti["required"])

    # UI grouping: the three model sockets are contiguous at the top of optional
    optional_order = list(ti["optional"].keys())
    trio = ["FaceSwap_model", "FaceRestore_model", "FaceDetection_model"]
    check("model sockets grouped (contiguous, first)", optional_order[:3] == trio)

    # former Fast Face Swap [OPTIONS] + Restore Face Advanced are merged in
    check("OPTIONS + Advanced nodes scrapped",
          "ReFactorFaceSwapOpt" not in pkg.NODE_CLASS_MAPPINGS
          and "ReFactorRestoreFaceAdvanced" not in pkg.NODE_CLASS_MAPPINGS)
    check("OPTIONS socket on the main node", "options" in ti["optional"]
          and "FaceRestorePrePass_model" in ti["optional"])

    # CodeFormer must be registered (regression: silent missing import broke restore)
    from rfactor.faceboost.archs.registry import ARCH_REGISTRY
    check("CodeFormer registered in arch registry", "CodeFormer" in ARCH_REGISTRY)

    # Face Booster is deprecated: its bundle, socket and node are gone
    check("FACE_BOOST fully removed", "face_boost" not in ti["optional"] and "FACE_BOOST" not in str(ti)
          and "ReFactorFaceBoost" not in pkg.NODE_CLASS_MAPPINGS)

    # upRes contract on the four restore-capable nodes
    for name in ("ANTsFaceDancer", "ANTsFaceRestore"):
        t = pkg.NODE_CLASS_MAPPINGS[name].INPUT_TYPES()
        check(f"{name}: upRes widgets",
              "face_restore_upres" in t["required"] and "upres_interpolation" in t["required"])
        choices = list(t["required"]["upres_interpolation"][0])
        check(f"{name}: interpolation choices end with 'Use Upscale model'",
              choices[-1] == "Use Upscale model" and choices[0] == "Lanczos")

    # pre-pass contract
    check("pre-pass toggle + selector", "face_restore_pre_pass" in ti["required"]
          and "face_restore_pre_pass_uses" in ti["required"]
          and list(ti["required"]["face_restore_pre_pass_uses"][0]) == ["main FR model", "dedicated FR model"])
    check("ACTIVE toggle", "active" in ti["required"] and "enabled" not in ti["required"])
    # merged face-selection controls
    for w in ("face_selection", "sort_by", "reverse_order", "take_start", "take_count"):
        check(f"merged filter widget '{w}'", w in ti["required"])

    check("loaders registered", all(n in pkg.NODE_CLASS_MAPPINGS for n in (
        "ANTsFaceSwapModelLoader", "ANTsFaceRestoreModelLoader",
        "ANTsFaceDetectionModelLoader", "ANTsUpscaleModelLoader")))
    check("brand: no ReFactor keys left", not [k for k in pkg.NODE_CLASS_MAPPINGS if k.startswith("ReFactor")])
    check("display names carry the ANTs brand",
          all(v.startswith("ANTs") for v in pkg.NODE_DISPLAY_NAME_MAPPINGS.values()))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

"""Tests for the Face Restore upRes interpolator logic (ants/upres.py)
and the comfy-native upscale-model loader/upscaler plumbing.

Usage: python tests/test_upres.py
"""

import sys
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

    import cv2
    from ants.upres import (
        UPRES_USE_MODEL,
        decide_restore_size,
        interp_flag,
        native_size_from_name,
        needs_model_upscale,
        restore_model_native,
    )

    # ---- interpolation mapping ----
    check("Lanczos flag", interp_flag("Lanczos") == cv2.INTER_LANCZOS4)
    check("Bicubic flag", interp_flag("Bicubic") == cv2.INTER_CUBIC)
    check("Bilinear flag", interp_flag("Bilinear") == cv2.INTER_LINEAR)
    check("Nearest flag", interp_flag("Nearest") == cv2.INTER_NEAREST)
    check("Use Upscale model has no cv2 flag", interp_flag(UPRES_USE_MODEL) is None)
    check("unknown falls back to Lanczos", interp_flag("nonsense") == cv2.INTER_LANCZOS4)

    # ---- native size + dynamic detection ----
    check("native 512 default", native_size_from_name("GFPGANv1.4.pth") == 512)
    check("native 1024", native_size_from_name("GPEN-BFR-1024.onnx") == 1024)
    check("native 2048", native_size_from_name("GPEN-BFR-2048.onnx") == 2048)

    class _In:
        def __init__(self, shape):
            self.shape = shape

    class _Sess:
        def __init__(self, shape):
            self._in = [_In(shape)]
        def get_inputs(self):
            return self._in

    check("onnx fixed 512 -> (512, False)",
          restore_model_native("codeformer.onnx", _Sess([1, 3, 512, 512])) == (512, False))
    check("onnx dynamic -> dynamic",
          restore_model_native("gfpgan.onnx", _Sess([1, 3, "h", "w"]))[1] is True)
    check("onnx fixed 256 honored over name",
          restore_model_native("whatever_512.onnx", _Sess([1, 3, 256, 256])) == (256, False))
    check("gfpgan .pth is dynamic", restore_model_native("GFPGANv1.4.pth")[1] is True)
    check("codeformer .pth is fixed", restore_model_native("codeformer-v0.1.0.pth") == (512, False))

    # ---- decision ----
    check("switch OFF -> native", decide_restore_size(512, True, 100, False) == 512)
    check("small face + dynamic -> own size (rounded to 8)",
          decide_restore_size(512, True, 200, True) == 200)
    check("tiny face clamped to 64", decide_restore_size(512, True, 20, True) == 64)
    check("face near native -> native", decide_restore_size(512, True, 500, True) == 512)
    check("big face -> native (downscale path)",
          decide_restore_size(512, False, 2000, True) == 512)
    check("small face + fixed model -> native (must upscale)",
          decide_restore_size(512, False, 200, True) == 512)
    check("no detection info -> native", decide_restore_size(512, True, None, True) == 512)

    # ---- S1 helper ----
    check("model upscale only for small faces in UseModel mode",
          needs_model_upscale("Use Upscale model", 512, 200) is True
          and needs_model_upscale("Use Upscale model", 512, 700) is False
          and needs_model_upscale("Lanczos", 512, 200) is False)

    # ---- upscaler round trip (model stubbed at the model-call boundary) ----
    from ants import upscaler

    calls = {}

    class _FakeUpscaler:
        pass

    class _Wrap:
        def __init__(self, a):
            self.a = a
        def cpu(self):
            return self
        def numpy(self):
            return self.a
        def __getitem__(self, i):
            return _Wrap(self.a[i])

    def _fake_upscale(model, t):
        calls["fed"] = tuple(t.shape)
        return _Wrap(t)

    real_upscale = upscaler.upscale_image_with_model
    real_from_numpy = sys.modules["torch"].from_numpy
    upscaler.upscale_image_with_model = _fake_upscale

    class _Arr:
        def __init__(self, a):
            self.a = a
        def unsqueeze(self, i):
            return np.expand_dims(self.a, i)

    sys.modules["torch"].from_numpy = lambda a: _Arr(a)  # stub env: array passthrough
    try:
        bgr = np.zeros((8, 8, 3), dtype=np.uint8)
        bgr[:, :, 0] = 255  # Blue channel max in BGR
        out = upscaler.upscale_bgr_face(_FakeUpscaler(), bgr)
        check("upscale_bgr_face returns uint8 BGR", out.dtype == np.uint8 and out.shape == (8, 8, 3))
        check("channel order preserved (BGR in, BGR out)", out[:, :, 0].max() == 255)
        check("model received a (1,H,W,C) batch", calls.get("fed", ())[0] == 1)
    finally:
        upscaler.upscale_image_with_model = real_upscale
        sys.modules["torch"].from_numpy = real_from_numpy

    # ---- loader node ----
    import folder_paths
    from ants.loaders import ReFactorUpscaleModelLoader, UPSCALE_MODEL

    check("UPSCALE_MODEL type string", UPSCALE_MODEL == "UPSCALE_MODEL")
    original = folder_paths.get_filename_list
    folder_paths.get_filename_list = lambda d: ["4x-UltraSharp.pth"] if d == "upscale_models" else []
    try:
        ti = ReFactorUpscaleModelLoader.INPUT_TYPES()
        check("loader lists comfy upscale models", ti["required"]["model_name"][0] == ["4x-UltraSharp.pth"])
    finally:
        folder_paths.get_filename_list = original

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

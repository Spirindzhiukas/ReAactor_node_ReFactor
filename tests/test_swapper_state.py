"""Regression test for the UnboundLocalError in getFaceSwapModel.

The crash (reported from a live ComfyUI portable install):
    UnboundLocalError: cannot access local variable 'FS_MODEL' where it is not
    associated with a value  (ants/swapper.py, _getFaceSwapModel_locked)

Usage: python tests/test_swapper_state.py
"""

import os
import sys
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

    import ants.swapper as swapper

    # --- resolve_swap_model_path: no more UnboundLocalError on odd names -----
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        swap_file = os.path.join(td, "my_custom_swap_model.onnx")
        open(swap_file, "wb").write(b"stub")

        resolved = swapper.resolve_swap_model_path({"name": "my_custom_swap_model.onnx", "path": swap_file})
        check("loader dict resolves", resolved == os.path.realpath(swap_file))
        resolved = swapper.resolve_swap_model_path(swap_file)
        check("abs path resolves", resolved == os.path.realpath(swap_file))

        try:
            swapper.resolve_swap_model_path("totally_unknown_name.onnx")
            check("unknown name raises helpful error", False)
        except FileNotFoundError as e:
            check("unknown name raises helpful error", "totally_unknown_name" in str(e) and "models" in str(e))

        try:
            swapper.resolve_swap_model_path({"name": "ghost.onnx", "path": os.path.join(td, "missing.onnx")})
            check("dict with dead path falls back to folder search -> raises", False)
        except FileNotFoundError:
            check("dict with dead path falls back to folder search -> raises", True)

        # --- getFaceSwapModel: global-state read/write across the locked helper ---
        class DummySwapper:
            def __init__(self, path, providers=None):
                self.path = path

        real_ins, real_hyper = swapper.INSwapper, swapper.HyperSwapper
        swapper.INSwapper = DummySwapper
        swapper.HyperSwapper = DummySwapper
        try:
            model = swapper.getFaceSwapModel({"name": "m1.onnx", "path": swap_file})
            check("first load returns a swapper", isinstance(model, DummySwapper))
            check("FS_MODEL updated", swapper.FS_MODEL is model)
            check("CURRENT_FS_MODEL_PATH recorded", swapper.CURRENT_FS_MODEL_PATH == os.path.realpath(swap_file))

            again = swapper.getFaceSwapModel({"name": "m1.onnx", "path": swap_file})
            check("cached path returns same object", again is model)

            other = swapper.getFaceSwapModel({"name": "m2.onnx", "path": swap_file})
            check("dict re-resolution to same path stays cached", other is model)
        finally:
            swapper.INSwapper, swapper.HyperSwapper = real_ins, real_hyper

    # --- unload_all_models resets everything ---
    swapper.unload_all_models()
    check("unload_all_models clears FS_MODEL", swapper.FS_MODEL is None)
    check("unload_all_models clears analysis models",
          swapper.ANALYSIS_MODELS["640"] is None and swapper.ANALYSIS_MODELS["320"] is None)

    # --- sort_by_order / get_face_gender sanity (pure logic) ---
    class F:
        def __init__(self, x1, y1, x2, y2):
            self.bbox = [x1, y1, x2, y2]

    faces = [F(100, 0, 200, 100), F(0, 0, 50, 50)]
    lr = swapper.sort_by_order(faces, "left-right")
    check("sort left-right", [f.bbox[0] for f in lr] == [0, 100])
    ls = swapper.sort_by_order(faces, "large-small")
    check("sort large-small default", ls[0].bbox[0] == 100)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

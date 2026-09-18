"""Regression test for the UnboundLocalError in getFaceSwapModel.

The crash (reported from a live ComfyUI portable install):
    UnboundLocalError: cannot access local variable 'FS_MODEL' where it is not
    associated with a value  (rfactor/swapper.py, _getFaceSwapModel_locked)

Usage: python tests/test_swapper_state.py
"""

import sys
import types
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

    import rfactor.swapper as swapper

    # --- getFaceSwapModel: global-state read/write across the locked helper ---
    class DummySwapper:
        def __init__(self, path, providers=None):
            self.path = path

    real_ins, real_hyper = swapper.INSwapper, swapper.HyperSwapper
    swapper.INSwapper = DummySwapper
    swapper.HyperSwapper = DummySwapper
    try:
        model = swapper.getFaceSwapModel("/models/insightface/inswapper_128.onnx")
        check("first load returns a swapper", isinstance(model, DummySwapper))
        check("FS_MODEL updated", swapper.FS_MODEL is model)
        check("CURRENT_FS_MODEL_PATH recorded",
              swapper.CURRENT_FS_MODEL_PATH == "/models/insightface/inswapper_128.onnx")

        again = swapper.getFaceSwapModel("/models/insightface/inswapper_128.onnx")
        check("cached path returns same object", again is model)

        other = swapper.getFaceSwapModel("/models/reswapper/reswapper_128.onnx")
        check("new path reloads", isinstance(other, DummySwapper) and other is not model)
        check("unload+replace updates state", swapper.FS_MODEL is other)
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

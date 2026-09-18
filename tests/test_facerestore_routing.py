"""Regression tests for face-restore model routing.

Bug being pinned: ``".onnx" in face_restore_model`` performed membership on a
dict ({"name","path"}) which is always False, so ONNX restorers (GPEN,
RestoreFormer, GFPGAN.onnx, CodeFormer.onnx) fell into the torch .pth branch
and died with ``UnpicklingError: invalid load key '\\x08'``.

Usage: python tests/test_facerestore_routing.py
"""

import os
import sys
import types
from pathlib import Path

import numpy as np

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


class _FakeInput:
    def __init__(self, name):
        self.name = name


class FakeOrtSession:
    """Minimal ORT session: records the feed it was given."""

    last_feed = None
    input_names = ("input",)

    def get_inputs(self):
        return [_FakeInput(n) for n in FakeOrtSession.input_names]

    def run(self, _names, feed):
        FakeOrtSession.last_feed = feed
        h, w = 512, 512
        return [np.zeros((1, 3, h, w), dtype=np.float32)]


def main():
    sys.path.insert(0, str(REPO / "tests"))
    from smoke_import import install_stubs

    install_stubs()
    sys.path.insert(0, str(REPO))

    from rfactor.faceboost import restorer
    from rfactor.utils import run_facerestore_onnx

    # ---- run_facerestore_onnx behavior -------------------------------------
    FakeOrtSession.input_names = ("input", "weight")
    crop = np.zeros((128, 128, 3), dtype=np.uint8)
    out = run_facerestore_onnx(FakeOrtSession(), crop)
    check("named inputs fed (input+weight)", set(FakeOrtSession.last_feed) == {"input", "weight"})
    check("output normalized to uint8 BGR", out.dtype == np.uint8)

    FakeOrtSession.input_names = ("Input",)  # case variation, single input
    run_facerestore_onnx(FakeOrtSession(), crop)
    check("single odd-named input gets the crop (case-insensitive fallback)",
          list(FakeOrtSession.last_feed) == ["Input"])

    # ---- get_restored_face routing: dict model with .onnx name must take the
    #      ONNX branch (never torch.load) ------------------------------------
    torch_load_calls = []

    def _guard(*a, **k):
        torch_load_calls.append(a)
        raise AssertionError("torch.load must not be called for ONNX models")

    real_torch_load = sys.modules["torch"].load if hasattr(sys.modules["torch"], "load") else None
    sys.modules["torch"].load = _guard
    real_create_session = restorer.create_session
    restorer.create_session = lambda path, providers=None: FakeOrtSession()

    # stub-env shims for the pre-inference tensor prep (real torch unavailable)
    class _T:
        def __init__(self, a):
            self.a = a
        def unsqueeze(self, i):
            return _T(np.expand_dims(self.a, i))
        def to(self, d):
            return self

    restorer.img2tensor = lambda img, bgr2rgb=True, float32=False: _T(img.transpose(2, 0, 1))
    restorer.normalize = lambda *a, **k: None

    class _NullCtx:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    _torch_mod = sys.modules["torch"]
    _torch_mod.no_grad = _NullCtx
    class _Cuda:
        @staticmethod
        def empty_cache():
            pass
        @staticmethod
        def is_available():
            return False
    _torch_mod.cuda = _Cuda
    try:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            existing = os.path.join(td, "GPEN-BFR-512.onnx")
            open(existing, "wb").write(b"stub-onnx")
            crop = np.zeros((128, 128, 3), dtype=np.uint8)
            restored, scale = restorer.get_restored_face(
                crop,
                face_restore_model={"name": "GPEN-BFR-512.onnx", "path": existing},
                face_restore_visibility=1,
                codeformer_fidelity=0.5,
            )
            check("dict+.onnx routes to ONNX branch (no torch.load)", not torch_load_calls)
            check("ONNX restore returns an image", isinstance(restored, np.ndarray) and restored.dtype == np.uint8)
            check("scale computed from model name (512/128)", scale == 4.0)

            # plain-name string form too (resolves via folder_paths)
            import folder_paths

            existing2 = os.path.join(td, "restoreformer.onnx")
            open(existing2, "wb").write(b"stub-onnx")
            original_gfp = folder_paths.get_full_path
            folder_paths.get_full_path = lambda folder, name: existing2 if name == "restoreformer.onnx" else None
            try:
                restored, scale = restorer.get_restored_face(
                    crop, face_restore_model="restoreformer.onnx",
                    face_restore_visibility=1, codeformer_fidelity=0.5,
                )
                check("string+.onnx routes to ONNX branch", not torch_load_calls)
            finally:
                folder_paths.get_full_path = original_gfp
    finally:
        restorer.create_session = real_create_session
        if real_torch_load is not None:
            sys.modules["torch"].load = real_torch_load

    # ---- restore loader must not offer swap-family files --------------------
    from rfactor import loaders

    check("swap-family files filtered from restore choices",
          all(not any(h in n.lower() for h in loaders._SWAP_FAMILY_HINTS)
              for n in loaders.get_restore_model_choices()))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

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


class _FakeHelper:
    """Minimal FaceRestoreHelper stand-in for the main restore path."""

    def __init__(self, crops, face_size=512):
        self.cropped_faces = list(crops)
        self.det_faces = [(10.0, 10.0, 210.0, 210.0, 0.99) for _ in crops]
        self.captured = []  # survives clean_all()
        self.input_img = None
        self.face_size = (face_size, face_size)

    def clean_all(self):
        pass

    def read_image(self, img):
        self.input_img = img

    def get_face_landmarks_5(self, **k):
        pass

    def align_warp_face(self):
        pass

    def add_restored_face(self, face):
        self.captured.append(face)

    def get_inverse_affine(self, x):
        pass

    def paste_faces_to_input_image(self, **k):
        return self.input_img.copy()


class _WhiteOrt:
    """ORT session that 'restores' by returning an all-white face."""

    def __init__(self):
        self.fed = False

    def get_inputs(self):
        class _I:
            name = "input"
            shape = [1, 3, 512, 512]
        return [_I()]

    def run(self, _names, feed):
        self.fed = True
        return [np.ones((1, 3, 512, 512), dtype=np.float32)]


class _ImgWrap:
    def __init__(self, arr):
        self.arr = arr
    def cpu(self):
        return self
    def numpy(self):
        return self.arr


def test_main_path_onnx(pkg, check):
    """Regression: the ONNX branch in restore_face() used to fall through to
    a leftover `del output` -> UnboundLocalError -> except -> the UNRESTORED
    crop was pasted back, making every ONNX restorer produce identical output.
    Also pins .onnx-before-codeformer dispatch (codeformer.onnx used to hit
    torch.load) and the empty-model-name guard."""
    import importlib
    import tempfile

    nodes_mod = importlib.import_module("ReAactor_node_ReFactor.nodes")

    crop = np.zeros((512, 512, 3), dtype=np.uint8)
    helper = _FakeHelper([crop])
    white = _WhiteOrt()

    saved = {k: getattr(nodes_mod, k) for k in ("FACE_SIZE", "FACE_HELPER", "ARCH_REGISTRY")}
    nodes_mod.FACE_HELPER = helper
    nodes_mod.FACE_SIZE = 512
    nodes_mod.create_session = lambda path, providers=None: white
    class _T:  # minimal tensor stand-in (the ONNX branch ignores it)
        def unsqueeze(self, i):
            return self
        def to(self, d):
            return self
    nodes_mod.img2tensor = lambda img, bgr2rgb=True, float32=False: _T()
    nodes_mod.normalize = lambda *a, **k: None

    class _NoArch:
        @staticmethod
        def get(*a, **k):
            raise AssertionError("CodeFormer arch must not be built for an ONNX restore model")

    nodes_mod.ARCH_REGISTRY = _NoArch
    _torch = sys.modules["torch"]
    saved_torch = {k: getattr(_torch, k, None) for k in ("from_numpy", "no_grad", "cuda", "load")}

    class _Ctx:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def __call__(self, f=None, **k):
            return self if f is None else f

    class _NoGradFactory2:
        def __call__(self, *a, **k):
            return _Ctx()
    _torch.from_numpy = lambda a: a
    _torch.no_grad = _NoGradFactory2()
    _torch.cuda = types.SimpleNamespace(empty_cache=lambda: None, is_available=lambda: False)
    def _no_torch_load(*a, **k):
        raise AssertionError("torch.load must not be called for an ONNX restore model")
    _torch.load = _no_torch_load
    try:
        ns = types.SimpleNamespace(restore_swapped_only=False, last_swapped_bboxes=None)
        with tempfile.TemporaryDirectory() as td:
            model = os.path.join(td, "GPEN-BFR-512.onnx")
            open(model, "wb").write(b"stub")
            inp = _ImgWrap(np.zeros((1, 512, 512, 3), dtype=np.float32))
            main_cls = pkg.NODE_CLASS_MAPPINGS["ReFactorFaceSwap"]
            main_cls.restore_face(
                ns, inp, {"name": "GPEN-BFR-512.onnx", "path": model}, 1.0, 0.5, "retinaface_resnet50")
            check("main path: ONNX session actually fed", white.fed)
            check("main path: restored face differs from crop (no silent except-fallback)",
                  len(helper.captured) == 1 and helper.captured[-1].min() == 255)

            white.fed = False
            model2 = os.path.join(td, "codeformer.onnx")
            open(model2, "wb").write(b"stub")
            main_cls = pkg.NODE_CLASS_MAPPINGS["ReFactorFaceSwap"]
            main_cls.restore_face(
                ns, inp, {"name": "codeformer.onnx", "path": model2}, 1.0, 0.5, "retinaface_resnet50")
            check("main path: codeformer.onnx routed to ONNX session (never torch.load)", white.fed)

            # empty restore-model name -> restore skipped, returns input unchanged
            inp2 = _ImgWrap(np.full((1, 64, 64, 3), 0.5, dtype=np.float32))
            out = main_cls.restore_face(
                ns, inp2, {"name": None, "path": None}, 1.0, 0.5, "retinaface_resnet50")
            check("main path: empty model name skips restore gracefully", out is inp2)
    finally:
        for k, v in saved.items():
            setattr(nodes_mod, k, v)
        for k, v in saved_torch.items():
            if v is not None:
                setattr(_torch, k, v)


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
        """Context manager AND decorator, like real torch.no_grad."""
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def __call__(self, f=None, **k):
            return self if f is None else f

    _torch_mod = sys.modules["torch"]
    class _NoGradFactory:
        def __call__(self, *a, **k):
            return _NullCtx()
    _torch_mod.no_grad = _NoGradFactory()
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

    # ---- Face Boost with NO restore model connected: skip, don't crash ------
    crop = np.zeros((64, 64, 3), dtype=np.uint8)
    out, scale = restorer.get_restored_face(crop, None, 1, 0.5)
    check("boost with None model: skipped gracefully", scale == 1.0 and (out == crop).all())
    out, scale = restorer.get_restored_face(crop, {"name": None, "path": None}, 1, 0.5)
    check("boost with empty model dict: skipped gracefully", scale == 1.0 and (out == crop).all())

    # ---- swap models: analysis models (antelopev2 etc.) must be rejected -----
    import tempfile
    from rfactor.engine.inswap import INSwapper

    with tempfile.TemporaryDirectory() as td:
        fake = os.path.join(td, "genderage.onnx")
        open(fake, "wb").write(b"stub-onnx")
        # stand-in ORT session: single 'input' (analysis-model shape), no 'target'/'source'
        import rfactor.ort_utils as _ort_utils
        _ort_mod = _ort_utils.get_onnxruntime()

        class _FakeInp:
            name = "input"
            shape = [1, 3, 112, 112]

        class _FakeOut:
            name = "output"

        class _FakeSess:
            def get_inputs(self):
                return [_FakeInp()]
            def get_outputs(self):
                return [_FakeOut()]

        _ort_mod.InferenceSession = lambda *a, **k: _FakeSess()
        try:
            INSwapper(fake, providers=["CPUExecutionProvider"])
            check("analysis model rejected as swapper", False)
        except ValueError as e:
            check("analysis model rejected as swapper", "not a supported face SWAP model" in str(e))

    # ---- swap loader choices: only swap-family files under insightface/ ------
    with tempfile.TemporaryDirectory() as td:
        ins_dir = os.path.join(td, "insightface")
        res_dir = os.path.join(td, "reswapper")
        hyp_dir = os.path.join(td, "hyperswap")
        for d in (ins_dir, res_dir, hyp_dir):
            os.makedirs(d)
        for name in ("inswapper_128.onnx", "genderage.onnx", "scrfd_10g_bnkps.onnx",
                     "glintr100.onnx", "1k3d68.onnx", "2d106det.onnx"):
            open(os.path.join(ins_dir, name), "wb").write(b"x")
        open(os.path.join(res_dir, "reswapper_256.onnx"), "wb").write(b"x")
        open(os.path.join(hyp_dir, "hyperswap_1a_256.onnx"), "wb").write(b"x")

        from rfactor import loaders
        saved_paths = {k: getattr(loaders.model_paths, k)
                       for k in ("insightface_path", "reswapper_path", "hyperswap_path")}
        loaders.model_paths.insightface_path = ins_dir
        loaders.model_paths.reswapper_path = res_dir
        loaders.model_paths.hyperswap_path = hyp_dir
        try:
            names = [n for n, _ in loaders.get_swap_model_choices()]
            check("insightface analysis models not offered as swappers",
                  "genderage.onnx" not in names and "scrfd_10g_bnkps.onnx" not in names
                  and "glintr100.onnx" not in names and "1k3d68.onnx" not in names
                  and "2d106det.onnx" not in names)
            check("real swap models still offered",
                  set(names) == {"inswapper_128.onnx", "reswapper_256.onnx", "hyperswap_1a_256.onnx"})
        finally:
            for k, v in saved_paths.items():
                setattr(loaders.model_paths, k, v)

    # ---- main restore path end-to-end (stubbed helper + ORT) ----------------
    import importlib
    sys.path.insert(0, str(REPO.parent))
    pkg = importlib.import_module("ReAactor_node_ReFactor")
    test_main_path_onnx(pkg, check)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

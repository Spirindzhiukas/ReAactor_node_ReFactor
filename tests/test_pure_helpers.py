"""Unit tests for the dependency-free helpers (no torch / comfy required).

Usage: python tests/test_pure_helpers.py
"""

import os
import sys
import tempfile
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


def stub_comfy():
    tmp = tempfile.mkdtemp(prefix="rfactor_models_")
    fp = types.ModuleType("folder_paths")
    fp.models_dir = tmp
    fp.folder_names_and_paths = {}
    fp.supported_pt_extensions = {".pt", ".pth", ".safetensors"}
    fp.add_model_folder_path = lambda *a, **k: None
    fp.get_filename_list = lambda name: []
    fp.get_full_path = lambda name, f: None
    sys.modules["folder_paths"] = fp
    return tmp


def main():
    stub_comfy()
    # minimal torch stub: torch_utils only imports it at module level for typing
    torch = types.ModuleType("torch")
    torch.__version__ = "0.0-stub"
    torch.Tensor = object
    torch.nn = types.SimpleNamespace(Module=object)
    sys.modules["torch"] = torch
    st = types.ModuleType("safetensors")
    st_torch = types.ModuleType("safetensors.torch")
    st_torch.save_file = lambda *a, **k: None
    st_torch.safe_open = lambda *a, **k: None
    sys.modules["safetensors"] = st
    sys.modules["safetensors.torch"] = st_torch
    comfy = types.ModuleType("comfy")
    mm = types.ModuleType("comfy.model_management")
    mm.get_torch_device = lambda: types.SimpleNamespace(index=0, type="cpu")
    mm.processing_interrupted = lambda: False
    mm.intermediate_device = mm.get_torch_device
    cu = types.ModuleType("comfy.utils")
    cu.ProgressBar = lambda n=0: types.SimpleNamespace(update=lambda *a: None, current=0)
    cu.load_torch_file = lambda *a, **k: {}
    sys.modules["comfy"] = comfy
    sys.modules["comfy.model_management"] = mm
    sys.modules["comfy.utils"] = cu
    sys.path.insert(0, str(REPO))

    # ---- stat_mode --------------------------------------------------------
    import numpy as np
    from rfactor.torch_utils import stat_mode

    emb = np.array([[1, 2, 3], [1, 2, 9], [5, 2, 3]], dtype=np.float32)
    mode = stat_mode(emb, axis=0)
    # col0: tie 1 vs 5 (count 2 vs 1) -> 1 ; col1: all 2 -> 2 ; col2: tie 3 -> 3
    check("stat_mode picks smallest-on-tie", np.allclose(mode.flatten(), [1, 2, 3]))
    check("stat_mode keeps old-scipy shape", mode.shape == (1, 3))

    # ---- DLSS dll discovery ------------------------------------------------
    from rfactor import model_paths
    from rfactor.dlssnr import discovery

    os.makedirs(model_paths.DLSSNR_MODELS_PATH, exist_ok=True)
    check("no dll sets initially", discovery.discover_dll_sets() == [])
    check("combo always has refresh", "refresh" in discovery.combo_choices())

    v1 = os.path.join(model_paths.DLSSNR_MODELS_PATH, "dlss5-rc1")
    os.makedirs(v1)
    check("empty dir is not a set", discovery.discover_dll_sets() == [])
    for dll in discovery._REQUIRED_DLLS:
        open(os.path.join(v1, dll), "wb").write(b"x")
    sets = discovery.discover_dll_sets()
    check("complete set discovered", len(sets) == 1 and sets[0]["name"] == "dlss5-rc1" and sets[0]["complete"])
    check("resolve by name", discovery.resolve_dll_dir("dlss5-rc1") == v1)
    check("resolve auto -> complete set", discovery.resolve_dll_dir("auto") == v1)

    v2 = os.path.join(model_paths.DLSSNR_MODELS_PATH, "dlss5-rc2")
    os.makedirs(v2)
    for dll in discovery._REQUIRED_DLLS[:2]:  # incomplete set
        open(os.path.join(v2, dll), "wb").write(b"x")
    sets = discovery.discover_dll_sets()
    check("two sets listed, incomplete flagged",
          len(sets) == 2 and sorted(s["complete"] for s in sets) == [False, True])
    check("auto prefers the complete set", discovery.resolve_dll_dir("auto") == v1)
    check("missing name falls back", discovery.resolve_dll_dir("nope") in (v1, v2))

    # ---- safe_download -----------------------------------------------------
    from rfactor.download import safe_download

    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "src.bin")
        payload = b"A" * 4096
        open(src, "wb").write(payload)
        dst = os.path.join(td, "sub", "dst.bin")
        safe_download("file://" + src, dst, "dst.bin", min_bytes=1024)
        check("download lands atomically", open(dst, "rb").read() == payload and not os.path.exists(dst + ".part"))

        try:
            safe_download("file://" + src, os.path.join(td, "small.bin"), "small.bin", min_bytes=1 << 20)
            check("min-size rejects tiny payloads", False)
        except IOError:
            check("min-size rejects tiny payloads", not os.path.exists(os.path.join(td, "small.bin"))
                  and not os.path.exists(os.path.join(td, "small.bin.part")))

        os.environ["REFACTOR_NO_AUTO_DOWNLOAD"] = "1"
        try:
            safe_download("file://" + src, os.path.join(td, "blocked.bin"), "blocked.bin", min_bytes=1)
            check("opt-out env blocks downloads", False)
        except FileNotFoundError as e:
            check("opt-out env blocks downloads", "REFACTOR_NO_AUTO_DOWNLOAD" in str(e))
        finally:
            del os.environ["REFACTOR_NO_AUTO_DOWNLOAD"]

    # ---- masking ops (pure numpy/cv2) --------------------------------------
    from rfactor.masking import ops

    m = ops.bboxes_to_mask({"x": 10, "y": 10, "width": 20, "height": 20}, 64, 64)
    check("bbox mask covers box", m[10, 10] == 1.0 and m[9, 9] == 0.0 and m[29, 29] == 1.0 and m[31, 31] == 0.0)
    m2 = ops.bboxes_to_mask({"x": 0.0, "y": 0.0, "width": 0.5, "height": 0.5}, 64, 64)
    check("normalized bbox accepted", m2[31, 31] == 1.0 and m2[33, 33] == 0.0)
    m3 = ops.bboxes_to_mask([{"x": 1, "y": 1, "width": 5, "height": 5},
                             {"x": 30, "y": 30, "width": 5, "height": 5}], 64, 64)
    check("bbox union", m3[2, 2] == 1.0 and m3[32, 32] == 1.0)

    class _F:
        def __init__(self, bbox):
            self.bbox = bbox

    img = np.zeros((100, 100, 3), np.uint8)
    fm = ops.face_region_mask(img, [_F([40, 40, 60, 60])], crop_factor=1.5, falloff=3)
    check("face region mask centered", fm[50, 50] == 1.0 and fm[0, 0] == 0.0)
    band = fm[64:80, 50]  # just outside the ellipse edge -> soft falloff
    check("face region soft edges", np.any(band > 0) and np.any(band < 1) and band[-1] == 0.0)

    grown = ops.grow_mask(m3, 4)
    check("grow expands", grown[1, 1] == 1.0 and grown.sum() > m3.sum())
    shrunk = ops.grow_mask(m3, -4)
    check("shrink contracts", shrunk.sum() < m3.sum())
    closed = ops.apply_morphology(grown, "close", 3)
    check("morphology runs", closed.shape == m3.shape)
    feathered = ops.feather_mask(m3.astype(np.float32), 5, 1.0)
    check("feather blurs", feathered.max() < 1.0 and feathered.sum() > 0)

    base = np.zeros((4, 4, 3), np.uint8)
    overlay = np.full((4, 4, 3), 255, np.uint8)
    half = np.zeros((4, 4), np.float32); half[2:, :] = 1.0
    comp = ops.composite(base, overlay, half)
    check("composite blends by mask", comp[0, 0, 0] == 0 and comp[3, 0, 0] == 255)

    # ---- env flags ---------------------------------------------------------
    from rfactor import env

    os.environ["REFACTOR_SKIP_INSTALL"] = "1"
    check("skip flag reads true", env._flag("REFACTOR_SKIP_INSTALL"))
    del os.environ["REFACTOR_SKIP_INSTALL"]

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

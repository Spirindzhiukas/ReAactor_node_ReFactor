"""Import smoke test for the nodepack.

Runs the full package import with stubbed ComfyUI/torch/onnxruntime modules
to validate the import graph (relative imports, no sys.path hacks, no missing
names) and to construct every node's INPUT_TYPES.

Usage:  python tests/smoke_import.py [nodepack_dir]
"""

import importlib
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def install_stubs():
    # ---- magic module factory -------------------------------------------
    class _Stub:
        def __init__(self, name=""):
            object.__setattr__(self, "_stub_name", name)

        def __getattr__(self, item):
            if item.startswith("__") and item.endswith("__"):
                raise AttributeError(item)
            child = _Stub(f"{object.__getattribute__(self, '_stub_name')}.{item}")
            object.__setattr__(self, item, child)
            return child

        def __call__(self, *a, **k):
            return _Stub("call")

        def __setattr__(self, k, v):
            object.__setattr__(self, k, v)

        def __getitem__(self, k):
            return _Stub("item")

        def __iter__(self):
            return iter(())

        def __repr__(self):
            return f"<stub {object.__getattribute__(self, '_stub_name')}>"

    magic = types.ModuleType

    def magic_module(name):
        mod = magic(name)

        def getattr_(item):
            if item in ("__all__", "__path__", "__file__"):
                raise AttributeError(item)
            stub = _Stub(f"{name}.{item}")
            setattr(mod, item, stub)
            return stub

        mod.__getattr__ = getattr_
        return mod

    # ---- torch -----------------------------------------------------------
    class _Module:
        def __init__(self, *a, **k):
            pass

        def __call__(self, *a, **k):
            return self

        def __getattr__(self, item):
            return _Stub(f"nn.{item}")

    class _Tensor:
        pass

    torch = magic_module("torch")
    torch.__version__ = "2.6.0+cpu.stub"

    real_module = type("Module", (), {"__init_subclass__": classmethod(lambda cls, **k: None)})
    real_batchnorm = type("_BatchNorm", (), {"__init_subclass__": classmethod(lambda cls, **k: None)})

    def make_pkg(name, **attrs):
        mod = magic_module(name)
        mod.__path__ = []
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod
        return mod

    nn = make_pkg("torch.nn", Module=real_module)
    torch.nn = nn
    make_pkg("torch.nn.modules")
    make_pkg("torch.nn.modules.batchnorm", _BatchNorm=real_batchnorm)
    make_pkg("torch.nn.modules.conv")
    make_pkg("torch.nn.modules.pooling")
    make_pkg("torch.nn.modules.activation")
    make_pkg("torch.nn.modules.linear")
    make_pkg("torch.nn.modules.normalization")
    make_pkg("torch.nn.modules.padding")
    make_pkg("torch.nn.modules.upsampling")
    make_pkg("torch.nn.init")

    class _F:
        @staticmethod
        def interpolate(*a, **k):
            raise NotImplementedError("runtime op in stub env")

    torch.nn.functional = _F()
    sys.modules["torch.nn.functional"] = _F()

    class _TorchVersion(str):
        pass

    torch.Tensor = _Tensor
    torch.device = lambda *a, **k: "cpu"
    torch.float32 = "float32"
    torch.from_numpy = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("runtime op in stub env"))
    sys.modules["torch"] = torch
    sys.modules["torch.utils"] = magic_module("torch.utils")
    sys.modules["torch.hub"] = magic_module("torch.hub")
    sys.modules["torchvision"] = magic_module("torchvision")

    # ---- safetensors -------------------------------------------------------
    st = magic_module("safetensors")
    st_torch = magic_module("safetensors.torch")
    st_torch.save_file = lambda *a, **k: None
    st_torch.safe_open = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("runtime op in stub env"))
    sys.modules["safetensors"] = st
    sys.modules["safetensors.torch"] = st_torch

    # ---- comfy -------------------------------------------------------------
    comfy = magic_module("comfy")
    mm = magic_module("comfy.model_management")

    def get_torch_device():
        class Dev:
            index = 0
            type = "cpu"

        return Dev()

    mm.get_torch_device = get_torch_device
    mm.processing_interrupted = lambda: False
    mm.intermediate_device = get_torch_device
    mm.load_model_gpu = lambda *a, **k: None
    cu = magic_module("comfy.utils")
    cu.ProgressBar = lambda n=0: types.SimpleNamespace(update=lambda *a: None, current=0)
    cu.load_torch_file = lambda *a, **k: {}
    cu.common_upscale = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("runtime op in stub env"))
    sys.modules["comfy"] = comfy
    sys.modules["comfy.model_management"] = mm
    sys.modules["comfy.utils"] = cu

    # ---- onnxruntime (stub: CPU provider only) ------------------------------
    ort = magic_module("onnxruntime")
    ort.get_available_providers = lambda: ["CPUExecutionProvider"]
    ort.set_default_logger_severity = lambda sev: None
    ort.InferenceSession = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("runtime inference not available in stub env"))
    sys.modules["onnxruntime"] = ort

    # ---- yaml (comfy core dep; stub for the sandbox) -----------------------
    yaml = magic_module("yaml")
    yaml.safe_load = lambda *a, **k: {}
    yaml.load = lambda *a, **k: {}
    sys.modules["yaml"] = yaml

    # ---- folder_paths ------------------------------------------------------
    tmp = tempfile.mkdtemp(prefix="rfactor_models_")
    fp = magic_module("folder_paths")
    fp.models_dir = tmp
    fp.folder_names_and_paths = {}
    fp.supported_pt_extensions = {".ckpt", ".pt", ".pt2", ".bin", ".pth", ".safetensors", ".pkl", ".sft"}
    fp.add_model_folder_path = lambda name, path, is_default=False: None
    fp.get_filename_list = lambda folder_name: []
    fp.get_full_path = lambda folder_name, name: None
    fp.supported_pt_extensions  # noqa
    sys.modules["folder_paths"] = fp
    return tmp


def main():
    tmp = install_stubs()
    sys.path.insert(0, str(REPO.parent))
    pkg_name = REPO.name
    try:
        pkg = importlib.import_module(pkg_name)
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"\nFAIL: package import raised {type(e).__name__}: {e}")
        return 1

    print(f"\n[OK] imported {pkg_name} __version__={pkg.__version__}")
    print(f"[OK] registered {len(pkg.NODE_CLASS_MAPPINGS)} nodes")

    # instantiate INPUT_TYPES for every node (classmethod, no instance side effects)
    problems = []
    for name, cls in sorted(pkg.NODE_CLASS_MAPPINGS.items()):
        try:
            types_def = cls.INPUT_TYPES()
            assert "required" in types_def, "missing 'required'"
        except Exception as e:
            problems.append((name, repr(e)))
    if problems:
        for name, err in problems:
            print(f"[FAIL] INPUT_TYPES {name}: {err}")
        return 1

    # --- signature gate: every optional socket needs a defaulted parameter ---
    # (ComfyUI omits disconnected optional sockets from the execute() call, so a
    #  required parameter for an optional socket raises TypeError at runtime.)
    import inspect

    sig_problems = []
    for name, cls in sorted(pkg.NODE_CLASS_MAPPINGS.items()):
        fn = getattr(cls, cls.FUNCTION, None)
        if fn is None:
            sig_problems.append((name, f"FUNCTION {cls.FUNCTION!r} not found on class"))
            continue
        params = inspect.signature(fn).parameters
        has_varkw = any(pp.kind == pp.VAR_KEYWORD for pp in params.values())
        tdef = cls.INPUT_TYPES()
        for sock in tdef.get("required", {}):
            if sock not in params and not has_varkw:
                sig_problems.append((name, f"required socket '{sock}' has no execute() parameter"))
        for sock in tdef.get("optional", {}):
            if has_varkw:
                continue
            if sock not in params:
                sig_problems.append((name, f"optional socket '{sock}' has no execute() parameter"))
            elif params[sock].default is inspect._empty:
                sig_problems.append((name, f"optional socket '{sock}' is a REQUIRED parameter "
                                           "(crashes when the socket is unconnected)"))
    if sig_problems:
        for name, err in sig_problems:
            print(f"[FAIL] signature {name}: {err}")
        return 1
    print("[OK] every socket maps to a execute()-parameter with a safe default")

    names = sorted(pkg.NODE_CLASS_MAPPINGS)
    print("[OK] all node INPUT_TYPES construct cleanly:")
    for n in names:
        print(f"     - {n}")
    print(f"[OK] models dir used for the test: {tmp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

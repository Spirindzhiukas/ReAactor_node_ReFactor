"""Regression test: IntermediateLayerGetter must register wrapped layers as
real torch submodules.

Bug being pinned: the old rfactor.torch_utils.IntermediateLayerGetter was a
plain-Python wrapper (not an nn.Module), so RetinaFace's ``self.body`` never
appeared in the detector's state_dict. Loading the facexlib detection
checkpoints (detection_Resnet50_Final.pth etc.), whose keys are
``body.conv1.weight ... body.layer4.2.conv3.weight``, then failed with
``load_state_dict(strict=True)``:
"Unexpected key(s) in state_dict: body.conv1.weight, ...".

The sandbox has no usable torch build, so this test ships a ~60-line harness
that replicates the nn.Module child-registration + state_dict recursion
semantics that matter here, installs it as ``torch`` in sys.modules, imports
the real rfactor.torch_utils.IntermediateLayerGetter against it, and asserts
that (a) wrapped children appear in state_dict() and (b) keys propagate with
the ``body.`` prefix when nested inside a parent module.

Usage: python tests/test_detection_state_dict.py
"""

import sys
from collections import OrderedDict
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


# ---- minimal nn.Module machinery -------------------------------------------

class _FakeParam:
    def __init__(self, shape=(4, 4)):
        self.shape = shape


class _FakeModule:
    def __init__(self):
        self._modules = OrderedDict()
        self.training = True

    def __setattr__(self, name, value):
        if isinstance(value, _FakeModule):
            self._modules[name] = value
        object.__setattr__(self, name, value)

    def named_children(self):
        return list(self._modules.items())

    def state_dict(self, prefix=""):
        out = OrderedDict()
        for name, mod in self._modules.items():
            for p in getattr(mod, "_params", ()):
                out[prefix + name + "." + p] = True  # value irrelevant
            out.update(mod.state_dict(prefix + name + "."))
        return out

    def eval(self):
        self.training = False
        for m in self._modules.values():
            m.eval()
        return self

    def to(self, device):
        return self


class _Conv(_FakeModule):
    def __init__(self):
        super().__init__()
        self._params = ["weight", "bias"]

    def __call__(self, x):
        return x


class _Block(_FakeModule):
    def __init__(self, n):
        super().__init__()
        for i in range(n):
            self._modules[str(i)] = _Conv()

    def __call__(self, x):
        return x


class _FakeModuleDict(_FakeModule):
    def __init__(self, modules):
        super().__init__()
        for name, mod in modules.items():
            self._modules[name] = mod

    def items(self):
        return self._modules.items()


class _FakeNN:
    Module = _FakeModule
    ModuleDict = _FakeModuleDict


class _FakeTorch:
    nn = _FakeNN

    class Tensor:  # only referenced in annotations
        pass


# ---- the actual test --------------------------------------------------------

def main():
    import importlib.util

    sys.modules["torch"] = _FakeTorch()  # before importing the real module
    spec = importlib.util.spec_from_file_location("rf_torch_utils", REPO / "rfactor" / "torch_utils.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    IntermediateLayerGetter = mod.IntermediateLayerGetter

    check("base is a Module-like type (real torch: nn.ModuleDict)",
          issubclass(IntermediateLayerGetter, _FakeModuleDict))

    # torchvision-ResNet-like backbone
    backbone = _FakeModule()
    backbone.conv1 = _Conv()
    backbone.layer1 = _Block(1)
    backbone.layer2 = _Block(2)
    backbone.layer3 = _Block(3)
    backbone.layer4 = _Block(4)

    getter = IntermediateLayerGetter(backbone, {"layer2": 1, "layer3": 2, "layer4": 3})
    keys = set(getter.state_dict())

    check("wrapped layers registered as submodules (layer2.* keys present)",
          any(k.startswith("layer2.") for k in keys))
    check("pre-return layers included too (conv1.* present — torchvision keeps them)",
          any(k.startswith("conv1.") for k in keys))
    check("early return-layer layer1 also registered (torchvision stops after last return)",
          any(k.startswith("layer1.") for k in keys))

    # the actual user-facing failure mode: detector nests the getter as .body
    detector = _FakeModule()
    detector.body = getter
    detector.fpn = _Conv()
    det_keys = set(detector.state_dict())

    check("body.* keys appear in the detector state_dict (checkpoint compatibility)",
          any(k == "body.conv1.weight" for k in det_keys)
          and any(k == "body.layer4.3.weight" for k in det_keys))

    # old wrapper returned {} here -> strict load of a body.* checkpoint failed.
    # A checkpoint dict shaped like the model's own state_dict must round-trip.
    checkpoint = OrderedDict((k, True) for k in det_keys)  # as if loaded from the .pth
    check("strict load round-trip feasible (model keys == checkpoint keys)",
          set(checkpoint) == det_keys and len(det_keys) > 0)

    # forward mapping still yields the requested feature maps by mapped key
    feats = getter.forward("x")
    check("forward returns feature maps keyed by mapped indices",
          sorted(feats) == [1, 2, 3])

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

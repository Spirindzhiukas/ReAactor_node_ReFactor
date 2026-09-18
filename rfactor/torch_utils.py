"""Small pure-torch/numpy helpers that replace one-off torchvision / scipy uses.

The nodepack previously imported torchvision (normalize, masks_to_boxes,
make_grid) and scipy (stats.mode) for these trivialities — two heavyweight,
version-coupled dependencies for a handful of lines of math. Implemented here
with plain torch/numpy instead.
"""

import numpy as np
import torch
from collections import OrderedDict

# Real torch: proper ModuleDict base so wrapped layers register as submodules.
# Stub torch (test envs without a usable build): plain object — the class is
# only ever constructed with real torch.
_ModuleDictBase = getattr(torch, "nn", None) and getattr(torch.nn, "ModuleDict", None)
if not isinstance(_ModuleDictBase, type):
    _ModuleDictBase = object


def normalize_(tensor: torch.Tensor, mean, std, inplace: bool = True) -> torch.Tensor:
    """In-place normalize a (..., C, H, W) tensor like torchvision's normalize."""
    if not inplace:
        tensor = tensor.clone()
    mean = torch.as_tensor(mean, dtype=tensor.dtype, device=tensor.device)
    std = torch.as_tensor(std, dtype=tensor.dtype, device=tensor.device)
    tensor.sub_(mean[:, None, None]).div_(std[:, None, None])
    return tensor


def masks_to_boxes(masks: torch.Tensor) -> torch.Tensor:
    """(N, H, W) binary masks -> (N, 4) [x1, y1, x2, y2] boxes (same contract as
    torchvision.ops.masks_to_boxes). Empty masks produce a zero box."""
    n = masks.shape[0]
    device = masks.device
    boxes = torch.zeros((n, 4), dtype=torch.float32, device=device)
    if n == 0:
        return boxes
    flat = masks.reshape(n, -1)
    any_mask = flat.any(dim=1)
    if not any_mask.any():
        return boxes
    idx = torch.arange(flat.shape[1], device=device)
    w = masks.shape[2]
    ys = (idx // w).float()
    xs = (idx % w).float()
    for i in torch.nonzero(any_mask, as_tuple=False).flatten().tolist():
        m = flat[i]
        xs_m, ys_m = xs[m], ys[m]
        boxes[i, 0] = xs_m.min()
        boxes[i, 1] = ys_m.min()
        boxes[i, 2] = xs_m.max()
        boxes[i, 3] = ys_m.max()
    return boxes


def make_grid(tensor: torch.Tensor, nrow: int = 8) -> torch.Tensor:
    """Minimal CHW-batch grid used only by tensor2img (replaces torchvision's)."""
    if tensor.dim() == 3:
        return tensor
    n, c, h, w = tensor.shape
    ncol = int(np.ceil(n / max(nrow, 1)))
    grid = torch.full((c, ncol * h, nrow * w), 0.5, dtype=tensor.dtype, device=tensor.device)
    for i in range(n):
        r, col = divmod(i, nrow)
        grid[:, r * h:(r + 1) * h, col * w:(col + 1) * w] = tensor[i]
    return grid


def stat_mode(values: np.ndarray, axis: int = 0) -> np.ndarray:
    """Row-wise mode (smallest value on ties) — numpy replacement for the single
    ``scipy.stats.mode`` call, matching its pre-1.9 return semantics."""
    arr = np.asarray(values)
    arr = np.moveaxis(arr, axis, 0)
    flat = arr.reshape(arr.shape[0], -1)
    out = np.zeros(flat.shape[1], dtype=arr.dtype)
    for i in range(flat.shape[1]):
        col = flat[:, i]
        vals, counts = np.unique(col, return_counts=True)
        max_count = counts.max()
        out[i] = vals[counts == max_count].min()
    return out.reshape(arr.shape[1:] if axis != 0 else (1,) + arr.shape[1:])


class IntermediateLayerGetter(_ModuleDictBase):
    """Stand-in for torchvision.models._utils.IntermediateLayerGetter.

    MUST register the wrapped layers as real submodules (nn.ModuleDict): the
    RetinaFace detector stores this as ``self.body``, and facexlib checkpoints
    contain ``body.*`` keys — with a plain-Python wrapper those keys never
    appear in state_dict() and load_state_dict(strict=True) fails with
    "Unexpected key(s): body.conv1.weight, ...". Semantics match torchvision:
    run children in order, collect the feature maps named in return_layers
    (keyed by their mapped value), stop after the last requested one.
    """

    def __init__(self, model: torch.nn.Module, return_layers: dict):
        if not set(return_layers).issubset(name for name, _ in model.named_children()):
            raise ValueError("return_layers are not present in model")
        layers = OrderedDict()
        remaining = dict(return_layers)
        for name, module in model.named_children():
            layers[name] = module
            if name in remaining:
                del remaining[name]
            if not remaining:
                break
        super().__init__(layers)
        self.return_layers = dict(return_layers)

    def forward(self, x):
        out = {}
        for name, module in self.items():
            x = module(x)
            if name in self.return_layers:
                out[self.return_layers[name]] = x
        return out

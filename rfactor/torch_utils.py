"""Small pure-torch/numpy helpers that replace one-off torchvision / scipy uses.

The nodepack previously imported torchvision (normalize, masks_to_boxes,
make_grid) and scipy (stats.mode) for these trivialities — two heavyweight,
version-coupled dependencies for a handful of lines of math. Implemented here
with plain torch/numpy instead.
"""

import numpy as np
import torch


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
    h, w = masks.shape[1], masks.shape[2]
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


class IntermediateLayerGetter:
    """Tiny stand-in for torchvision.models._utils.IntermediateLayerGetter:
    wraps an nn.Module and returns chosen intermediate feature maps by name."""

    def __init__(self, model: torch.nn.Module, return_layers: dict):
        self.model = model
        self.return_layers = dict(return_layers)

    def __call__(self, x):
        out = {}
        for name, module in self.model._modules.items():
            x = module(x)
            if name in self.return_layers:
                out[self.return_layers[name]] = x
        return out

    def eval(self):
        self.model.eval()
        return self

    def to(self, device):
        self.model.to(device)
        return self

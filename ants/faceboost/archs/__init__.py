"""Vendored, self-contained face-restoration architectures (GFPGANv1Clean, CodeFormer).

Importing ``codeformer_arch`` has the side effect of registering CodeFormer
in the ARCH_REGISTRY (decorator-based); GFPGAN is constructed directly via
``model_loading`` and needs no registration.
"""

from . import model_loading  # noqa: F401
from . import codeformer_arch  # noqa: F401  -- side-effect: registers "CodeFormer"
from .registry import ARCH_REGISTRY  # noqa: F401

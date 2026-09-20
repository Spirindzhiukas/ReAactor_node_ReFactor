"""ANTs package root (ex ReFactor ReFactor).

All imports in the nodepack are package-relative: nothing is ever injected
into ``sys.path`` (the #1 cause of custom-node import collisions).
"""

__version__ = "1.1.0-alpha1"

APP_TITLE = "ReActor ReFactor (Face Swap for ComfyUI)"

# --- CUDA context flags, armed as early as our code runs -------------------
# The neuroframe engine refuses its zero-copy CUDA path unless the process's
# CUDA primary context carries CU_CTX_SCHED_BLOCKING_SYNC (its own log line:
# "active CUDA primary context does not use FFmpeg blocking-sync flags"), and
# PyTorch never sets that flag. cudaSetDeviceFlags() is only legal BEFORE the
# context exists, so the attempt has to happen at import - ComfyUI loads
# custom nodes long before the first tensor reaches the GPU on most machines.
# Best effort, once, and it never raises: the log says what each route
# answered (see ants/dlsssr/cuda_flags.py).
try:                                          # pragma: no cover - env dependent
    from .dlsssr import cuda_flags as _cuda_flags
    _cuda_flags.log_early()
except Exception:                             # never break ComfyUI's startup
    pass

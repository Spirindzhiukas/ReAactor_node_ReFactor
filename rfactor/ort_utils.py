"""onnxruntime access, isolated and clash-safe.

Key properties:
- ``onnxruntime`` is imported lazily (the nodepack loads fine without it and
  every node degrades to a clear, actionable error instead of a crash).
- Both pip flavors (``onnxruntime`` and ``onnxruntime-gpu``) provide the same
  ``onnxruntime`` module; we simply use whichever is already installed and
  NEVER install one over the other (see ``install.py``).
- Execution providers are validated against ``onnxruntime.get_available_providers()``
  with CPU as the final fallback, so a GPU-less environment logs one clear
  notice instead of failing silently or choosing an impossible provider.
"""

import threading

from .env import ort_preference
from .log import logger

_lock = threading.RLock()
_ort = None
_ort_error = None
_resolved = None
_sessions = {}


def get_onnxruntime():
    """Return the onnxruntime module (lazy, cached). Raises with guidance if absent."""
    global _ort, _ort_error
    with _lock:
        if _ort is not None:
            return _ort
        try:
            import onnxruntime as ort
        except Exception as exc:  # pragma: no cover - depends on user env
            _ort_error = exc
            raise ImportError(
                "[ReFactor] The 'onnxruntime' package is required but not importable "
                f"({exc}). Install the flavor you prefer inside your ComfyUI python:  "
                "pip install onnxruntime          # CPU  (or:)  "
                "pip install onnxruntime-gpu      # NVIDIA CUDA (matches your torch/CUDA)"
            ) from exc
        _ort = ort
        ort.set_default_logger_severity(3)
        return _ort


def resolve_providers() -> list:
    """Pick execution providers that actually exist in the installed build."""
    global _resolved
    with _lock:
        if _resolved is not None:
            return list(_resolved)
        ort = get_onnxruntime()
        available = list(ort.get_available_providers())

        import torch

        desired = ort_preference()
        if desired is None:
            desired = []
            if torch.cuda.is_available():
                desired.append("CUDAExecutionProvider")
            if torch.backends.mps.is_available():
                desired.append("CoreMLExecutionProvider")
            if hasattr(torch, "dml") or hasattr(torch, "privateuseone"):
                # DirectML / privateuseone builds of onnxruntime expose "DmlExecutionProvider"
                desired.append("DmlExecutionProvider")
                desired.append("ROCMExecutionProvider")
        desired.append("CPUExecutionProvider")

        chosen = [p for p in desired if p in available]
        if not chosen:
            chosen = ["CPUExecutionProvider"]
        dropped = [p for p in desired if p not in available and p != "CPUExecutionProvider"]
        if dropped:
            logger.info(f"ONNX Runtime providers not present in this build, skipping: {dropped}")
        logger.info(f"ONNX Runtime execution providers: {chosen}")
        _resolved = chosen
        return list(chosen)


def create_session(model_path: str, providers=None):
    """Create (and cache) an InferenceSession for a model path."""
    ort = get_onnxruntime()
    providers = providers or resolve_providers()
    key = (os_path_key(model_path), tuple(providers))
    with _lock:
        session = _sessions.get(key)
        if session is None:
            session = ort.InferenceSession(model_path, providers=providers)
            _sessions[key] = session
        return session


def clear_sessions() -> None:
    with _lock:
        _sessions.clear()


def os_path_key(path: str) -> str:
    import os

    return os.path.realpath(path)

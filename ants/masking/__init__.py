"""Masking: ComfyUI-native mask sources + built-in face-region fallback.

``ops`` is importable standalone (pure numpy/cv2); the node class is loaded
lazily so tests and tools can use the ops without a ComfyUI environment.
"""


def __getattr__(name):
    if name == "ReFactorMaskBuilder":
        from .builder import ReFactorMaskBuilder

        return ReFactorMaskBuilder
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

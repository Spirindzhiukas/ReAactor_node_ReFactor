"""User-controllable behavior switches (environment variables).

Everything the old install script did silently is now either explicit,
interactive, or governed by one of these flags:

- ``REFACTOR_SKIP_INSTALL``     : install.py becomes a no-op report (also honored at runtime).
- ``REFACTOR_NO_AUTO_DOWNLOAD`` : never download model files automatically; missing models
                                  raise a clear error with manual download URLs instead.
- ``REFACTOR_ORT_PREFERENCE``   : comma list of preferred onnxruntime execution providers,
                                  e.g. ``CUDAExecutionProvider,CPUExecutionProvider``.
"""

import os


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


SKIP_INSTALL = _flag("REFACTOR_SKIP_INSTALL")
NO_AUTO_DOWNLOAD = _flag("REFACTOR_NO_AUTO_DOWNLOAD")


def ort_preference():
    raw = os.environ.get("REFACTOR_ORT_PREFERENCE", "").strip()
    if not raw:
        return None
    return [p.strip() for p in raw.split(",") if p.strip()]

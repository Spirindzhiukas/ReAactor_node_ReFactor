"""ANTs Face Nodes — face-swap nodepack for ComfyUI (ex ReActor ReFactor).

A dependency-hygiene-focused rework of the ReActor node, now under the ANTs brand:
- zero sys.path pollution (proper package-relative imports),
- minimal, clash-free dependencies (no ultralytics / segment_anything /
  albumentations / forced onnxruntime flavors),
- user-controllable install (see install.py),
- pure-Python ONNX face engine (no insightface SDK, no C++ builds),
- 3rd-party DLSS-NR DLLs stay manual, now with user-supplied version sets.
"""


# The nodepack is a proper package: nothing is ever injected into sys.path.
from .rfactor import __version__, model_paths

model_paths.register_folders()

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "__version__"]

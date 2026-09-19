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
from .ants import __version__, model_paths

model_paths.register_folders()

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

# Frontend (JS) companion for the DLSS5 node family - served from /web/
# (ComfyUI's custom-node web dir convention; NOT a "js/" folder).
WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "__version__",
           "WEB_DIRECTORY"]

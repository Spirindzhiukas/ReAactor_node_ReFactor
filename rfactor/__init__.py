"""ReFactor package root.

All imports in the nodepack are package-relative: nothing is ever injected
into ``sys.path`` (the #1 cause of custom-node import collisions).
"""

__version__ = "1.0.0-alpha1"

APP_TITLE = "ReActor ReFactor (Face Swap for ComfyUI)"

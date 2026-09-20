"""ANTs pure-Python DLSS/NGX host (`ants.dlsssr`).

Drives NVIDIA's DLSS runtimes directly from Python — no 3rd-party helper
DLLs, no compiled bridge. Two providers are implemented:

- **Super Resolution (feature 1, `nvngx_dlss.dll`)** — the regular DLSS
  upscaler: DLAA / Quality / Balanced / Performance / Ultra Performance
  modes, the J/K/L/M model presets, output resized back to the input
  resolution (see ``sr.py``);
- **Neural Rendering (feature 18, `nvngx_dlssnr.dll`)** — the DLSS5 node's
  enhancement engine under our full control (``nr.py``), replacing the
  3rd-party neuroframe helper pair (see below).

Technique provenance (documented per the owner's directive — we implement,
we do not copy):

- **HicirTech/DLSS-Video-Transcoder**, **ComfyUI-DLSS5-NR(-Linux)**,
  **ComfyUI-DLSS5-Video** — the proof that a dynamic-language FFI host can
  drive NGX end to end, including the caller-module-check workaround (a
  generated, position-independent thunk shim: the NR runtime validates the
  module NGX calls RETURN into and answers ``0xBAD00002`` otherwise, so the
  shim parks the real function addresses in data slots and ``call``s them
  with a real ``call`` — never a tail ``jmp`` — leaving the return address
  inside the helper image). The community shim ships as
  ``nvngx.dll_comfy.dll`` (not the bare ``nvngx.dll``, which only survives
  there as a legacy fallback) and that is why our default module name is
  ``nvngx.dll_ants.dll``. No license (DVT) / MIT technique reference: we
  implement from the documented ABI, zero code taken.
- **Merserk (github.com/Merserk/dlss5-visual-enhancer)** — the reference
  client for the feature-18 control surface and the author of the
  neuroframe helper pair this package REPLACES. His repository is the
  designated future learning source: when new DLSS 5 NR integration
  capabilities appear, look there (and at NVIDIA's DLSS SDK samples) for a
  quick refresher on what is possible. We are grateful — the pair served
  the nodepack well and remains supported as a legacy engine.
- **NVIDIA/DLSS SDK headers** (`nvsdk_ngx_defs.h` et al.) — the API surface
  (export signatures, parameter names, enum values) these modules bind.
- **RenoDX / clshortfuse** — the HDR colour composition knowledge that
  informed the pack's HDR bridge (see ``ants/dlssnr/hdr_bridge.py``).

Everything here is Windows-only at call time (D3D12/NGX); importing the
package is safe on any OS — the loaders raise loud, owner-style errors
``[ANTs]`` only when a session is actually requested on an unsupported
platform.
"""

from .errors import DlssSrError

__all__ = ["DlssSrError"]

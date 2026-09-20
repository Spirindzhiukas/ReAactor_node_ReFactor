# RESEARCH — technique references (LOCAL ONLY)

## Why this directory is (mostly) not in Git

`merserk_ve10/` holds source files cloned from
<https://github.com/Merserk/dlss5-visual-enhancer> (tag v10.0, cloned 2026-09-20
with the owner's explicit authorization to inspect them for the ANTs DLSS
work).

Merserk's `LICENSE` (see `merserk_ve10/LICENSE`) is a **source-available
proprietary** license: inspection and private modification are allowed, but
**redistribution** (mirroring, repackaging, placing on another distribution
channel) requires prior written permission. This repository is pushed to
GitHub, which is a distribution channel — so the copied sources are
**gitignored** (`RESEARCH/merserk_ve10/` in the repo-root `.gitignore`) and
exist only in this working tree. The committed parts of RESEARCH/ are this
README and `NOTES.md`: our own analysis, in our own words, with short
attributed quotes only — the same regime as the DVT technique-reference rule.

To recreate the directory locally:

```
git clone --depth 1 https://github.com/Merserk/dlss5-visual-enhancer
```

## Contents

- `merserk_ve10/src/core/paths.py` — runtime layout pins (`bin/runtime/dlssnr/`
  = engine + caller shim + `nvngx_dlssnr.dll`; `bin/runtime/dlssg/`).
- `merserk_ve10/src/core/ngx_runtime.py` — the process-wide NGX RLock doctrine.
- `merserk_ve10/src/core/neural_bridge.py` — the feature-18 bridge ABI (v6)
  as bound from Python: watchdogs, poison states, parameter structs.
- `merserk_ve10/src/core/runtime.py` — session layer, NR styles mapping.
- `merserk_ve10/src/core/nr_composition.py` — mask handling for NR.
- `merserk_ve10/src/frame_interpolation/native.py` — DLSS-G bridge (priming).
- `merserk_ve10/src/upscale/video/native.py` — RTX Video (VSR/HDR) bridge.

The compiled bridges themselves (and the NVIDIA snippet's internals) are NOT
in the source repo — they ship as release binaries only.

# ReActor → ReFactor: Nodepack Audit & Refactor Plan

**Audit date:** 2026-09-18
**Baseline analyzed:** ReActor for ComfyUI **v0.7.1-b1** — current upstream state of `Gourieff/ComfyUI-ReActor`
(the project's original GitHub home `Gourieff/comfyui-reactor-node` was TOS-blocked on 2025-01-16; development
continued on Codeberg and has returned to GitHub under the new name. This repo is a clean-room rework of it.)

---

## 1. What the nodepack is today

```
ComfyUI-ReActor (v0.7.1-b1)
├── nodes.py                  18 nodes (face swap, restore, mask helper, face models, DLSS5, ...)
├── install.py / install.bat  install-time dependency + model bootstrap
├── reactor_core/             ★ pure-Python ONNX face engine (replaces the insightface SDK)
│   ├── face_objects.py       Face container + onnxruntime session base
│   ├── inswap.py             SCRFD detector, ArcFace embedder, genderage, landmarks, INSwapper
│   ├── hyperswap.py          HyperSwapper (hyperswap_1x_256 family)
│   ├── analyzer.py           buffalo_l orchestrator (+ auto-download from HF)
│   └── meanshape_68.py       3D-pose math constants
├── scripts/                  swapper orchestration, face boost (GFPGAN/CodeFormer),
│                             masking (SAM + YOLO), NSFW (SFW) filter, logger, version
├── r_basicsr/                vendored Basicsr (Apache-2.0) — archs needed for GFPGAN
├── r_chainner/               vendored chainner GFPGANv1Clean arch loader
├── r_facelib/                vendored facexlib subset (RetinaFace/YOLOv5face, parsing, helpers)
├── r_modules/                sd-webui-style shims (processing/scripts/shared)
├── r_dlssnr/                 DLSS5 Frame Enhancer — ctypes bridge to 3rd-party DLLs
│   └── dll/                  (user-supplied: neuroframe_*.dll + nvngx_dlssnr.dll)
└── pyproject.toml            Comfy Registry metadata
```

### What is already good (keep & preserve)
- **`reactor_core` is a pure-Python reimplementation of everything the `insightface` SDK used to do**
  (SCRFD detection, ArcFace embeddings, genderage, 2D/3D landmarks, INSwapper latent math, emap extraction,
  and the newer HyperSwap models). No C++ toolchain, no prebuilt-wheel downloads from sketchy mirrors. This
  is exactly the right direction — the refactor keeps and hardens it.
- **Vendored `r_basicsr` / `r_chainner` / `r_facelib`** already replaced the `basicsr`, `gfpgan`, `facexlib`
  pip packages (all abandoned/unmaintained) with local code.
- Face models saved as **safetensors**; models live under ComfyUI's `models/` tree.
- `pyproject.toml` with `[tool.comfy]` registry metadata; `install.bat` already handles Desktop venv /
  portable embedded python detection.
- DLSS5 DLL loading is already **user-supplied, 3rd-party-only** (`r_dlssnr/dll/README.md` documents manual
  download; `nvngx_dlssnr.dll` may not be redistributed per NVIDIA license).

---

## 2. Findings — dangerous, outdated, sketchy, clash-prone

### A. Install-time behavior (the "sketchy, uncontrollable" part)

| # | Severity | Finding |
|---|----------|---------|
| A1 | 🔴 High | `install.py` performs **pip surgery behind the user's back**: picks and installs `onnxruntime-gpu` versions with an extra index (`aiinfra.pkgs.visualstudio.com`), and `pip install -U` upgrades. No dry-run, no consent, no report, no skip switch. |
| A2 | 🔴 High | **Broken version logic:** `torch.torch_version.__version__ <= "2.2.0"` compares *strings* lexicographically → `"2.10.0" <= "2.2.0"` is `True`. On modern torch (≥2.10) it installs `onnxruntime-gpu==1.17.0` — an ABI-mismatched ancient wheel → guaranteed breakage. Also `torch.torch_version` is a *module*, and the comparison is not PEP-440. |
| A3 | 🔴 High | **onnxruntime flavor clash:** the script never checks whether `onnxruntime` (CPU) is already installed before forcing `onnxruntime-gpu` (or vice versa). The two distributions both own the `onnxruntime` package dir; last install wins and the other node's install silently breaks — the single most notorious clash in the ComfyUI ecosystem. |
| A4 | 🟠 Med | `pkg_resources` (deprecated, removed from setuptools' default presence) with a fallback to `importlib_metadata` — which is **not the stdlib name** (`importlib.metadata`); on Python ≥3.12 without setuptools this crashes the whole install. |
| A5 | 🟠 Med | `requirements.txt` is installed via the same unchecked `run_pip()`; a `>=` pin triggers installs that can alter shared packages. `numpy` is listed unpinned and `opencv-python>=4.7.0.72` will happily install the *GUI* flavor **over** an existing `opencv-python-headless` (or co-install both → `cv2` import roulette). |
| A6 | 🟡 Low | Model auto-download (inswapper_128.onnx, buffalo_l.zip, NSFW detector, face-restoration models) uses raw `urllib` with **no timeout, no resume, no integrity check, no .part temp file** — a stalled HF connection hangs startup and a partial file poisons the models dir. No opt-out switch. |

### B. Dependency body (clash sources)

| # | Severity | Finding |
|---|----------|---------|
| B1 | 🔴 High | **`ultralytics` is a hard dependency for exactly one node** (`ReActorMaskHelper` → `subcore.load_yolo`). It drags in pandas/matplotlib/py-cpuinfo, pins its own opencv/numpy expectations, changes APIs aggressively, and is the #1 cause of env conflicts in face-swap stacks. |
| B2 | 🟠 Med | **`albumentations>=1.4.16` is listed in requirements.txt and pyproject.toml but is never imported anywhere in the codebase.** Pure dead weight that every user installs for nothing (and albumentations is a frequent numpy/opencv clash source). |
| B3 | 🟠 Med | **`segment_anything`** (Meta) — unmaintained since 2023 — used only by `MaskHelper`/`r_masking`. Small, but needless as a pip dep: it can be vendored like the other libs. |
| B4 | 🟠 Med | **`transformers` is imported unconditionally** (`scripts/reactor_sfw.py`, for the ViT NSFW filter) **but is not declared anywhere**. It only works because ComfyUI core happens to ship it. Any reorganization of ComfyUI's deps breaks the nodepack's import. |
| B5 | 🟡 Low | `torchvision` used for three trivialities (`normalize`, `masks_to_boxes`, `make_grid`) and `scipy` for a single `stats.mode` call — both are core deps today, but both usages are one-liners replaceable with pure torch/numpy, removing two version-coupled APIs. |
| B6 | 🟡 Low | `opencv-python` is a genuine hard requirement (affine warps, blurs) — fine — but the *flavor* must be handled intelligently (never install a second flavor over an existing importable `cv2`). |

### C. Code-level issues (compat & safety)

| # | Severity | Finding |
|---|----------|---------|
| C1 | 🔴 High | **`sys.path.insert(0, repo_dir)` + absolute imports** (`from scripts.… import`, `import reactor_utils`, `from r_facelib…`). Any other nodepack shipping a top-level `scripts` (or colliding name) package breaks ReActor — and ReActor breaks it. This is the classic custom-node namespace pollution. |
| C2 | 🟠 Med | `torch.load(model_path)` **without `weights_only=True`** for CodeFormer checkpoints → unsafe pickle deserialization (RCE class) on torch < 2.6; torch ≥ 2.6 flips the default and needs the flag handled explicitly. |
| C3 | 🟠 Med | Execution-provider detection is wrong/fragile: DirectML/privateuseone → `"ROCMExecutionProvider"` (nonsense), no validation against `onnxruntime.get_available_providers()`, ORT warnings silenced (`set_default_logger_severity(3)`) so silent CPU fallbacks are invisible. Duplicated in two places (`reactor_swapper.py`, `r_faceboost/restorer.py`). |
| C4 | 🟡 Low | `np.warnings = warnings` monkeypatch (numpy removed `np.warnings` in 2.x); global `logging.STATUS` level monkeypatch; global mutable model caches without locks (unsafe under ComfyUI's free-memory threading). |
| C5 | 🟡 Low | `r_basicsr` vendored *in full* including training-only code (losses, metrics, dataloaders, CUDA op sources ≈ 60% dead weight). Harmless but bloats installs and audits. |
| C6 | 🟡 Low | No `__version__` export, no `WEB_DIRECTORY`, version printed via import side effect; `logging.STATUS` used before registration possible; mixed RU/EN comments; duplicated provider logic; dead code blocks in `reactor_utils.py`. |

### D. DLSS5 (`r_dlssnr`) — per owner's instruction: preserve, then extend

Current design is already 3rd-party-only (no auto-download in code; manual placement per README):
- `neuroframe_caller.dll` / `neuroframe_engine.dll` (3rd-party bridge, Merserk) + `nvngx_dlssnr.dll` (NVIDIA, redistribution prohibited) loaded via `ctypes` from a **single flat folder** `r_dlssnr/dll/`.
- **Missing (to be added, per owner):** support for user-supplied DLL versions and a **switch between installed versions** — today only one flat set can exist. As DLSS5-family DLL versions multiply, users need to drop several versioned sets side-by-side and pick one.

**Planned extension (DLL strategy itself untouched):**
- New user-facing DLL roots: `ComfyUI/models/dlssnr/<version_name>/` (version subfolders) — scanned at node init alongside the built-in `r_dlssnr/dll/` flat folder (kept for backward compat).
- `DLSS5FrameEnhancer` gains a `dll_version` combo (`Auto` = newest ABI-compatible, or a listed version), refreshed from the scan; ABI version handshake (`dlss5nr_frame_abi_version`) decides compatibility, with a clear console report of what was found and what was picked.
- Everything remains manual/3rd-party download — the node never fetches DLLs itself.

---

## 3. Refactor plan ("ReFactor")

Goal: **zero forced downgrades/upgrades of anything ComfyUI or other nodes own; fully user-controllable
install; minimal, honest, clash-free dependency set; modern-ComfyUI hygiene.**

### R1. Package hygiene (kills the `sys.path` clash class)
- Reestructure as one proper package; **all imports package-relative**; delete every `sys.path` manipulation.
- Export `__version__`; keep node class names identical for workflow compatibility.
- Lazy, deferred loading: nothing heavy at import time; nodes that need optional pieces degrade gracefully
  with actionable messages instead of import crashes.

### R2. Dependency disposition

| Dependency | Status today | Disposition |
|---|---|---|
| `insightface` SDK | already removed upstream | stays removed (pure-Python engine) |
| `basicsr`, `gfpgan`, `facexlib` | already vendored | stays vendored (trimmed to runtime-needed modules) |
| `albumentations` | listed, **never used** | **removed** |
| `ultralytics` | hard dep for 1 node | **optional**: lazy-imported; MaskHelper registers only when present; never in requirements/pyproject |
| `segment_anything` | hard dep for 1 node | **vendored** (Apache-2.0) into the nodepack; dep removed |
| `transformers` | undeclared hard import | **lazy** (SFW filter only); if absent → filter disabled with notice; never a hard dep |
| `torchvision` | 3 trivial uses | replaced with pure torch/numpy helpers |
| `scipy` | 1 trivial use | replaced with numpy |
| `opencv` | hard dep, flavor-blind | required, but flavor-aware: install only if `cv2` not importable at all |
| `onnx` | emap extraction | kept (pure-Python, LF-maintained, clash-free) |
| `onnxruntime(-gpu)` | forced via pip surgery | **flavor-aware**: never install if any flavor exists; if missing, install CPU wheel only, tell user exactly how to get GPU EP; correct PEP-440 logic; no `-U`, no exotic extra-index pins |
| `numpy` / `torch` / `pillow` / `tqdm` / `safetensors` | ComfyUI core owns | never installed, never upgraded by us |

### R3. New `install.py` (user-controllable, transparent)
- Uses `importlib.metadata` (stdlib) + PEP-440 comparisons.
- **Snapshot guard:** records `torch/numpy/opencv/onnxruntime` versions before touching anything; aborts with
  a clear message if any pre-existing package would change version.
- Modes: default (report + minimal installs), `--dry-run`, `--yes`, `--skip-deps`,
  env `REACTOR_SKIP_INSTALL=1` honored (also honored at ComfyUI startup).
- Model downloads (inswapper_128, buffalo_l): timeout + resume + `.part` temp files + minimum-size checks +
  opt-out env `REACTOR_NO_AUTO_DOWNLOAD=1` (clear manual-download instructions instead).

### R4. Safety & modern-runtime sweep
- `torch.load(..., weights_only=True)` everywhere; explicit handling for torch ≥2.6 semantics.
- Single provider-resolution helper: intersects desired EPs with `onnxruntime.get_available_providers()`,
  logs the actual choice once, correct MPS/CUDA/DirectML mapping, shared by all sessions.
- numpy-2.x and Python 3.10–3.13 clean (no `np.warnings`, no removed aliases); no global `logging` monkeypatch
  (scoped logger instead); locks around model caches.
- Trim `r_basicsr` to the runtime subset (archs/registry/utils actually imported), dropping training-only
  trees and dead CUDA op sources.

### R5. DLSS5 version switcher (additive; DLL policy unchanged)
- As described in §D: versioned user DLL roots + `dll_version` combo + ABI handshake + clear reporting.

### R6. Node surface (compat-first)
- All existing node class names, inputs, outputs and order preserved → existing workflows keep working.
- New optional inputs only appear as *optional* sockets.
- `MaskHelper` auto-hides (with console explanation) when optional deps are absent, instead of crashing
  the whole nodepack.

---

## 4. Explicitly out of scope (owner's instruction)
- Any change to DLSS DLL acquisition strategy — stays manual / 3rd-party only.
- Re-adding an `insightface`-SDK dependency — the pure-Python core stays.

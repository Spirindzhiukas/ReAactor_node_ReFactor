<div align="center">

# ReActor ReFactor ⚡

**A dependency-hygiene rework of the ReActor face-swap nodepack for ComfyUI.**

Same engine lineage as [ReActor](https://github.com/Gourieff/ComfyUI-ReActor) (GPL-3.0), rebuilt for modern
ComfyUI environments: minimal dependencies, zero import collisions, a fully user-controllable install,
and no dependency clashes with other custom nodes.

</div>

---

## Why this fork exists

The original ReActor node carries install machinery that can silently mutate a shared ComfyUI python
environment — the classic source of "after installing X, other nodes broke". This rework keeps the
battle-tested pure-Python ONNX face engine and removes every mechanism that can touch your environment
without asking.

### What changed vs. upstream (v0.7.1-b1 baseline)

| Area | Upstream | ReFactor |
|---|---|---|
| `insightface` SDK | removed upstream already | stays removed — pure-Python ONNX engine (SCRFD / ArcFace / INSwapper / HyperSwap) |
| `albumentations` | declared, never imported | **removed** |
| `ultralytics` + `segment_anything` | hard deps for one masking node | **removed** — masking now uses ComfyUI-native `SAM3_Detect`/mask nodes or the built-in face-region fallback |
| onnxruntime install | pip surgery with broken version-string compares, forced `-gpu` flavor, extra index | **flavor-aware**: never installs if any flavor exists, correct PEP-440 logic, CPU default, no `-U`, no exotic indexes |
| pip behavior at install | silent, unstoppable | **interactive / dry-run / flags / env switches**; snapshots torch/numpy/opencv/ort and refuses to change them |
| `sys.path` injection + absolute imports | collision risk with other node packs | **zero** — proper package-relative imports |
| NSFW (SFW) filter | transformers ViT checker | **removed** (uncensored build — use responsibly and obey your local laws) |
| Masking helper | bundled ultralytics YOLO + SAM loader | **Mask Builder**: accepts `MASK` / `BOUNDING_BOX` from ComfyUI's own nodes (SAM3, SAM2, RMBG, …), plus built-in soft face-region masks; grow / morphology / feather / invert post-chain |
| DLSS5 Frame Enhancer | single flat DLL folder | **unchanged 3rd-party policy** + `dll_version` switcher: drop any number of user-supplied DLL sets into `models/dlssnr/<version>/` and pick per workflow |
| `torch.load` | no `weights_only` | `weights_only=True` everywhere |
| Downloads | raw urllib, no timeout/resume | timeout + resume + atomic `.part` + size checks + `REFACTOR_NO_AUTO_DOWNLOAD=1` opt-out |
| basicsr vendored copy | full training framework (~100 files) | trimmed to the tiny registry/logger subset actually used |

Node class names are **new and distinct** (`ReFactorFaceSwap`, `ReFactorMaskBuilder`, …), so this nodepack can
safely coexist with the original ReActor in one ComfyUI installation — no mapping or module collisions.

## Installation

1. Clone into `ComfyUI/custom_nodes`:
   ```
   git clone https://github.com/Spirindzhiukas/ReAactor_node_ReFactor.git
   ```
2. Run the bootstrap (Windows) or the python script:
   - `install.bat` — or — `python install.py` (use `--dry-run` to just report)
3. Restart ComfyUI. Nodes appear under the **ReFactor** category.

### What install.py will and won't do

**Will:** report your environment; install `onnx` / `opencv-python` only if actually missing; install
`onnxruntime` (CPU) only if **no** onnxruntime flavor is present; download `inswapper_128.onnx`
(~530 MB, from the official ReActor HF dataset) unless `--no-models` / `REFACTOR_NO_AUTO_DOWNLOAD=1`.

**Won't:** upgrade/downgrade torch, numpy, opencv, or an existing onnxruntime; touch any extra pip
index; run anything without telling you first (pass `--yes` in scripts to skip the confirmation).

### GPU acceleration

onnxruntime flavor choice is yours:
- CPU: `pip install onnxruntime`
- NVIDIA: `pip install onnxruntime-gpu` (or run `install.py --ort-gpu` when no flavor exists)

The engine validates execution providers against the installed build and logs what it picked.

## Models

| Model | Location | Notes |
|---|---|---|
| `inswapper_128.onnx` | `models/insightface/` | downloaded by install (or fetch manually from the [ReActor HF dataset](https://huggingface.co/datasets/Gourieff/ReActor/tree/main/models)) |
| `buffalo_l` (detect/embed) | `models/insightface/models/buffalo_l/` | auto-downloaded on first swap |
| `reswapper_128/256.onnx` | `models/reswapper/` | manual |
| `hyperswap_1x_256.onnx` | `models/hyperswap/` | manual (see [facefusion models](https://huggingface.co/facefusion/models-3.3.0/tree/main)) |
| GFPGAN / CodeFormer / GPEN | `models/facerestore_models/` | downloaded when the FaceRestore Model Loader executes with a canonical entry |
| DLSS-NR DLLs (3rd-party) | `models/dlssnr/<version>/` | **manual only** — see `rfactor/dlssnr/dll_README.md` |

## DLSS5 Frame Enhancer (3rd-party DLLs)

This node never downloads DLLs. Place `neuroframe_caller.dll`, `neuroframe_engine.dll` and
`nvngx_dlssnr.dll` into `ComfyUI/models/dlssnr/<any-version-name>/` — as many versions as you like —
then pick the set in the node's `dll_version` selector (`auto` picks the best complete set; `refresh`
re-scans after you add DLLs). NVIDIA's license prohibits redistributing `nvngx_dlssnr.dll`, so DLL
acquisition stays 100% yours — sources and licenses in `rfactor/dlssnr/dll_README.md`.

## Masking without ultralytics

Connect any ComfyUI-native segmentation into the **Mask Builder**:
- `SAM3_Detect` (core, text/box/point prompts) → `masks` and/or `bboxes` outputs → Mask Builder;
- SAM2 / EfficientSAM / RMBG / any MASK-producing node → `mask` input;
- or no input at all: the built-in fallback derives soft, feathered face-region masks from the
  nodepack's own detector (always available, zero extra models beyond buffalo_l).

## Nodes

**Model loaders** (the facerestore_cf pattern — models via dedicated nodes, typed sockets):
`ReFactorFaceSwapModelLoader` → `FACE_SWAP_MODEL`, `ReFactorFaceRestoreModelLoader` → `FACE_RESTORE_MODEL`,
`ReFactorFaceDetectionModelLoader` → `FACE_DETECT_MODEL`. Swap models get their own loader (not shared
with restore) because they are persistent cached ONNX sessions with family routing
(inswapper / reswapper / hyperswap), unlike per-run restoration models.

**Main:** `ReFactorFaceSwap` / `ReFactorFaceSwapOpt` with sockets
`original_image`, `target_face_image` (the face donor image), `target_face_model` (prebuilt FACE_MODEL),
`FaceSwap_model`, `FaceRestore_model`, `FaceDetection_model`, `face_boost`.
The old monolithic `facedetection` / `face_restore_model` / `swap_model` dropdowns are gone —
connect the loaders instead. CodeFormer's `codeformer_weight` is now named correctly everywhere:
`codeformer_fidelity` (same CodeFormer `w` parameter that facerestore_cf calls fidelity).

**Also:** `ReFactorOptions`, `ReFactorFaceBoost`, `ReFactorMaskBuilder`, `ReFactorSetWeight`,
`ReFactorSaveFaceModel` / `ReFactorLoadFaceModel` / `ReFactorBuildFaceModel` / `ReFactorMakeFaceModelBatch`,
`ReFactorRestoreFace` / `ReFactorRestoreFaceAdvanced` (both now take `FACE_RESTORE_MODEL` /
`FACE_DETECT_MODEL` inputs), `ReFactorFaceSimilarity`, `ReFactorImageDuplicator`,
`ReFactorImageRGBA2RGB`, `ReFactorUnload`, `ReFactorDLSS5Enhancer`.

Typical graph:

```
[FaceSwap Model Loader]──FaceSwap_model─────────┐
[FaceRestore Model Loader]──FaceRestore_model───┤
[FaceDetection Model Loader]──FaceDetection_model┤
[target face image]──target_face_image──────────┤
                                                ▼
[original image]────────────────────> ReFactor ⚡ Fast Face Swap ──> SWAPPED_IMAGE
```

## Development

```
python tests/smoke_import.py    # full package import + node INPUT_TYPES validation
```

## Disclaimer

This nodepack ships **without** an NSFW filter. Face-swap technology can be misused; you are solely
responsible for what you create with it. Do not use it to deceive, harass, or harm; respect the law,
platform rules, and the dignity of real people. Licensed GPL-3.0 (inherited from ReActor).

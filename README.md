<div align="center">

# ANTs Face Nodes (ex ReActor ReFactor) ⚡

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

| Area | Upstream | ANTs |
|---|---|---|
| `insightface` SDK | removed upstream already | stays removed — pure-Python ONNX engine (SCRFD / ArcFace / INSwapper / HyperSwap) |
| `albumentations` | declared, never imported | **removed** |
| `ultralytics` + `segment_anything` | hard deps for one masking node | **removed** — masking now uses ComfyUI-native `SAM3_Detect`/mask nodes or the built-in face-region fallback |
| onnxruntime install | pip surgery with broken version-string compares, forced `-gpu` flavor, extra index | **flavor-aware**: never installs if any flavor exists, correct PEP-440 logic, CPU default, no `-U`, no exotic indexes |
| pip behavior at install | silent, unstoppable | **interactive / dry-run / flags / env switches**; snapshots torch/numpy/opencv/ort and refuses to change them |
| `sys.path` injection + absolute imports | collision risk with other node packs | **zero** — proper package-relative imports |
| NSFW (SFW) filter | transformers ViT checker | **removed** (uncensored build — use responsibly and obey your local laws) |
| Masking helper | bundled ultralytics YOLO + SAM loader | **Mask Builder**: accepts `MASK` / `BOUNDING_BOX` from ComfyUI's own nodes (SAM3, SAM2, RMBG, …), plus built-in soft face-region masks; grow / morphology / feather / invert post-chain |
| DLSS5 Frame Enhancer + NR Scheduler | single flat DLL folder | **manual-only policy** + `dll_version` switcher: drop user-supplied DLL sets (any filenames) into `models/DLSS/dlssnr_<version>/` and pick per workflow |
| `torch.load` | no `weights_only` | `weights_only=True` everywhere |
| Downloads | raw urllib, no timeout/resume | timeout + resume + atomic `.part` + size checks + `REFACTOR_NO_AUTO_DOWNLOAD=1` opt-out |
| basicsr vendored copy | full training framework (~100 files) | trimmed to the tiny registry/logger subset actually used |

Node class names are **new and distinct** (`ANTsFaceSwap`, `ANTsMaskBuilder`, …), so this nodepack can
safely coexist with the original ReActor in one ComfyUI installation — no mapping or module collisions.

## Installation

1. Clone into `ComfyUI/custom_nodes`:
   ```
   git clone https://github.com/Spirindzhiukas/ReAactor_node_ReFactor.git
   ```
2. Run the bootstrap (Windows) or the python script:
   - `install.bat` — or — `python install.py` (use `--dry-run` to just report)
3. Restart ComfyUI. Nodes appear under the **ANTs** category.

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
| DLSS-NR DLLs (3rd-party) | `models/DLSS/dlssnr_<version>/` (any filenames) | **manual only** — see `ants/dlssnr/dll_README.md` |

## DLSS5 (ANTs⚡DLSS5 Frame Enhancer)

Hybrid enhancer: our feature-rich DLSS-NR surface + OreX-inspired temporal history management + a RenoDX-inspired HDR Colour Bridge stage. Design notes, comparison and full credits: [docs/RESEARCH_dlss5_hybrid.md](docs/RESEARCH_dlss5_hybrid.md).

This node never downloads DLLs. Place your DLL sets into `ComfyUI/models/DLSS/dlssnr_<version>/` —
category folders (`NR/`, `SR/`, `FG/`) with one folder per version inside, **any .dll filenames accepted** (the engine is identified by its exports, not
by name) — then pick the set in the node's `dll_version` selector (`auto` picks the first found set;
`refresh` re-scans after you add DLLs). `nvngx_dlssnr.dll` must be procured by you (NVIDIA's license
prohibits redistributing it) — sources and licenses in `ants/dlssnr/dll_README.md`.

**NR Schedules** — replace the old ``nr_passes`` repeat with the **ANTs⚡DLSS NR Scheduler**: a style
per pass (Natural/Cinematic cycling is the owner-validated default; varied passes beat monolithic
`nr_passes = 4` — the engine's author names style 1 **Natural**, not "Nature"; same ABI int,
corrected display), optional per-pass full settings (bypass the main node's widgets when on), per-pass
pre-SR denoise with dedicated model slots. Dynamic JS UI on both nodes (pack's `web/` folder); the
Python side validates everything and works headless without it.

**Pre-SR denoise (optional):** connect a 1x denoising/restoration model — `ANTs Upscale Model Loader` →
the node's `denoise_model` socket (SCUNet, PureScale2 `1x_PureVision`, …) — it runs through the
comfy-native tiled pipeline *before* the DLSS-NR engine, with a `pre_denoise_strength` blend.

**GPU acceleration (on by default):** frames are processed GPU-resident through the engine's CUDA
entry point (`dlss5nr_process_cuda_v6`) — zero PCIe copies when the batch already lives in VRAM,
orders of magnitude faster at 4K and with `nr_passes` > 1. `CPU (host staging)` stays as a compat
fallback (and for engine builds too old for CUDA — you get a clear error).

HDR Colour Bridge: **Classic** (paper-white gain; Diffuse white 220 nits, Scene Paper-White Scale
1.0 — neutral by default — plus HDR Transfer Strength 1.0, Color Strength 1.0) or **Anchored**
(auto white point anchored to the frame's highlights + black-floor lever) — after RenoDX's DLSS 5
colour work by clshortfuse (MIT). The image-tuned defaults replace RenoDX's game-engine reference
values (237 nits / 2.537), which overbrighten regular 8/16-bit images.

Credit: [OreX (orex2121)](https://github.com/orex2121/ComfyUI-DLSS5-orex) (ComfyUI-DLSS5 — temporal
history design), [RenoDX by clshortfuse](https://github.com/clshortfuse/renodx) (HDR bridge concept),
[Merserk](https://github.com/Merserk) (neuroframe helper DLLs + engine ABI/CUDA reference).

## Masking without ultralytics

Connect any ComfyUI-native segmentation into the **Mask Builder**:
- `SAM3_Detect` (core, text/box/point prompts) → `masks` and/or `bboxes` outputs → Mask Builder;
- SAM2 / EfficientSAM / RMBG / any MASK-producing node → `mask` input;
- or no input at all: the built-in fallback derives soft, feathered face-region masks from the
  nodepack's own detector (always available, zero extra models beyond buffalo_l).

## Nodes

**Model loaders** (the facerestore_cf pattern — models via dedicated nodes, typed sockets):
`ANTsFaceSwapModelLoader` → `FACE_SWAP_MODEL`, `ANTsFaceRestoreModelLoader` → `FACE_RESTORE_MODEL`,
`ANTsFaceDetectionModelLoader` → `FACE_DETECT_MODEL`, `ANTsUpscaleModelLoader` → `UPSCALE_MODEL`
(comfy-native listing + spandrel loading; the output even plugs into the stock
"Upscale Image (using Model)" node). Swap models get their own loader (not shared
with restore) because they are persistent cached ONNX sessions with family routing
(inswapper / reswapper / hyperswap), unlike per-run restoration models.

**Main:** `ANTsFaceDancer` (ANTs⚡Face Dancer) with sockets
`original_image`, `target_face_image` (the face donor image), `target_face_model` (prebuilt FACE_MODEL),
`FaceSwap_model`, `FaceRestore_model`, `FaceDetection_model`, `UpscaleModel`.
The old monolithic `facedetection` / `face_restore_model` / `swap_model` dropdowns are gone —
connect the loaders instead. The three model sockets sit **grouped together** at the top of the
optional inputs (in load order). CodeFormer's `codeformer_weight` is now named correctly everywhere:
`codeformer_fidelity` (same CodeFormer `w` parameter that facerestore_cf calls fidelity).

**Face Restore upRes interpolator** (absorbs — and replaces — the old Face Booster node): the
`face_restore_upres` switch (default ON) plus the `upres_interpolation` selector
(Lanczos / Bicubic / Bilinear / Nearest / **Use Upscale model**). With the switch ON, the restore
model runs at the face's own resolution whenever the model supports it (dynamic-input models like
GFPGAN restore small faces with **no** pixel scaling at all); otherwise the face is brought to the
model's native resolution, restored, and scaled back to the image resolution. "Use Upscale model"
hands the pixel scaling to a connected `UPSCALE_MODEL` (tiled, OOM-aware — same code path as
ComfyUI's stock upscale node): when the face must be *enlarged* for the restore model, the aligned
crops are super-resolved with it **before** inference; when a restored face must be scaled back up,
the model does that too — and the result is always normalized back to the face's exact resolution,
whatever the model's 1x/2x/4x/8x output is. Every upRes decision and fallback is logged
(`upRes: ...` STATUS lines); the loader must be connected or the node falls back to interpolation
and says so. `ANTsFaceBoost` is **removed** — the main node now
restores better than the booster did (proper affine paste-back with soft masks at any resolution,
no crop-space aliasing).

**Restore-only mode:** leave `FaceSwap_model` unconnected and the node skips swapping entirely while
still applying face restoration to `original_image` — useful as a standalone restorer pipeline stage.

**Also:** `ANTsOptions`, `ANTsMaskBuilder`, `ANTsSetWeight`,
`ANTsSaveFaceModel` / `ANTsLoadFaceModel` / `ANTsBuildFaceModel` / `ANTsMakeFaceModelBatch`,
`ANTsRestoreFace` / `ANTsRestoreFaceAdvanced` (both now take `FACE_RESTORE_MODEL` /
`FACE_DETECT_MODEL` inputs), `ANTsFaceSimilarity`, `ANTsImageDuplicator`,
`ANTsImageRGBA2RGB`, `ANTsUnload`, `ANTsDLSS5Enhancer`, `ANTsDLSSNRScheduler` → `NR_SCHEDULE`.

Typical graph:

```
[FaceSwap Model Loader]──FaceSwap_model─────────┐
[FaceRestore Model Loader]──FaceRestore_model───┤
[FaceDetection Model Loader]──FaceDetection_model┤
[target face image]──target_face_image──────────┤
                                                ▼
[original image]────────────────────> ANTs⚡Face Dancer ──> SWAPPED_IMAGE
```

## Development

```
python tests/smoke_import.py    # full package import + node INPUT_TYPES validation
```

## Disclaimer

This nodepack ships **without** an NSFW filter. Face-swap technology can be misused; you are solely
responsible for what you create with it. Do not use it to deceive, harass, or harm; respect the law,
platform rules, and the dignity of real people. Licensed GPL-3.0 (inherited from ReActor).

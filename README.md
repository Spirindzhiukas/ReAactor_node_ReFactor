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
| DLSS-NR DLLs (3rd-party) | `models/DLSS/NR/` (any filenames) | **manual only** — see `docs/MODELS_DLSS_LAYOUT.md` and `ants/dlssnr/dll_README.md` |
| DLSS-SR / -FG DLLs | `models/DLSS/SR/`, `models/DLSS/FG/` | manual only |
| Neuroframe helper pair | `models/DLSS/Merserk_DLLS/` | manual only, **one copy** (author: Merserk) |

## DLSS5 (ANTs⚡DLSS5 Frame Enhancer + the two focused processors)

Three nodes, two engines:

| node | engine | notes |
|---|---|---|
| **ANTs⚡DLSS5 Frame Enhancer** | both (selector) | the original node — full surface, engine picker |
| **ANTs⚡DLSS5 Processor (ReShade based)** | legacy DLL engine only | the RenoDX-derived `nvngx_dlssnr.dll` + the neuroframe helper pair (**by Merserk**, credit to **clshortfuse**'s RenoDX work) — the engine that runs the rig today, CUDA zero-copy included. **Full settings parity**: the `engine` selector is the only thing it drops. |
| **ANTs⚡DLSS5 Processor (Native NGX, experimental)** | the pack's own native NGX host only | our pure-Python D3D12 + feature-18 path (no 3rd-party helper DLLs, caller shim). Kept separate so its failures cannot perturb the working node. **Full settings parity**: the `engine` selector is the only thing it drops. |

Why "ReShade based": the DLL lineage is the RenoDX DLSS-5 addon, which is a **ReShade addon** —
*OptiScaler is a different project* (a DLSS/XeSS/FSR call redirector) and none of its code is involved
here. All three nodes take the **same** ANTs⚡DLSS NR Scheduler output and share the look controls and
the HDR Colour Bridge.

Hybrid enhancer: our feature-rich DLSS-NR surface + OreX-inspired temporal history management + a RenoDX-inspired HDR Colour Bridge stage. Design notes, comparison and full credits: [docs/RESEARCH_dlss5_hybrid.md](docs/RESEARCH_dlss5_hybrid.md).

This node never downloads DLLs. Place your builds into `ComfyUI/models/DLSS/NR/` (and `SR/`, `FG/`),
**any .dll filenames accepted** (the engine is identified by its exports, not by name), then pick one in
the node's `dll_version` selector (`refresh` re-scans after you add DLLs). `nvngx_dlssnr.dll` must be
procured by you (NVIDIA's license prohibits redistributing it) — sources and licenses in
`ants/dlssnr/dll_README.md`, full path/keep-delete reference in `docs/MODELS_DLSS_LAYOUT.md`.

### Which build gets loaded — the naming rule

`dll_version = auto` loads the **newest** build it can find, and the version comes from the **file
name**, so put it there:

| scheme | example | use it for |
|---|---|---|
| date | `nvngx_dlssnr_2026-09-14.dll` | the community NR builds (no official numbering) |
| NVIDIA version | `nvngx_dlss_310.9.1.dll`, `nvngx_dlssg_310.9.1.dll` | the driver-supplied SR/FG runtimes |
| bare number | `nvngx_dlssnr_2.dll` | quick local A/B builds (lowest priority) |

A date outranks a dotted version, which outranks a bare number, which outranks **no version at all**
(an unversioned file is ordered by its file date and always sorts last — rename it if it should win).
Free text after the version is ignored: `nvngx_dlssnr_2026-09-14_renodx4000.dll` and
`nvngx_dlssnr_2026-09-14.dll` are the same version, and hardware tags (`4000`, `3090`, `series`,
`friendly`) are never mistaken for a version. The selector lists builds **newest first**, and an
**explicit pick always wins** — that is the way to run one specific build deliberately.

**Which builds exist, and why the community ones are normal here.** The official NVIDIA DLSS 5 NR
runtime targets RTX 50-series hardware; on RTX 30/40 series the community RenoDX-derived builds
(RenoDX by clshortfuse, packaged inside Merserk's Visual.Enhancer bundle) are the ones that work, so
this pack treats them as the ordinary path — no build is ranked, skipped or refused because of its
name. Credit for those builds and for the neuroframe helper DLLs stays with their authors.

Names never decide *what a file is*: the runtime is identified by probing its exports, and anything
that is not a runtime is refused with its path. Duplicate names of the same build are detected by
content (identical bytes), so the report tells you when two files are one build.

**NR Schedules** — replace the old ``nr_passes`` repeat with the **ANTs⚡DLSS NR Scheduler**: a style
per pass (the default node plan is **3 passes: Cinematic → Natural → Default**, the owner's showcase
cycle; varied passes beat monolithic `nr_passes = 4` — the engine's author names style 1 **Natural**,
not "Nature"; same ABI int, corrected display), optional per-pass full settings (bypass the main
node's widgets when on), per-pass pre-SR denoise with dedicated model slots. Dynamic JS UI on every
node (pack's `web/` folder), which re-fits the node size when the per-pass rows come and go; the
Python side validates everything and works headless without it.

**If the native node ever reports `CreateCommittedResource ... 0x80070057` while the device says
"healthy"** - that wall is solved (rig 23:32 -> 01:22, fixed 2026-09-21), and the answer was one wrong
bit: the pack sent `0x8` for `ALLOW_UNORDERED_ACCESS`, but `d3d12.h` defines the UAV flag as **`0x4`**
- `0x8` is `DENY_SHADER_RESOURCE`, so every texture this pack intended as a UAV was a UAV-less one
(the ladder hid it by silently degrading to *no flags*, which is what produced the `Close()` E_INVALIDARG
and the GPU faults of the previous evening, and once the degradation was forbidden the driver's refusal
of that byte surfaced at creation). The constant is fixed, the ladder may no longer drop the UAV flag,
and every recipe line prints the flags **byte** next to its name so a log can never again say
"ALLOW_UNORDERED_ACCESS" while the descriptor says `0x8`. `tools\check_d3d12_uav.bat` re-proves it on
the machine in ~20 s without ComfyUI: it runs a **flags matrix** first (0x4 vs 0x8 vs 0x0 - one device,
one texture description, only the byte changes) and exits **14** = "THE FLAGS BYTE WAS THE BUG", then
the historical phases (fresh 11_0 vs 12_0, after plain CUDA work, after the staged legacy engine).
`ANTS_D3D12_FEATURE_LEVEL=12_0` switches the device to the feature level the reference host asks for.
The rule that made the wrong byte fail is in the docs, not in a driver: `DENY_SHADER_RESOURCE` "must be
used with `ALLOW_DEPTH_STENCIL`", so `0x8` alone is an invalid description that any D3D12 runtime
refuses. For the next wall of this kind there is now an instrument: `ANTS_D3D12_DEBUG_LAYER=1` arms the
D3D12 debug layer (needs the Windows "Graphics Tools" optional feature) and a refused description is
followed by the runtime's own explanation, read from `ID3D12InfoQueue` (`ANTS_D3D12_DEBUG_MESSAGES`
caps how many lines are printed). The probe arms it automatically, because that is exactly the run in
which a sentence like "DENY_SHADER_RESOURCE can only be set with ALLOW_DEPTH_STENCIL" ends an
investigation instead of starting one.

**Pre-SR denoise (optional, and now switchable OFF):** `pre_denoise_mode` has three values — the
ANTs SR host (`SR (DLSS denoise)`), a wired upscale/denoise model (`Denoise Model`), and
**`OFF (no pre-denoise)`**, which disables the stage completely: a connected model and a
`pre_denoise_strength` above zero are ignored (and said so in the console), and the widget greys out in
the UI while keeping its value. connect a 1x denoising/restoration model — `ANTs Upscale Model Loader` →
the node's `denoise_model` socket (SCUNet, PureScale2 `1x_PureVision`, …) — it runs through the
comfy-native tiled pipeline *before* the DLSS-NR engine, with a `pre_denoise_strength` blend.

**GPU acceleration needs a CUDA-capable helper engine.** The zero-copy path (`dlss5nr_process_cuda_v6`
in the neuroframe engine) is ~20-25x faster than CPU staging. The node logs which engine build it
loaded and whether that entry point exists; if it is missing, the run still works but on CPU, and the
console says why. Put the current neuroframe pair in `models/DLSS/Merserk_DLLS/` (see
[docs/MODELS_DLSS_LAYOUT.md](docs/MODELS_DLSS_LAYOUT.md)) — the collector's *HELPER / ENGINE INVENTORY*
section lists every helper DLL on disk with its exports.

**GPU acceleration (on by default):** frames are processed GPU-resident through the engine's CUDA
entry point (`dlss5nr_process_cuda_v6`) — zero PCIe copies when the batch already lives in VRAM,
orders of magnitude faster at 4K and with `nr_passes` > 1. `CPU (host staging)` stays as a compat
fallback (and for engine builds too old for CUDA — you get a clear error).

**Multi-GPU machines (Windows).** ComfyUI core enumerates every visible GPU at startup, and on
Windows a CUDA driver bug ([ComfyUI issue #15255 / CORE-398](https://github.com/Comfy-Org/ComfyUI/issues/15255))
can then poison the process: host→device copies start failing with `CUDA_ERROR_OUT_OF_MEMORY` even
with free VRAM, and the same process can end in a `D3D12 device REMOVED` here. If a run dies that
way, restart ComfyUI and launch it with a single device (`--cuda-device 0`, or one GPU's UUID) and/or
`--disable-pinned-memory`. `tools\check_cuda_multigpu.bat` tests whether your machine reproduces the
bug (its own process, ~10 s, writes nothing), and the evidence collector's *CUDA / MULTI-GPU VIEW*
section lists every GPU with its LUID together with the launch flags it found. The node itself always
creates its D3D12 device on the adapter matching its CUDA device **by LUID**, so `--cuda-device N`
(or a hidden/renumbered device list) cannot make it pick the wrong GPU.

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

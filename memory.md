# memory.md — Persistent Project State (living ledger)

Update this file in the same commit as any state-changing work. Rules of the project
live in `CLAUDE.md`; the active checklist lives in `plan.md`.

## Snapshot

- **Version:** v1.1.0-alpha1 · branch: `arena/01a0b5e0-reaactor-node-refactor` (work branch;
  `main` moves via PR merge)
- **Head at last update:** `588f790` (DLSS5 hybrid upgrade) on top of `88305cb` (pre-pass +
  consolidation + rebrand)
- **Suite:** ALL GREEN — 171 checks + gates (details below)

## Shipped history (short)

| Commit | What |
|---|---|
| `588f790` | DLSS5 hybrid: HDR Colour Bridge (Classic def / Anchored / Off), OreX-style temporal history (Auto/Continuous/Per-frame reset), `models/DLSS/dlssnr_<version>/` discovery with ANY filenames (export probing), stale `CATEGORY` fixes, credits, `docs/RESEARCH_dlss5_hybrid.md` |
| `88305cb` | Face Restoration pre-pass (main/dedicated model, ACTIVE toggle); consolidation 20→18 nodes; full ANTs rebrand (version v1.1.0-alpha1, `[ANTs]` prefix); RU comment sweep; README rebrand |
| `2cb2c0c` | upRes fix (crop SR stage was dead code) |
| earlier | upRes interpolator + upscale-model support; ORT fixes; detection state-dict; RetinaFace loader fix; docs |

## Node census (18, all `ANTs`-prefixed)

Loaders (typed-socket pattern): `ANTsFaceSwapModelLoader` → FACE_SWAP_MODEL,
`ANTsFaceRestoreModelLoader` → FACE_RESTORE_MODEL, `ANTsFaceDetectionModelLoader` → FACE_DETECT_MODEL,
`ANTsUpscaleModelLoader` → UPSCALE_MODEL.
Pipeline: `ANTsFaceDancer` (main node: swap + optional restore + pre-pass + options socket),
`ANTsOptions` (options bundle via OPTIONS socket), `ANTsFaceRestore`, `ANTsFaceSimilarity`,
`ANTsMaskBuilder`, `ANTsDLSS5Enhancer` (class `ReFactorDLSS5Enhancer`, display
"ANTs⚡DLSS5 Frame Enhancer", category `ANTs`).
Face-model batch: `ANTsBuildFaceModel`, `ANTsLoadFaceModel`, `ANTsSaveFaceModel`,
`ANTsMakeFaceModelBatch`, `ANTsSetWeight`.
Utilities: `ANTsImageDuplicator`, `ANTsImageRGBA2RGB`, `ANTsUnload`.

## Architecture map

- `nodes.py` — node registrations, the main execute() (OPTIONS merge, pre-pass contract,
  restore selection), Mappings/Display.
- `rfactor/swapper.py` — swap engine (inswapper/hyperswap via `rfactor/engine/`), frame-safe
  restore gate.
- `rfactor/faceboost/` — restoration (CodeFormer/GFPGAN/VQGAN archs, facelib detection:
  RetinaFace + Yolov5Face; `codeformer_fidelity` widget).
- `rfactor/upres.py` + `rfactor/upscaler.py` — face-res interpolation + comfy-core-mirrored
  upscale models (stub seam for tests: `upscale_image_with_model`).
- `rfactor/masking/` — Mask Builder (ultralytics-free: SAM3/SAM2/RMBG sockets + built-in
  feathered fallback).
- `rfactor/dlssnr/` — DLSS5 node: `core.py` (ctypes ABI **v6** struct: style/intensity/tone/
  structure/skin/automask/reset/color_strength/tone_preservation/mask/face_skin_protection/
  grain_preservation/nr_passes/shimmer_suppression/prefer_nvof; `dlss5nr_init(ordinal,
  dll_dir, err)` with 4096-char error buffer; `dlss5nr_process_v6` HOST-memory path;
  engine located by probing `.dll`s for the `dlss5nr_init` export), `discovery.py`
  (models/DLSS/dlssnr_<version> → flat models/DLSS → legacy models/dlssnr → package dll/),
  `hdr_bridge.py` (Classic: sRGB→lin → paper-white gain 2.537 → extended-Reinhard knee at
  237 nits → chroma-around-luma; Anchored: p95-luma white point + black_lever; all float32
  HWC [0,1], applied post-DLL per frame), `node.py` (widgets incl. temporal_history Auto
  threshold 0.24 / Continuous / Per-frame reset).
- `rfactor/model_paths.py` — `DLSS_MODELS_PATH = models/DLSS` (+ legacy `DLSSNR_MODELS_PATH`);
  known low-pri bug: `_migrate_legacy_dirs` off-by-one.
- `docs/` — `RESEARCH_dlss5_hybrid.md` (ours-vs-OreX verdict, bridge math, 2-dll-vs-1,
  pre-SR conclusion), `RESEARCH_ultralytics_eye_fix.md` (tabled eyes, §8),
  `REFACTOR_AUDIT.md`, `upstream/` (original READMEs EN/RU).

## Owner environment

Windows; portable ComfyUI `C:\ComfyUI_PORTABLE\...`; updates via `git pull`; NVIDIA RTX 24 GB;
DLSS5 needs RTX 40/50 + driver ≥ 616.x.

## Test inventory (all standalone scripts; run `python tests/<file>.py`)

| File | Checks | Coverage |
|---|---|---|
| test_pure_helpers.py | 26 | pure helpers + DLSS discovery policy |
| test_swapper_state.py | 13 | swapper state dict |
| test_runtime_surface.py | 58 | 18-node surface, ANTs branding, pre-pass contract, OPTIONS socket |
| test_facerestore_routing.py | 21 | restore routing incl. e2e loud-failure |
| test_detection_state_dict.py | 7 | detector state dicts |
| test_upres.py | 27 | upRes/upscale paths |
| test_dlssnr_bridge.py | 19 | HDR bridge math + models/DLSS discovery + loud error |
| smoke_import.py | — | import + 18-node assert + socket/execute wiring |
| test_pyflakes.py, test_scope_check.py | — | gates |

**Total: 171 checks, all green at `588f790`.** Sandbox venv: numpy, opencv-python-headless,
pillow, pyflakes (NO torch — stub harness only).

## Known issues / dead ends (do not retry)

- Sandbox restarts wipe `.venv_test` and git refs (`.git/config` unpersisted) — recovery
  recipe in `CLAUDE.md`.
- torch uninstallable in sandbox → stub-module harness only; don't try again.
- `renodx-dlss5.addon64` source is Discord-only; the readable reference is
  Dagherbou/OptiScaler_DLSSNR (MIT, credits clshortfuse).
- OreX's native bridge (CUDA interop, upscale modes, chunking) lives in HIS C++ DLL —
  not adoptable from Python over the neuroframe ABI; a true merge = new native bridge
  project (his `bridge/` sources are the starting point, MIT).
- No separate NVIDIA pre-SR denoise DLL exists; denoise lives inside `nvngx_dlssnr.dll`
  (temporal/motion-vector driven). External pre-denoiser (OIDN) would be a new dependency.
- Upscale-model integration mirrors comfy core and resizes output back to face resolution
  (settled design).
- `model_paths._migrate_legacy_dirs` off-by-one (low priority, known).

## Awaiting owner feedback

- Options-socket part of `88305cb` (ANTsOptions → ANTsFaceDancer) — not yet owner-tested.
- DLSS5 hybrid of `588f790` — owner said "will test that version": Classic/Anchored/Off
  bridge, temporal modes, new DLL location `models/DLSS/dlssnr_<version>/` with any filenames.

# memory.md — Persistent Project State (living ledger)

Update this file in the same commit as any state-changing work. Rules of the project
live in `CLAUDE.md`; the active checklist lives in `plan.md`.

## Snapshot

- **Version:** v1.1.0-alpha1 · branch: `arena/01a0b5e0-reaactor-node-refactor` (work branch;
  `main` moves via PR merge)
- **Head at last update:** GPU acceleration commit (on top of `588f790` DLSS5 hybrid,
  `88305cb` pre-pass/rebrand)
- **Suite:** ALL GREEN — 272 checks + gates (details below)
- **Owner rig facts (probe v2, CONFIRMED):** NGX core PRESENT (DriverStore
  `nvmdsi.inf_amd64_05d1e242e80cf105`, core `_nvngx.dll` 32.0.16.1692 + loader
  `nvngx.dll` 30.0.14.9516) - SR hosting GO. `nvngx_dlss.dll` 310.9.1.0 (DLSS
  4.5-era; J/K/L/M presets present). neuroframe_engine 1.4.0.0 with FULL
  v1-v6 family: `process_frame_v6` (separate output dims - NR-side upscaling
  available) + `dlss5nr_shutdown` (now called on bridge teardown) + rebind/
  scene_score_v1/temporal_status/surface_*. NR build in use:
  `nvngx_dlssnr_RenoDX_4000_series_friendly.dll` 310.8.SF.0. dlls live FLAT
  in models/DLSS (works: flat set auto-labeled after the nvngx_dlssnr*
  inside). NOTE: dll_README.md was clobbered with a plan.md snapshot in
  11074ed (script mixed Path state) - rebuilt; docs edits now path-based only.

## Shipped history (short)

| Commit | What |
|---|---|
| dlsssr commit | ants/dlsssr/ SHIPPED: pure-Python NGX host (no 3rd-party helper dlls): shim.py generates the caller-check nvngx.dll thunk PE in pure bytes (pefile-validated); com/win32/d3d12.py minimal D3D12 layer (device/queue/allocator/list/fence, committed tex/buffers, staging upload+readback, vtable slots per public headers); ngx.py session (Init_Ext sdk 0x15, create/evaluate/release via shim-routed fn, NO Shutdown1 per DLT#75); parameters.py = our own 17-slot MSVC vtable over a dict (snippet-direct) + core-allocated wrapper; sr.py = feature 1 (DLAA/Quality/Balanced/Performance/UltraPerf + J/K/L/M artist presets, per-mode DLSS.Hint.Render.Preset.*); nr.py = feature 18 snippet-direct (DLSSNR.* raw params + best-effort extras); NEW NODE ANTsDLSSSRUpscaler (20 nodes; render->ratio->resize back; SR selector: each flat dll in models/DLSS/SR = own set); DLSS5 'engine' widget: native (default) / Legacy neuroframe (fallback until rig-validated, then removed per owner directive; masks unsupported on native - logged); RIG-CRASH FIXES pre-rig: RESOURCE_DESC was mis-packed (12 args/10 codes, 38B) -> correct 56B "<I4xQQIHHIIIIQ" + sizeof asserts; ALLOW_UNORDERED_ACCESS flag was 0x4 (=ALLOW_RENDER_TARGET) -> 0x8; shim+appData defaults moved off the DriverStore (admin-only) to %LOCALAPPDATA%/ANTs/<tag> with probe fallbacks; 22-check test_dlsssr.py |
| stash commit | discovery v3: helper stash (models/DLSS/HELPERS or *merserk*/hlp-named, apostrophes ignored) excluded from the NR selector + exposed via helper_dll_dirs(); engine fallback searches set dir then stash (loud error lists everything searched); generic folders without an nvngx_dlssnr runtime = OTHER category (out of NR selector); dlss5nr_shutdown called on bridge replacement; authoritative what-goes-where + naming guide in dll_README; pure-Python neuroframe replacement verdict: YES phased (SR host first in ants/dlsssr, then port feature 18 onto it - pair becomes optional legacy; RESEARCH_dlss_sr_upscaler §7) |
| layout commit | package renamed rfactor/ -> ants/ (last pre-rebrand artifact, suite-guarded); discovery v2: models/DLSS/<NR|SR|FG>/<version>/ category sets (NR selector excludes SR/FG; SR/FG reserved for future nodes), dlssnr_<version>/ legacy kept, flat models/DLSS labeled after the nvngx_dlssnr* runtime inside (owner's RenoDX-named dll now visible in the selector); probe v2 hardened (any-root arg + NGX [scan] diagnosis); "Nature"->"Natural" display rename (Merserk's runtime.py maps style 1 to Natural; OreX agrees; ABI ints unchanged); dll_README rebuilt (clobbered in 11074ed) |
| SR research commit | Regular DLSS SR verdict: feasible WITHOUT a compiled bridge — DVT (HicirTech/DLSS-Video-Transcoder, no LICENSE — reference technique only, never copy code) hosts NGX via pure FFI (LoadLibraryExW driver _nvngx.dll + nvngx.dll loader) and runs sr in.png --preset L on stills; presets are HOST-settable on nvngx_dlss.dll via DLSS.Hint.Render.Preset.<Mode> params (enum Default 0, A-F 1-6, J 10, K 11, L 12, M 13, N/O reserved; DLAA variant needs in==out); THE upscaler dll = nvngx_dlss.dll (user-procured, models/DLSS/dlss_<version>/); NGX core is driver-shipped, never bundled; our route = pure-Python ctypes NGX host (ants/dlsssr/) + D3D12 COM plumbing, modes DLAA/1.5/1.724/2/3 + output resized back to input; bonus route: neuroframe frame path (process_cuda_video_frame) has separate output dims (NV12/P010, RGBA8 in descriptors). Probe: tools/probe_dlss_rig.py |
| NR-schedule commit | NR Schedules: ANTs⚡DLSS NR Scheduler node (19 total) emits NR_SCHEDULE; style-per-pass (Nature/Cinematic cycle default), per-pass settings (bypass main widgets), per-pass denoise w/ 4 model slots (inherit main model); passes chain, bridge global post-final-pass; JS UIs in web/ (dynamic rows + live cross-node greying); schedule.py pure validated core; nr_passes removed. Preset verdict: nvngx_dlssnr has NO model-preset param (OreX string-table verify); presets = dll_version folders, artist names table in dll_README (J Crisp / K Stable / L Quality / M Fast) |
| pre-SR commit | DLSS5 pre-SR denoise: optional `denoise_model` socket (UPSCALE_MODEL via ANTsUpscaleModelLoader) + `pre_denoise_strength`; 1x denoisers (SCUNet, PureScale2 1x_PureVision) through the comfy-core mirror; resolution invariant; OIDN + OptiX rejected (MC-noise domain / device-side ABI) |
| GPU commit | DLSS5 GPU acceleration: `gpu_acceleration` widget (Auto def / Force / CPU); `dlss5nr_process_cuda_v6` device-pointer path (torch primary-context interop, zero PCIe when VRAM-resident); HDR bridge torch backend (on-GPU); host fallback optimized; defaults owner-tuned to 220 nits / 1.0 scale (neutral) |
| `588f790` | DLSS5 hybrid: HDR Colour Bridge (Classic def / Anchored / Off), OreX-style temporal history (Auto/Continuous/Per-frame reset), `models/DLSS/dlssnr_<version>/` discovery with ANY filenames (export probing), stale `CATEGORY` fixes, credits, `docs/RESEARCH_dlss5_hybrid.md` |
| `88305cb` | Face Restoration pre-pass (main/dedicated model, ACTIVE toggle); consolidation 20→18 nodes; full ANTs rebrand (version v1.1.0-alpha1, `[ANTs]` prefix); RU comment sweep; README rebrand |
| `2cb2c0c` | upRes fix (crop SR stage was dead code) |
| earlier | upRes interpolator + upscale-model support; ORT fixes; detection state-dict; RetinaFace loader fix; docs |

## Node census (18, all `ANTs`-prefixed)

Loaders (typed-socket pattern): `ANTsFaceSwapModelLoader` → FACE_SWAP_MODEL,
`ANTsFaceRestoreModelLoader` → FACE_RESTORE_MODEL, `ANTsFaceDetectionModelLoader` → FACE_DETECT_MODEL,
`ANTsUpscaleModelLoader` → UPSCALE_MODEL.
SR: `ANTsDLSSSRUpscaler` (pure-Python NGX host; models/DLSS/SR/ sets — each flat dll = its own selector entry).
Scheduler: `ANTsDLSSNRScheduler` → NR_SCHEDULE (consumed by the DLSS5 enhancer's nr_schedule socket; use_nr_schedule toggle).
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
- `ants/swapper.py` — swap engine (inswapper/hyperswap via `ants/engine/`), frame-safe
  restore gate.
- `ants/faceboost/` — restoration (CodeFormer/GFPGAN/VQGAN archs, facelib detection:
  RetinaFace + Yolov5Face; `codeformer_fidelity` widget).
- `ants/upres.py` + `ants/upscaler.py` — face-res interpolation + comfy-core-mirrored
  upscale models (stub seam for tests: `upscale_image_with_model`).
- `ants/masking/` — Mask Builder (ultralytics-free: SAM3/SAM2/RMBG sockets + built-in
  feathered fallback).
- `ants/dlsssr/` — pure-Python NGX host package (technique provenance in the package docstring: DVT shim concept credited/no code copied, Merserk repo = designated future learning source, NVIDIA SDK headers = API surface).
- `ants/dlssnr/` — DLSS5 nodes: `schedule.py` (pure NR-schedule core: parse/validate/clamp/pad, build_pass_plan bypass semantics, STYLES registry for future modes), `scheduler_node.py` (ANTsDLSSNRScheduler → NR_SCHEDULE + 4 denoise model slots, early loud slot validation), `web/dlss5_nr_schedule.js` via root WEB_DIRECTORY (scheduler dynamic per-pass rows → schedule_data JSON; enhancer greys bypassed widgets ⛓; pure graph-state sync), `core.py` (ctypes ABI **v6** struct: style/intensity/tone/
  structure/skin/automask/reset/color_strength/tone_preservation/mask/face_skin_protection/
  grain_preservation/nr_passes/shimmer_suppression/prefer_nvof; `dlss5nr_init(ordinal,
  dll_dir, err)` with 4096-char error buffer; HOST path `dlss5nr_process_v6` + CUDA path
  `dlss5nr_process_cuda_v6(src_dev, dst_dev, w, h, 0, params, err, n)` — masks stay HOST in
  the struct; engine+torch share the CUDA primary context so torch pointers pass directly;
  capability probe `cuda_available()` via `dlss5nr_cuda_supported()`; engine located by
  probing `.dll`s for the `dlss5nr_init` export), `discovery.py`
  (models/DLSS/dlssnr_<version> → flat models/DLSS → legacy models/dlssnr → package dll/),
  optional `denoise_model` UPSCALE_MODEL socket + `pre_denoise_strength` (1x denoisers
  via `ants/upscaler.py`, pre-engine), `hdr_bridge.py` (numpy AND torch backends behind
  op shims — one math path; Classic:
  sRGB→lin → paper-white gain → extended-Reinhard knee → chroma-around-luma; Anchored:
  index-percentile-luma white point + black_lever; defaults 220 nits / 1.0 scale = neutral;
  float32 [0,1] HWC), `node.py` (`gpu_acceleration` Auto/Force/CPU widget; GPU-resident
  loop with per-frame `torch.cuda.synchronize`; temporal_history Auto threshold 0.24 /
  Continuous / Per-frame reset).
- `ants/model_paths.py` — `DLSS_MODELS_PATH = models/DLSS` (+ legacy `DLSSNR_MODELS_PATH`);
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
| test_dlssnr_bridge.py | 36 | HDR bridge math + defaults neutrality + discovery (categories, flat labels, loud error) + GPU decision |
| test_nr_schedule.py | 32 | schedule parse/validate/pad, plan bypass + denoise fallback, loud slot errors, node surface (nr_passes gone) |
| smoke_import.py | — | import + 18-node assert + socket/execute wiring |
| test_pyflakes.py, test_scope_check.py | — | gates |

**Total: 272 checks, all green at the selector-restructure commit (20 nodes; package `ants/`).** Sandbox venv: numpy, opencv-python-headless,
pillow, pyflakes, pefile (NO torch — stub harness only). huggingface.co is TLS-blocked from the
sandbox (DLL zips can't be downloaded there — verify engine versions on the owner rig).

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
- DONE (owner-confirmed): GPU acceleration ("speed on par with OreX"), pre-SR denoise via
  upscale models ("helps a lot"; scunet_color_real_gan/psnr favorites).
- DLSS5 bridge/temporal/discovery: owner has the speed result; bridge defaults 220/1.0 in place.
- DLSS5 rig progress: run 1 frame_np UnboundLocalError (fixed); run 2
  guid() reversed field order, ALL IIDs wrong (fixed vs canonical literals,
  non-circular tests); run 3 CreateCommandQueue list+tuple (fixed) ->
  built the FAKE-COM flow harness (tests/test_native_flow.py, real ctypes
  vtables + d3d12.h signatures cross-checked vs DVT) which exposed 8 more
  pre-rig bugs (u32-for-u64 fence/signal/setevent argtypes, barrier missing
  ALL_SUBRESOURCES @16, illegal CopyResource texture<->buffer ->
  CopyTextureRegion slot 16 + 48B copy locations, sessions never set
  self.gpu, Map/Unmap c_void_p-into-u32, int(c_void_p) traps, win32.wide,
  spurious None iid forwarded by _create); run 4 CreateFence E_NOINTERFACE
  => IID_ID3D12Fence was Fence1's GUID (base = 0a753dcf-c4d8-4b91-
  adf6-be5a60d95a76) + ID3D12Resource ends 0fad not 02ad (both pinned by
  tests). Rig-proven so far: D3D12CreateDevice, CreateCommandQueue,
  CreateCommandAllocator, CreateCommandList (slots + IIDs + call plumbing).
  Sandbox CANNOT verify: shim machine-thunk on Windows, real NGX runtime
  responses, remaining slot behavior under real drivers.
- DLSS5 UI RESTRUCTURED (owner request, shipped): dll_version REMOVED;
  nr_dll_version / sr_dll_version / fg_dll_version (each flat .dll in
  models/DLSS/<CAT>/ listed individually + version subfolders, 'auto'
  default); sr_model J/K/L/M artist presets (default "L - Transformer II
  Quality"); nr_model_preset (best-effort DLSSNR.Hint.Render.Preset);
  pre_denoise_mode (SR 1:1 DLAA default / Upscale Model); refresh = JS
  button at the top of the node (web/dlss5_nr_schedule.js) re-fetching
  object_info; stage_sr_dll() copies non-canonical-named SR dlls to a
  writable dir as nvngx_dlss.dll (NGX core searches the literal name) -
  SR node + DLSS5 pre-denoise both use it (also fixed SR node passing a
  file path where a search dir belongs).
- NR Schedules (new): scheduler JS dynamics + enhancer greying in a real browser, schedule
  off/on parity, per-pass settings bypass, per-pass denoise slots, 4K multi-pass speed.

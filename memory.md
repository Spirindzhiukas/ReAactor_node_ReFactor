# memory.md — Persistent Project State (living ledger)

Update this file in the same commit as any state-changing work. Rules of the project
live in `CLAUDE.md`; the active checklist lives in `plan.md`.

## Snapshot

- **Version:** v1.1.0-alpha1 · branch: `arena/01a0bccd-reaactor-node-refactor` (session branch;
  `main` moves via PR merge)
- **Head at last update:** the native NGX host **RIG-VERIFIED** — UAV flag byte fix (`4e483f1`),
  documented rule + D3D12 debug layer (`cb7c572`), first-frame output smoke test + `ANTS_NR_SOAK` +
  opt-in `ANTS_NR_SESSION_CACHE` (`a3f2c47`), the SR pre-denoise stage alive on BOTH engines
  (`794d34e`), its SR session route fixed after rig run 29, rig run 30's two remaining causes
  fixed (the calling-module geometry + the ONE NGX context per process) with the result-code
  names taken from the header, and rig run 31's reordered SR ladder (`HOST_BUILD`
  `2026-09-21.7`): the driver core leads, both faulting geometries are opt-in
- **Suite:** ALL GREEN — 475 checks + 2 scanners + smoke_import (22 nodes)
  (`test_dlssnr_bridge` 112, `test_dlsssr` 109, `test_native_flow` 67,
  `test_runtime_surface` 58, `test_nr_schedule` 35, `test_upres` 27,
  `test_pure_helpers` 26, `test_facerestore_routing` 21, `test_swapper_state`
  13, `test_detection_state_dict` 7)
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
| dlsssr commit | ants/dlsssr/ SHIPPED: pure-Python NGX host (no 3rd-party helper dlls): shim.py generates the caller-check nvngx.dll thunk PE in pure bytes (pefile-validated); com/win32/d3d12.py minimal D3D12 layer (device/queue/allocator/list/fence, committed tex/buffers, staging upload+readback, vtable slots per public headers); ngx.py session (Init_Ext sdk 0x15, create/evaluate/release via shim-routed fn, NO Shutdown1 per DLT#75); parameters.py = our own 17-slot MSVC vtable over a dict (snippet-direct) + core-allocated wrapper; sr.py = feature 1 (DLAA/Quality/Balanced/Performance/UltraPerf + J/K/L/M artist presets, per-mode DLSS.Hint.Render.Preset.*); nr.py = feature 18 snippet-direct (DLSSNR.* raw params + best-effort extras); NEW NODE ANTsDLSSSRUpscaler (20 nodes; render->ratio->resize back; SR selector: each flat dll in models/DLSS/SR = own set); DLSS5 'engine' widget: native (default) / Legacy neuroframe (fallback until rig-validated, then removed per owner directive; masks unsupported on native - logged); RIG-CRASH FIXES pre-rig: RESOURCE_DESC was mis-packed (12 args/10 codes, 38B) -> correct 56B "<I4xQQIHHIIIIQ" + sizeof asserts; ALLOW_UNORDERED_ACCESS flag "corrected" 0x4 -> 0x8 (*WRONG*: 0x4 IS ALLOW_UNORDERED_ACCESS, 0x8 is DENY_SHADER_RESOURCE - reverted 2026-09-21, this one byte was the entire native-UAV wall); shim+appData defaults moved off the DriverStore (admin-only) to %LOCALAPPDATA%/ANTs/<tag> with probe fallbacks; 22-check test_dlsssr.py |
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
| test_dlssnr_bridge.py | 112 | HDR bridge math + defaults neutrality + discovery (categories, flat labels, loud error) + GPU decision + native output smoke/soak/cache instruments |
| test_dlsssr.py | 109 | NGX host: D3D12 flag table + debug-layer wire bytes, shim/params ABI, SR/NR sessions, probe bat |
| test_native_flow.py | 60 | native NR flow: stub thunk addresses, create/evaluate contract, loud refusals |
| test_runtime_surface.py | 58 | 22-node surface, ANTs branding, pre-pass contract, OPTIONS socket |
| test_nr_schedule.py | 35 | schedule parse/validate/pad, plan bypass + denoise fallback (incl. the SR-stage gate), loud slot errors, node surface |
| test_upres.py | 27 | upRes/upscale paths |
| test_pure_helpers.py | 26 | pure helpers + DLSS discovery policy |
| test_facerestore_routing.py | 21 | restore routing incl. e2e loud-failure |
| test_swapper_state.py | 13 | swapper state dict |
| test_detection_state_dict.py | 7 | detector state dicts |
| smoke_import.py | — | import + 22-node assert + socket/execute wiring |
| test_pyflakes.py, test_scope_check.py | — | gates |

**Total: 475 checks, all green (2026-09-21, `HOST_BUILD` `2026-09-21.7`; 22 nodes; package `ants/`).**
Sandbox venv: numpy, opencv-python-headless, pillow, pyflakes, pefile (NO torch — stub harness only).
huggingface.co is TLS-blocked from the sandbox (DLL zips can't be downloaded there — verify engine
versions on the owner rig).

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
- **The native NR silent-kill / UAV-refusal saga is CLOSED** (18:25 → 01:22): it was our own flag
  byte (0x8 `DENY_SHADER_RESOURCE` instead of 0x4 `ALLOW_UNORDERED_ACCESS`), hidden for hours by the
  silent fallback to a flagless texture. Do NOT re-open the process-state, CUDA-flag-arming,
  feature-level or "degraded driver / reboot" theories — the probe cleared all of them (exit 14) and
  the native node runs (see the two 2026-09-21 entries below).

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
- RIG run 11 = **NGX CONVERSATION LIVE**: SR path passed Init_Ext +
  AllocateParameters + CreateFeature THROUGH THE SHIM on the real driver
  core (caller-check satisfied - no fault); core ANSWERED 0xBAD0000B
  (this entry used to call it FeatureNotSupported - WRONG: the header says
  Fail|11 UnableToInitializeFeature; FeatureNotSupported is Fail|1. See the
  run-30 entry) for feature 1 -> most likely the core refusing a
  snippet outside its managed models root (SR 310.9.1 IS 40-series-ok;
  the 50-series lock is the NR runtime - already solved by using the
  RenoDX-unlocked build). SR now: search_paths include NGX_MODELS_DIR +
  AUTOMATIC snippet-direct fallback (load nvngx_dlss.dll with OWN params,
  same route as NR) on 0xBAD0000B; create_feature decodes result names.
- TEXTURE ROOT CAUSE (all combos failing incl. flagless): 
  D3D12_HEAP_TYPE_DEFAULT was 0 (=UNKNOWN!) -> E_INVALIDARG on ANY desc;
  DEFAULT=1 (UPLOAD 2/READBACK 3 were right by luck). Fixed + fake now
  validates heap types + constant pins.
- LEGACY engine: helper locates files by LITERAL 'nvngx_dlssnr.dll' name;
  owner's RenoDX-named build -> stage_legacy_runtime() copies the set to
  a writable dir under canonical names; manager gets the stage dir.
- OWNER'S APPDATA MAP (post run 16): (a) MASQUERADER CAUGHT -
  sr_staged/neuroframe_caller/nvngx_dlss.dll was 102.5 KB = Merserk's
  CALLER STUB resolving as an SR runtime (a caller dll inside the SR
  tree matched the nvngx_dlss* name filter); ADDED a size guard: SR
  runtimes must be >=1MB (real builds are tens of MB), auto SKIPS stubs,
  explicit choice of a stub raises loud; (b) AppData legacy stage
  (nr_legacy_staged, ~316MB duplicated 158MB dlls) is OBSOLETE - owner
  can delete it; (c) shim on disk IS the current unwind-capable build
  (2.00KB = text still fits one 0x200 page); (d) empty logs dir was
  consistent with the use-after-free. ARTIFACT ROOT MOVED under
  models/DLSS/staged/ANTs/<tag> per owner directive (AppData only as
  read-only fallback) - after next run: shim, staging AND the NGX log
  all live under models/DLSS/staged/ANTs/.
- RIG run 19 VERDICTS (two): (1) NATIVE NR ON THE RENODX BUILD IS CLOSED -
  native-crash.log present but 0 bytes + the first-in-process VECTORED
  handler saw NO exception + instant death = the dll FORCE-TERMINATES the
  process (ExitProcess/fail-fast) at first evaluate: a deliberate host
  check, not an accident (accidents raise first). SHIPPED GUARD:
  dlssnr/discovery.py KNOWN_FORCE_TERMINATOR_MARKERS ("renodx") +
  is_known_force_terminator(); the native engine now refuses such builds
  with a loud [ANTs] RuntimeError (env ANTS_ALLOW_KNOWN_BAD_NR=1
  overrides). Optional clean-fail experiment: stock nvngx_dlssnr from
  DLSS Swapper. (2) THE 9-TILE LEGACY BUG ROOT-CAUSED + FIXED: owner's
  discriminator (no denoise model = correct image; model = 9 gray tiles)
  pinned it to the pre-denoise path: upscale_image_with_model returns a
  movedim VIEW (planar memory); _pre_denoise_frame .to() keeps the
  non-contiguous strides; blend_frames fast path (default strength 1.0)
  returned the view verbatim; process_host/process_cuda passed its RAW
  POINTER to the dll -> planar RGB decoded as interleaved = 3 wrapped
  bands per plane (R,G,B) x 3 phases = the 9 gray tiles. FIXED at 4
  layers: blend_frames contiguous-izes processed; _pre_denoise_frame
  .contiguous(); CUDA loop no empty_like (preserves strides) + frame
  .contiguous() before data_ptr; process_host ascontiguousarray source +
  loud destination validation. Suite 295 (dlssnr_bridge 61).
- RIG run 18: crash again at first evaluate; the param-KEYS dump printed
  (names match DVT's table exactly); NO faulthandler trace, NO NGX log.
  Node masks verified = 1/1 (DVT parity). SHIPPED ants/dlsssr/crashlog.py:
  Python-registered VECTORED EXCEPTION HANDLER (first=1) armed right
  before the first evaluate; on hardware-class exceptions (AV, illegal
  instr, stack overflow, heap corruption, div0) writes
  '[ANTs] NATIVE CRASH: exception 0x... (kind) at <module>+0x<offset>'
  to stderr AND a pre-opened .../staged/ANTs/appdata/logs/native-crash.log,
  then EXCEPTION_CONTINUE_SEARCH (cap 50 lines; non-Windows = no-op).
  Run 19 reading guide: crash line present -> module+offset names the
  culprit dll; NOTHING printed (no file/lines) = fail-fast signature =
  the dll deliberately force-terminates on a non-ReShade host -> pivot.
- RIG run 17: SAME crash at first EvaluateFeature even with padding +
  init keep-alive + unwind info + classic-Init-first (signatures now
  line-for-line identical to DVT: evaluate (list,handle,params,null),
  create (list,i32,params,out)). Remaining variable = this ReShade-oriented
  build x our CPython host. SHIPPED: faulthandler.enable(all_threads) at
  package import (AV -> 'Windows fatal exception' + full Python stacks on
  the console) + a 15s watchdog thread logging 'EvaluateFeature STILL
  RUNNING for Xs' + the first-evaluate log now dumps the param keys.
  Run 18 is a DIAGNOSTIC run: paste the console tail (a crash now prints
  where; a hang now reports itself), plus ideally Task Manager alive/gone
  + Event Viewer python.exe faulting module. If the build simply requires
  a ReShade swapchain host, native NR on THIS dll is a dead end -> the
  decision becomes: stock NR dll needs 50-series; legacy neuroframe engine
  WORKS TODAY (run 12); or a different unlocked NR build.
- RIG run 16: same exact crash at first EvaluateFeature (padding fix
  insufficient). ROOT-CAUSE CANDIDATE #1 FOUND: DVT's initExt does
  `this.keep.push(path, featureInfo)` - the runtime RETAINS the
  appDataPath wide-string and FeatureCommonInfo POINTERS and reads them
  lazily; ours were temporaries (wide died inside try_init, FeatureCommonInfo
  freed at __init__ return) -> use-after-free exactly when the runtime first
  touches them (first evaluate: its own log write / model load path reads).
  This ALSO explains why no NGX log materializes in
  %LOCALAPPDATA%/ANTs/appdata/logs - the log WRITE through the freed
  appDataPath pointer may BE the crash. FIXED: _init_keep on the session
  holds wide + info + path strings for the session's lifetime (pinned by
  test). Run 17 verdict: crash gone = confirmed use-after-free; still
  crashes = next suspects (runtime's ReShade-host expectations), and the
  three diagnostics remain (NGX log dir, Task Manager alive/gone, Event
  Viewer faulting module).
- RIG run 15 (post-unwind-info): **Init(classic) hr=1 + CreateFeature(18)
  hr=1 on the RenoDX runtime - the host handshake is ACCEPTED**; death is
  INSIDE the first EvaluateFeature (server stuck/dies, frontend
  reconnecting forever). Diffed every layer vs DVT: vtable layout IDENTICAL,
  param names IDENTICAL (DLSSNR.*), PerfQuality DLAA=5 same, eval flow same.
  Deltas fixed: our parameter OBJECT was 8 bytes (DVT pads 64 "so stray
  reads stay inside our memory" - heap corruption on stray access = crash
  inside first evaluate signature); added DLSSNR.UICorrection (DVT parity);
  NULL-handle check after CreateFeature. PENDING DIAGNOSTICS from owner:
  (a) NGX's own logs at %LOCALAPPDATA%/ANTs/appdata/logs (we set that as
  appDataPath - the RUNTIME writes its view there), (b) Task Manager at
  hang: python.exe alive (deadlock) or gone (AV), (c) Event Viewer
  python.exe Application Error: faulting module + exception code.
- RIG run 14 (first run with Claude's fix): HARD PROCESS CRASH, no
  traceback, right after load_bridge logs - the first time the REAL thunk
  machine code executed (the trampoline bug meant the thunk bytes were
  never jumped to before). RESPONSES SHIPPED: (a) shim now carries proper
  .pdata/UNWIND_INFO for the 3 non-leaf thunks (v2, prolog 4, 1x
  UWOP_ALLOC_SMALL 56, shared info; 3 RUNTIME_FUNCTIONs; exception dir
  wired; pefile-verified + pinned) so C++ exceptions can unwind through
  our frames (Claude's flagged risk); (b) in-flight NGX call markers in
  the log (init/createfeature/first-evaluate with hr codes) so a crash
  names the call in flight; (c) owner asked to pull the faulting MODULE
  + exception code from Event Viewer (Application, python.exe error) -
  that single field decides shim-thunk vs runtime-vs-reshade-host fault.
  Note: pre-denoise log line missing in run 14 output - either buffering
  loss on hard crash or another node crashed first; markers will tell.
- **THE BIG ONE (Claude Sonnet 5's catch, run 13): ngx.NgxModule stored
  the shim thunk as a ctypes CALLABLE; fn() then built
  CFUNCTYPE(restype, *argtypes)(that_callable) = a Python-callback
  TRAMPOLINE, not a pointer to the thunk -> every NGX call's args 2..n
  re-converted through the inner 1-arg proto (mangled), callback
  exceptions swallowed as 'Exception ignored', garbage returns
  (0xB4C6385B, 0xe06d7363 C++ exceptions inside the runtime on bogus
  handles). FIXED: _fwd_stub = int(fwd_create) raw address; fn() binds
  win32.callable_at(self._fwd_stub, argtypes, restype) + Claude's assert
  (cast(stub).value == thunk). NEW HARNESS PROBE: production fn() bound
  against REAL in-process callback addresses (real native jump, all four
  64-bit args verified intact - the test that would have caught it; the
  flow harness had patched fn() itself, which is WHY it was blind).
  Getter hardening per Claude: type-mismatched parameter reads now log
  name+type and return FeatureNotFound instead of raising inside the
  callback. Claude's open question (why Init/CreateFeature survived the
  broken path): unresolved but moot - run 13's 'successes' may also have
  been garbage returns; expect the REAL Init/CreateFeature answers on
  run 14. Claude's .pdata/unwind note: shim thunks have no unwind info -
  relevant only if C++ exceptions escape through them; revisit if seen.
- RIG run 12 = **LEGACY ENGINE WORKS END-TO-END** (staged set initialized
  on the 4090, full run 13.86s, output produced) AND **SR NATIVE PIPELINE
  WORKED** (SR session create_feature(1) + evaluate + readback through the
  shim - the 9-tile/bw output question is separate). NR-on-RenoDX failed at
  list Close E_FAIL AFTER its CreateFeature returned success -> the
  ReShade-oriented build likely targets the CLASSIC 4-arg Init (DVT's
  rig-proven NR flow) - snippet-direct sessions now Init4-FIRST (Init_Ext
  fallback); submit_and_wait recovered from Close E_FAIL (loud warn, reset
  list+allocator, continue). Legacy staging moved OUT of AppData per owner:
  canonical-named sets load IN PLACE (Merserk_DLLS masters); others stage
  under models/DLSS/staged/<dll>-<size>/ content-addressed (never rewrites
  -> no more locked-dll PermissionError). Widget rename: pre_denoise_mode
  choice "Upscale Model" -> "Denoise Model" (matches denoise_model input).
  9-tile output hypothesis: repeated-frames batch + Continuous temporal
  history on legacy + SCUNet gray-push accumulates per frame (preview grid
  shows progressive drift) - NOT yet confirmed; asked owner for input
  batch/schedule state.
- RIG run 10: E_INVALIDARG PERSISTED on the first texture even with
  TEXTURE2D=3 (line numbers confirm the fix was live; desc/heap bytes now
  byte-identical to DVT's rig-proven layout; UAV+COMMON creation is legal
  per DVT engine.ts) -> remaining suspects are driver-specific desc rules
  we cannot see. RESPONSE: create_texture2d is now ADAPTIVE - probe ladder
  (UAV flag+UAV state like NVIDIA's NGX hosts / UAV+COMMON like DVT /
  no flags+COMMON), first accepted combo cached on the device, loud log
  per attempt; transition() skips redundant barriers (DVT parity). Legacy
  engine fallback also fixed: DLSSStandaloneManager takes (dll_dir) only -
  the engine-switch patch passed a phantom 2nd arg. Run 11: if the ladder
  exhausts, the error will carry WHICH combos failed -> paste it.
- RIG run 9 = CreateCommittedResource E_INVALIDARG on the FIRST texture:
  our RESOURCE_DESC passed Dimension=2 (TEXTURE1D!) - DVT constant table
  says TEXTURE2D=3; and buffer descs had Layout=UNKNOWN(0) instead of
  ROW_MAJOR(1) (buffers would have died at first upload). Both fixed vs
  DVT constants; the FAKE COM layer now ENFORCES driver desc rules
  (dim/layout/sample-count/height; UAV-flag+COMMON creation is LEGAL -
  DVT rig-proven), so desc regressions fail the harness, not the rig.
  Run 10 should hit Init_Ext for real.
- RIG run 8 = **Init_Ext threshold reached** (shim exports resolved, module
  identity clean, GpuContext live on the 4090): new error was gpu=None at
  the init_ext CALL SITE - _sr_session_for built the SR pre-denoise session
  with self.native_gpu still None (it was only created inside
  _native_session_for, which runs AFTER pre-denoise). FIXED: shared
  _ensure_native_gpu() used by both session factories (+ regression check;
  Gemini's torch-device-binding theory was off - the None was OUR
  GpuContext creation order). Run 9 = the first real NGX Init_Ext answer.
- RIG run 7: IDENTITY CHECK PASSED (no collision error) -> Windows maps
  our hand-built shim PE cleanly (loader acceptance proven). New error:
  callable_at crashed = CFUNCTYPE ctor rejects c_void_p INSTANCES (needs
  raw int) AND GetProcAddress had NO restype -> ctypes truncated 64-bit
  addresses to c_int (silent time bomb for every kernel32 pointer return).
  FIXED: LoadLibraryExW/GetProcAddress/CreateEventW restype=c_void_p;
  callable_at normalizes int|c_void_p|_Pointer -> raw int; export_address
  never int()s a c_void_p. NOTE: with restype fixed we can't tell yet
  whether run 7's GetProcAddress failure was real parser refusal or would
  have succeeded - RVA fallback covers both. Run 8 should pass the shim
  and reach Init_Ext.
- RIG run 6 (shim-first live): fwd_set_slots STILL missing on BOTH
  attempts, including a first-run-in-fresh-process -> collision by another
  module UNLIKELY; suspect #1 = Windows GetProcAddress refusing OUR
  hand-built shim's export directory (LoadLibrary does NOT walk the export
  dir; pefile validates structure, not Windows semantics; full header audit
  found no defect but that proves little). FIX SHIPPED: exports now resolve
  by BUILD-TIME-KNOWN RVAs (HMODULE = relocated base, ASLR-safe) with
  GetProcAddress as cross-check -> shim works even if GetProcAddress is
  fussy; win32.get_module_filename identity check names the ACTUAL file
  under the handle (definitive collision vs parser verdict on next run);
  get_proc error text now includes WinError. If run 7 still fails at
  fwd_create the RVA answer was wrong (would mean the handle isn't our
  image) - the identity check will say so explicitly.
- RIG run 5 (post-restructure): (a) scale UnboundLocalError in the
  pre-denoise log block (my refactor left `if scale != 1` outside the else)
  - fixed; (b) fwd_set_slots GetProcAddress failure = WINDOWS LOADER BASE-
  NAME COLLISION: NgxModule loaded the ENGINE first, whose load pulled the
  owner's real nvngx.dll; our shim is file-named nvngx.dll -> LoadLibraryExW
  returned the real one -> no fwd_* exports. FIX: load the shim BEFORE the
  runtime (also the design: the runtime's nvngx.dll imports then bind to
  the shim); collision now gets a loud restart-hint error. LESSON: shim
  before engine, always. (c) DLSS family logs now tagged
  "[ANTs⚡DLSS5 Frame Enhancer]" (ants/log.py dlss_logger) per owner.
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

### 2026-09-20 — run-21 prep: guard softened + reference-host probe
- Force-terminator guard RESHAPED (owner consent after rename bypass): discovery.resolve_nr_runtime_path(choice, skip_known_bad=False) — native AUTO now skips RenoDX-marker builds (saw_bad flag); if ONLY bad builds exist → loud [ANTs] RuntimeError (EXPLICITLY hint); EXPLICIT picks always honored. node.py native branch: WARN-and-proceed for explicit picks (Merserk's C++ host runs it fine; our provider gap under analysis); ANTS_ALLOW_KNOWN_BAD_NR REMOVED. Legacy staging path unchanged (no skip) — these builds are fine behind the legacy engine.
- tools/probe_neuroframe_host.py NEW: stdlib-only PE32+ export/import decoder + class-based string miner, READ-ONLY, targets Merserk's proven host (neuroframe_engine_neural_rendering.dll, neuroframe_caller.dll full-ish strings; nvngx_dlssnr.dll 5 capped classes: NGX API surface, ReShade/RenoDX markers, failure words, GPU/driver gates, provider/dispatch/logging). Prints paste-sized report + writes probe_neuroframe_host_report.txt to cwd. VALIDATED on hand-crafted synthetic PE32+ (exports/imports/verdict-lead all correct).
- ngx.py init facts (re-checked): NR path = classic-Init-first with Init_Ext fallback; classic returned hr=1 on rig so fallback never fired — Init-variant question only answerable from his bridge source or probe.
- Suite: 299 checks ALL GREEN (bridge 65 [+4], schedule 32, pure 26, surface 58, sr 34, flow 16, facerestore 21, swapper 13, detection 7, upres 27) + pyflakes. .venv_test was wiped by sandbox reset — rebuilt with numpy/cv2/PIL/pefile/pyflakes.

### 2026-09-20 — run-21 round 2: owner files read, probe no-arg fix
- Owner ran probe WITHOUT argument → printed docstring only (no analysis). FIXED (1b92344): no-arg mode now probes the folder the script sits in if runtime/dlssnr/nvngx_dlssnr.dll exists there (owner placed the script in VE root; validated on synthetic /tmp/velike tree). Re-run = same invocation, no argument.
- src/core/ngx_runtime.py (465 B, Python): NGX_RUNTIME_LOCK = threading.RLock() + policy "Normal session teardown must not unload NGX or call its core shutdown routine". OUR host ALREADY implements never-Shutdown/never-unload (ants/dlsssr/ngx.py line 22) → teardown/unload ELIMINATED as the host-contract delta. His whole bridge stack is PYTHON (neural_bridge.py) calling his C++ engines — probe still needed to see which module exports/imports NVSDK_NGX_*.
- native-chrome.log = UI window-chrome logs (hwnd/dpi) — likely unrelated to NGX. app log: his NR image render completed 1.3 s on 2026-09-20 → the dll is healthy on his stack TODAY. config.ini: his UI uses nr_style = Natural (community naming confirmed at source), nr_passes = 3 multi-pass scheme, ai_gpu_uuid auto (multi-GPU by UUID).
- NEW OPEN QUESTION sent to owner: were runs 14-20 native NR in a FRESH ComfyUI process (no earlier native SR/legacy NGX in the same session)? Two co-resident NGX runtimes is what his lock comment hints at. If prior runs happened in-process, first-eval death could be co-residence, not Init contract.

### 2026-09-20 — run-21 round 3: run_probe.bat shipped
- tools/run_probe.bat NEW (CRLF, committed): double-click runner for the probe. Lives in VE root next to probe_neuroframe_host.py. Python auto-detect order: COMFYUI_PYTHON edit-line → COMFYUI_PORTABLE env → %VE_ROOT%\python_embeded → ..\ComfyUI_windows_portable → py launcher (-3) → where python with find /i /v WindowsApps (Store-stub filter) + -c "import sys" sanity. Passes "%~dp0" (backslash-stripped) as the VE root arg → report.txt lands next to the bat; type|clip copies the FULL report to clipboard automatically; pause keeps console open.
- Batch traps fixed during authoring: parens in echo text inside ( ) blocks close the block early (reworded); echo of %%P path with parens replaced by find-filter inside the for /f subshell; trailing \ of %~dp0 stripped before quoting (Windows arg-escaping).
- Awaiting from owner: probe report (console paste or clipboard via bat) + answer whether native NR crashes happened in a process that had already run SR/legacy NGX in-session (fresh-process discriminator).

### 2026-09-20 — run-21 round 4: probe path bug fixed (bin/ segment dropped)
- BUG: probe looked in runtime/dlssnr but the owner's own folder map says bin/runtime/dlssnr — rig run found all 3 files missing and still clipboard-copied an empty report. FIXED: find_dll_dir(root) candidates (bin/runtime/dlssnr, runtime/dlssnr, bin/dlssnr, dlssnr, .), big-dll presence decides; no-arg mode uses the same resolver; total miss → loud message + rc=3; bat now clips ONLY on rc=0 (else branch tells owner to paste console). Validated: bin-layout synthetic (A no-arg, B arg), wrong-root rc=3, legacy runtime/dlssnr layout regression — all good; pyflakes OK.
- Owner rig bat ergonomics CONFIRMED working: py launcher picked C:\WINDOWS\py.exe -3, correct target, pause kept console.

### 2026-09-20 — RUN 21 VERDICT: host-contract delta is ARCHITECTURAL (core + shim + Init_Ext)
- Probe report (owner rig, md5s recorded): engine 571,904 B / caller 104,960 B / nvngx_dlssnr 165,830,144 B md5 8fc583152592d02fd5bc78ee15cddcc6 (55 NVSDK_NGX_* exports incl. CUDA/D3D11/D3D12/VULKAN surfaces + Set{RuntimeParams,OverrideStatus,TelemetryEvaluate}Callback + GetSnippetVersion). ZERO ReShade/RenoDX markers, ZERO GPU-gate strings in the runtime.
- DECISIVE: Merserk's engine (a) hard-REQUIRES the NGX driver CORE _nvngx.dll in-process ('Could not load NVIDIA NGX core _nvngx.dll... DriverStore nv*.inf_*' error string; loader order core -> snippet -> CALLER SHIM), (b) inits via 'DLSSNR snippet Init_Ext via caller shim' with NVSDK_NGX_D3D12_Init_ProjectID also resolved, (c) sweeps 'API versions 0x13..0x20', (d) has seen AVs itself ('Native access violation inside CreateFeature(18)', 'session corrupted by a previous native crash') -> clean errors + SetUnhandledExceptionFilter recovery. Caller shim exports only DLSSNR_Call{Init,Create,Evaluate,Release} (102 KB; the snippet-host handshake). OUR path did NONE: no core, classic-Init-first, no callbacks -> runs 14-20 first-evaluate death.
- SHIPPED (suite 305 ALL GREEN: bridge 65, schedule 32, pure 26, surface 58, sr 39 [+5 run-21 checks], flow 17 [+1 Init_Ext-order], facerestore 21, swapper 13, detection 7, upres 27): ngx.py NR path (use_own_parameters) now (1) PRELOADS the driver core (explicit _nvngx.dll next to the snippet first, then locate_ngx_core() = System32/DriverStore nv*.inf_* newest; graceful skip; pinned _core_handle; freed in session close AFTER module.close - reverse order), (2) Init_Ext-FIRST with sdkVersion sweep 0x13..0x20 on failure, classic 4-arg last resort, (3) ANTS_NR_RUNTIME_CALLBACKS=1 env experiment registers pinned no-op CFUNCTYPE callbacks on the snippet's three Set*Callback exports. Docstring updated. RUN 22 = rig test: plain native NR (core+Init_Ext), then with env var if still dying.
- BUG CAUGHT BY SUITE: my close() edit first landed in NgxModule.close (AttributeError _core_handle, native_flow 15/16) - moved to NgxSession.close (reverse load order); fake in test_native_flow records ('Init_Ext', sdk) - checks updated (Init_Ext accepted once at 0x15, no Init4, CreateFeature(18) follows).
- Awaiting owner: fresh-process question (SR/legacy before NR in-session?) still unanswered; run-22 console tail + native-crash.log after each attempt.

### 2026-09-20 — RUN 22 VERDICT: death moved DEEPER - RuntimeParamsCallback hook proven
- Rig log (03:21): core preloaded (System32 DriverStore nvmdsi.inf_amd64_05d1e242e80cf105) + all 3 callbacks registered + Init_Ext accepted FIRST TRY at 0x15 (hr=1) + CreateFeature(18) hr=1 + EvaluateFeature -> **'[ANTs] snippet callback fired: NVSDK_NGX_SetRuntimeParamsCallback'** -> crash. FIRST positive evidence of snippet executing host-handshake code in evaluate (runs 14-21 died silently before this). The Python stub RAN (logged), returned 1, snippet died AFTER consuming the answer. Contract gap = what the callback must return/fill. Owner confirmed: dll bytes identical to Merserk/ReShade's, only renamed.
- SHIPPED (suite 305 green): instrumented callbacks - 4-slot pointer prototype (x64 extra regs safe), ANTS_NR_CALLBACK_RET (stub return, default 1), ANTS_NR_CALLBACK_DUMP=1 (IsBadReadPtr-guarded 64-byte hex dump per non-NULL pointer arg; struct imported into ngx.py). RUN 23 = CALLBACKS=1 + CALLBACK_RET=0 + CALLBACK_DUMP=1 -> paste console dump lines + native-crash.log content (0 bytes ALSO informative = deliberate abort/terminate, not an exception).
- Run-21 wiring CONFIRMED working on rig (preload + Init_Ext-first + callbacks all logged). Note: Merserk's caller shim (DLSSNR_Call*, 102 KB) is the real callback provider - his engine strings 'feature-18 host render parameters ABI v3/v4/v5/v6' document versioned struct ABIs -> if callback tuning fails, next decisive input = neural_bridge.py chunks from owner (the DLSSNR_Call* ABI + struct layouts).

### 2026-09-20 — RUN 23 DECODE + SELF-INFLICTED-AV VERDICT; fault-free dumper shipped (c6a9215)
- Callback ABI DECODED (rig dump): RuntimeParamsCallback(0x12=feature id 18, 0x8, 0x4AA0E070 = readable u32 table [1,2, 0,0, 0,0, 1,2, 1,0, 0,0, 1,2, 2,0...], 0x7FFE99D1F710 = ANSI printf format "DLSSNR:  color (%d,%d %dx%d) Mvec (%d,%d %dx%d) scale (%.2f,%.2f...)"). => the hook is a LOG/REPORT callback (snippet reports its render params to the host, Merserk-style printf logging), NOT a required-answer contract; ~10 conversions => varargs live in x64 stack homes (floats in XMM invisible to integer-slot prototypes).
- THE TWO 'NATIVE CRASH 0xC0000005 @ KERNEL32.DLL+0x27799' BANNERS WERE OUR OWN DUMPER: line 326 IsBadReadPtr call faulted inside kernel32 (deprecated API, racy on volatile pages); VEH logged banner + faulthandler, returned CONTINUE_SEARCH, IsBadReadPtr's SEH swallowed the AV, execution continued (arg1/2/3 lines print AFTER the banners; native-crash.log = exactly those 2 lines). Return-0/return-1 both dying is unproven - the deaths may all have been the dumper. LESSON: never probe memory with IsBadReadPtr in a VEH-logged process; VirtualQuery is fault-free.
- SHIPPED c6a9215 (suite 307: dlsssr 41 [+2 pins: fault-free dumper / resolver ships], flow 17 [restored del-flags idiom - REMOVING that 'unused' local breaks a ctypes callback: UnboundLocalError swallowed -> fake returns NULL -> 'NULL pointer access'; pyflakes-cleanup trap]): ngx.py dumper = VirtualQuery (MEM_COMMIT, not NOACCESS/GUARD, region-bounded) + scalar-arg skip + printf-format-string text decode + 8 slots (captures stack ints 5-8). tools/resolve_crash_offset.py: names MODULE+0xRVA via export table bisect (+ neighborhood), System32 convenience (usage: python tools\resolve_crash_offset.py KERNEL32.DLL 0x27799). Self-tested on synthetic PE (bug lesson: fixture name string overwrote the func-RVA array -> 'RVA 0x43'='C'+NUL).
- models\ symlink -> i:\ComfyUI\MODELS VERDICT: not implicated (legacy dlls run through the same link; this run found/loaded/inited/created through it; file APIs return errors, never AV, on symlinked paths). Parked unless the resolver names a file API.
- RUN 24 = same env lines (CALLBACKS=1, RET=0, DUMP=1) + pull + run resolver on KERNEL32.DLL 0x27799 -> expect: dumps complete cleanly, NO AV banners, then either SUCCESS or a silent terminate (deliberate abort => decisive move = neural_bridge.py chunks for the real callback impl). If silent: also try RET knob sweep not needed - ask for chunks.

### 2026-09-20 — RUN 24 VERDICT: death is a DELIBERATE TERMINATE; IAT termination tracer shipped
- Run 24 (fault-free dumper, RET=0): callback fired, dump completed (arg0=18, arg1=8, arg2 u32 table, arg3 printf fmt, arg4/arg7 = 0x200000001-style PACKED BY-VALUE u32 pairs (1,2) - stack varargs homes mirror arg2 table rows 1-4; %.2f doubles ride beyond slot 8), console ENDED at arg7 = silent death persists. NO exception anywhere + empty crash file => NOT an exception: deliberate terminate via the no-exception paths (abort/__fastfail/ExitProcess family) - the ONE class our VEH black box cannot see. Watchdog (15s hang alarm) stayed silent too => death within ~15s of the callback, not a hang.
- Resolver RIG-VERIFIED by owner: KERNEL32.DLL 0x27799 = IsBadReadPtr+0x29 (1671 exports parsed) - run 23 false-alarm chain fully closed; owner ran it standalone fine.
- SHIPPED: crashlog.install_termination_trap (gated ANTS_NR_TERMINATION_TRAP=0 to disable; auto-arms inside the ANTS_NR_RUNTIME_CALLBACKS block, BOTH modules: snippet + driver core). In-memory PE import walk (base+RVA, e_lfanew+144 = dir[1]) -> VirtualProtect(PAGE_READWRITE) -> patch every {ExitProcess, TerminateProcess, RaiseFailFastException, NtTerminateProcess, abort, exit, _exit, terminate} IAT thunk with a log-first trampoline: "[ANTs] TERMINATION via <name>; call chain: mod+0xOFF <- ..." (RtlCaptureStackBackTrace 8 frames via _module_for) -> chains to original. Import audit: unimported names => killer is statically linked CRT abort/fastfail or inline syscall. Tests: flat-PE fixture + fake k32 (lessons: WINFUNCTYPE is Windows-only -> getattr fallback CFUNCTYPE; create_string_buffer COPIES - assert on the ctypes image, not the source bytearray). Suite 308 (dlsssr 43).
- RUN 25 protocol: same env lines (CALLBACKS=1, RET=0, DUMP=1), git pull, run once. Expected NEW lines: "termination trap on snippet: patched ..." (or the not-imported audit) at session init; on death: "[ANTs] TERMINATION via <name>; call chain: nvngx_dlssnr+0xOFF <- ..." -> paste it -> resolve_crash_offset.py names the call site. If death comes with NO TERMINATION line: killer bypasses named imports (static CRT abort / inline syscall / patched elsewhere) => neural_bridge.py chunks become mandatory. If NO death: NR works, move to quality tuning + drop-Merserk cleanup.

### 2026-09-20 — RUN 25 = STALE BUILD (no trap code ran); resolver bat shipped; OWNER STANDING RULE: diagnostics ship as bats/ps1
- Run 25 console = character-identical to run 24 (dump to arg7, silent end, NO trap output of any kind). Every install_termination_trap path emits at init (patched list / not-imported audit / walk failure) => the process never had the new code: owner did NOT git pull before the run. NO new diagnostic data; the death question remains exactly where run 24 left it.
- OWNER RIG PATHS (pinned by owner request): embedded python C:\ComfyUI_PORTABLE\python_embeded\python.exe; owner keeps the resolver copy at C:\ComfyUI_PORTABLE\resolve_crash_offset.py; NR dll C:\ComfyUI_PORTABLE\ComfyUI\models\DLSS\NR\nvngx_dlssnr_RenoDX_4000_series_friendly.dll; crash log C:\ComfyUI_PORTABLE\ComfyUI\models\DLSS\staged\ANTs\appdata\logs\native-crash.log; node root C:\ComfyUI_PORTABLE\ComfyUI\custom_nodes\.
- OWNER STANDING RULE (imperative, owner-requested): ALWAYS ship rig test/diagnostic tooling as ready .bat (or .ps1) files - never raw commands. tools/resolve_offsets.bat now does: baked paths, system-python fallback, 'KERNEL32' first-arg switch, bare-launch interactive prompt, auto-clipboard return, one marked owner-editable block, CRLF, zero parens on executable lines. Test-pinned (dlsssr 44, suite 309).
- RUN 26 PROTOCOL: (1) cd C:\ComfyUI_PORTABLE\ComfyUI\custom_nodes\<node> && git pull; (2) VERIFY new code: findstr /c:"TERMINATION_TRAP" ants\dlsssr\ngx.py must print a line - if not, pull failed; (3) same env lines (CALLBACKS=1, RET=0, DUMP=1); (4) run once; EXPECT right after the 3 'callback registered' lines: '[ANTs] termination trap on snippet: patched ...' (or the audit line); at death: '[ANTs] TERMINATION via <name>; call chain: ...' -> resolve offsets with resolve_offsets.bat <offset>. Death with NO TERMINATION line = static CRT abort/inline syscall => neural_bridge.py chunks mandatory. No death = NR works.

### 2026-09-20 — RUN 26: killer DODGES both patched IATs; ntdll detour shipped; owner workflow pinned (GitHub Desktop, no git commands)
- Run 26 rig: trap ARMED + verified ('termination trap on snippet ... patched KERNEL32.dll!TerminateProcess, KERNEL32.dll!ExitProcess' + same on driver core + audit line: no NtTerminateProcess/RaiseFailFastException/_exit/abort/exit/terminate imports). Death STILL silent after the arg dump, NO TERMINATION line => kill avoids every patched import. Classes: unpatched third module / static CRT abort->__fastfail(int 29h) / raw syscall. Deliberate no-import kill - strong anti-host-check smell.
- OWNER WORKFLOW (standing): working clone = I:\AI SHITE\CODING\GITHUB\ReAactor_node_ReFactor, SYMLINKED into C:\ComfyUI_PORTABLE\ComfyUI\custom_nodes\ReAactor_node_ReFactor. Owner syncs via GITHUB DESKTOP after agent push (waits ~1 min). NEVER give owner git commands; findstr pre-checks may print nothing if run before his scoop - the run itself is the proof.
- Console vs files: STATUS lines = logger; [ANTs] trap/detour/TERMINATION lines = raw fd2 + native-crash.log. => After a death, paste models\DLSS\staged\ANTs\appdata\logs\native-crash.log CONTENT - the TERMINATION line survives there even if the console buffer is lost (owner saw STATUS lines missing from comfyui's log file - direction of the split confirmed).
- Resolver rig bug fixed: junk arg ('resolve_offsets.bat' pasted at the prompt) hit int(raw,0) -> traceback; now '[skip] not an offset' + functional test w/ inline synthetic PE. Bat prompt reworded; zero-paren guard caught my own paren regression in that prompt (rule validated again).
- SHIPPED: crashlog.install_ntdll_terminate_detour (same env gate): every self-termination funnels through ntdll!NtTerminateProcess - verify Win10/11 stub prologue (4C8BD1 B8.. .. 0F05 C3), steal verbatim to RWX buf, entry = mov rax,tramp; jmp rax; trampoline logs chain (RtlCaptureStackBackTrace) then runs stolen stub (real syscall). Loud skip on unexpected prologue. NOTE: normal ComfyUI exit prints one TERMINATION line at shutdown = expected traffic.
- RUN 27: scoop via Desktop, same env lines, run once. (a) 'TERMINATION via ntdll!NtTerminateProcess ... call chain: ...' -> paste console AND native-crash.log; resolve the named module offsets with resolve_offsets.bat -> killing module identified (third dll = plot twist; snippet = its own check). (b) Still silent => int29/raw syscall => hard anti-host check => neural_bridge.py chunks MANDATORY. REQUESTED in parallel either way: neural_bridge.py (63 KB) in ~8 KB chunks.

### 2026-09-20 — neural_bridge.py DELIVERED + DECODED: his Python NEVER touches NGX; compiled bridge dll is the host; NO callbacks registered; RUN 27 = callbacks OFF
- ARCHITECTURE TRUTH: Neuroframe python never calls the NGX runtime. A COMPILED bridge dll (paths.DLSSNR_BRIDGE, exports dlss5nr_*) owns process-wide NGX; python = ctypes binds + per-call DAEMON-THREAD watchdogs (45s x nr_passes, cap 180s; timeout/exception => session POISONED, buffers kept alive forever) + NGX_RUNTIME_LOCK single-flight (separate .ngx_runtime module). Bridge dll internal: Init_Ext equiv + CreateFeature(18) + CUDA<->D3D12 interop (cuda_result in every result struct).
- **HIS PYTHON REGISTERS NO Set*Callback ANYWHERE** => decisive experiment (RUN 27): our current host (core preload + Init_Ext + own params + traps) with ANTS_NR_RUNTIME_CALLBACKS=0. Callback registration is the only major delta we have never A/B-ed since the run-21 wiring (runs 14-19 predate it; 22-26 all had =1). Survives => registration itself implicated => mimic his host (register NOTHING). Dies => traps should print TERMINATION (ntdll detour armed); still silent => __fastfail/int29 class (bypasses VEH+IAT+ntdll) => run 28 = text-patch snippet int29->int3, VEH logs site.
- Bridge ABI pins (adoptable contract): dlss5nr_frame_abi_version()==6 HARD requirement; init(ordinal_cudaid, DLSSNR_DIR_wide, err,4K); rebind(ordinal,err,len) poisons on fail; release_session at idle boundary; **NEVER Shutdown / NEVER unload ("both observed to WEDGE after a successful feature-18 evaluation" - confirms our no-Shutdown policy)**; DLSSG-FIRST priming before feature 18 ("the D3D12 NGX core keeps the FIRST feature search path for the lifetime of the process" - SR+NR coexistence implication for our nodepack: whichever feature family inits first pins the search path).
- RenderParameters V3->V6 layouts (v3 prefix stable): {struct_size u32, abi u32, style i32, intensity f32, tone f32, structure f32, skin f32, automask i32, reset i32, color_strength f32, tone_preservation f32, mask_memory_type u32(HOST0/CUDA1/NONE2), mask_w/h/stride u32, mask_plane u64} +v4{face_skin f32, grain f32} +v5{nr_passes i32} +v6{shimmer_suppression f32, prefer_nvof i32}. Fields map 1:1 onto our DLSSNR.* keys (tone=GlobalToneStrength, structure=LocalStructureStrength...). Defaults: nr_passes 1, prefer_nvof false.
- FrameDescriptorV1: {struct_size u32, abi u32, memory_type u32, pixel_format u32(RGBA8=1/NV12=2/P010=3), w u32, h u32, planes[3] u64, strides[3] u32, color_matrix u32, color_range u32, rotation u32, reserved u32, timestamp i64}. FrameResultV1: {sizes, abi, ngx_create i32, ngx_evaluate i32, cuda i32, scene_reset i32, scene_score f32, reserved u32, upload_bytes u64, download_bytes u64, timestamp i64}. **Defaults ngx_create/evaluate = 0x00000001 => hr=1 success pin re-confirmed.** Restart-poison markers: corrupt/access violation/device recovery failed|aborted/device removal/reinitialization failed (HIS host sees AVs too).
- Scene/reset heuristic: dlss5nr_scene_score_v1(desc*, threshold=0.24, out score f32, out reset i32) - reduced-luma signature scoring drives DLSSNR.Reset per frame. Host float path: dlss5nr_process_v6(c_float* src, dst, w, h, params, err, len) - HOST BUFFERS ARE f32 RGBA in the legacy entries; v6 frame path is descriptor-based (RGBA8 u8). CUDA: bridge wraps nvcuda.dll driver API (primary ctx retain, mem_alloc, HtoD/DtoH, synchronize); DLPack capsule export of bridge-owned NV12/P010 surfaces to PyAV (retain/release + deleter records).
- Owner resolver outputs (bat VERIFIED end-to-end incl [skip]): 0x27799 resolved against the NR dll = NVSDK_NGX_CUDA_Shutdown1+0x619 - NOTE: that is the OLD run-23 KERNEL32 offset applied to the WRONG module (offsets are module-relative; no NR-dll offset is pending until a TERMINATION line names one). Trivia that may matter: the dll exports a full NVSDK_NGX_CUDA_* family (CUDA backend entry points) - plausible his host evaluates feature 18 CUDA-direct via the snippet's CUDA API rather than our D3D12 staging path.
- RUN 27 PROTOCOL: launch bat: change set "ANTS_NR_RUNTIME_CALLBACKS=1" -> =0 (RET/DUMP lines stay, harmless; traps + ntdll detour auto-arm). Outcomes: (a) NR WORKS => registration was the poison; next = print his format line from the bridge-side or keep none; (b) TERMINATION line => resolve named offsets; (c) silent => int29/fastfail => ship int29->int3 text patch (VEH logs STATUS_BREAKPOINT site) as run 28. Next inputs queued: src/upscale/video/native.py (39 KB), src/core/runtime.py (43 KB), bridge C++ source if it exists in his repo.

### 2026-09-20 — native.py (RTX Video bridge) decoded; STATIC import-probe shipped (engine-dll discriminator, no execution)
- native.py = the THIRD compiled-bridge in his stack (rtxv_* exports; runtime nvngx_vsr.dll + nvngx_truehdr.dll, SDK 1.1.0). Same doctrine everywhere: python NEVER touches NGX/driver directly; every native call = daemon thread + event + timeout (180s) + poison state; NGX_RUNTIME_LOCK serializes ALL feature families process-wide (NR + VSR/HDR + frame interp share it).
- NEW ORDERING PIN: FFmpeg CUDA primary ctx (av_hwdevice_ctx_create, primary_ctx=1) is created+retained BEFORE NGX loads - "some driver releases reject the first av_hwdevice_ctx_create after NGX has initialized CUDA... releasing before NGX can recreate the driver-ordering hang". => CUDA context ordering matters around NGX init; our pure-D3D12 host never orders CUDA, but the snippet's internal CUDA usage (NVSDK_NGX_CUDA_* exports) might care about torch's earlier CUDA init.
- GPU match: cuDeviceGetLuid (nvcuda) to map CUDA ordinal -> DXGI adapter. Surface pool: 8-capacity acquire/release with backpressure retry loop. Session = rtxv_session_create/release; frame = rtxv_process_frame_v1(desc,desc,result,err).
- DECISIVE CHEAP PROBE SHIPPED: tools/list_imports.py + list_imports.bat (drag-drop any dll, static parse, NEVER executes the target): flags TERMINATION APIs (the trap set) + NVSDK_NGX_* bindings (D3D12 vs CUDA backend split) + nvcuda cu* usage; verdict line for no-termination-imports. Owner targets: (1) his ENGINE dll (the 571 KB dlss5nr bridge from run 21) -> shows D3D12 vs CUDA backend + termination imports; (2) STOCK nvngx_dlssnr.dll (DLSS Swapper) -> the never-run discriminator, now ZERO-RISK static; (3) any other dll in his chain (caller, VSR/HDR runtimes).
- OPEN EXECUTION EXPERIMENT (unchanged): run 27 = ANTS_NR_RUNTIME_CALLBACKS=0 (his python registers no callbacks). Outcomes: works => registration poison; TERMINATION line => resolve offsets; silent => int29/fastfail class => ship int29->int3 patch as run 28.
- Fixture lesson (2nd): PE import-name RVAs point at a 2-byte hint THEN the name; forgetting the hint truncates names by exactly 2 chars (tool was right, fixture wrong - verify against the fixture bytes before blaming the parser).

### 2026-09-20 — MERSERK SOURCE ACCESS (owner-authorized): RESEARCH/ created (license-local), full architecture + killer-class analysis; ntdll detour STILL untested on rig
- Owner pointed at github.com/Merserk/dlss5-visual-enhancer + authorized grabbing files into a RESEARCH dir. License = SOURCE-AVAILABLE PROPRIETARY (inspect + private modify OK; redistribution NOT). => RESEARCH/merserk_ve10/ is GITIGNORED (local working tree only); committed = RESEARCH/README.md (provenance) + RESEARCH/NOTES.md (our analysis, own words). Do not push his files.
- REPO HAS NO BRIDGE C++ (bin/ gitignored; dlls ship as release binaries). Python layer fully mapped: python NEVER touches NGX; snippet validated-as-file only; 3 compiled bridges own everything (watchdog+poison doctrine); NGX_RUNTIME_LOCK process RLock; never Shutdown/unload; DLSSG-first priming; FFmpeg CUDA primary ctx BEFORE NGX loads. NR styles Default=0/Natural=1/Cinematic=2.
- OWNER'S IMPORT LISTINGS (list_imports.bat rig-verified): NO NVSDK_NGX_* imports in ANY of his 5 dlls => engine(92)/caller(69) bind NGX DYNAMICALLY (LoadLibrary+GetProcAddress; kernel32-dominated tables). Termination imports: ExitProcess+TerminateProcess in engine/snippet/caller/dlssg; frame-interp engine = CRT terminate only (17 dlls). NEXT: list_imports --all (bat now always --all) on the ENGINE => see the other 4-5 imported dlls (d3d12? nvcuda? nvngx?) => backend question statically narrowed.
- KILLER-CLASS CONSEQUENCE: dynamic GetProcAddress termination calls DODGE IAT patches by construction (run-26 traps verified armed, death dodged them anyway) => the ntdll!NtTerminateProcess detour (shipped AFTER owner's last fatal run) is THE net for every dynamic/static terminate path - IT HAS NEVER BEEN ON THE RIG. Still invisible: static CRT abort->__fastfail(int29) + raw syscalls => if run 27 dies with NO TERMINATION line, ship int29->int3 patch (VEH names abort site) = run 28.
- CUDA HYPOTHESIS (run 28 candidate): his NR bridge self-describes as "D3D12/NGX CUDA bridge"; pipeline CUDA-first; snippet exports full NVSDK_NGX_CUDA_* family => he likely evaluates feature 18 via the snippet's CUDA backend with raw device pointers (no D3D12 choreography). Pure-python implementable (cuInit/primary ctx/cuMemAlloc/cuMemcpy + NVSDK_NGX_CUDA_Init/CreateFeature/EvaluateFeature on our proven direct-load path). If run 27 still dies => run 28 = CUDA-backend bind.
- RUN 27 (unchanged, NOW double-loaded): CALLBACKS=0 (his python registers none) + ntdll detour armed. Outcomes: works => registration poison; TERMINATION line => resolve; silent => fastfail class => int29 patch.

### 2026-09-20 — --all listings decoded: snippet ships VERSION+ADVAPI32+USER32 gate plumbing; engine = own-device standalone host (own DXGI enum + D3D12CreateDevice + D3DCompile; CRT static; CUDA dynamic)
- Engine (NR): d3d12:1 + dxgi:1 + ole32:1 + D3DCOMPILER_47:1 + KERNEL32:88. NO nvcuda static (CUDA = dynamic), NO CRT dlls (static CRT). His host creates its OWN device like ours - device ownership parity. D3DCompile = engine-side shaders (compositor/convert).
- Snippet (dlssnr) AND dlssg: ADVAPI32:3 (registry reads) + VERSION:3 (GetFileVersionInfo family = DRIVER VERSION GATE by metadata - machinery behind our earlier '0 gate strings' finding: checks via APIs/values, no strings) + USER32:1 (unknown fn: MessageBoxW candidate = BLOCKING POPUP failure mode candidate - ask name-level listing) + KERNEL32:120/119.
- Frame-interp engine: the only dynamic-CRT binary (MSVC /MD: MSVCP140+VCRUNTIME140+api-ms-crt); ADVAPI32:3 + USER32:1 + d3d12:1 + dxgi:1 + D3DCOMPILER_47:1; termination import = CRT terminate only.
- Caller: kernel32-only 69 CONFIRMED again.
- TOOL FIX: list_imports --all now prints EVERY function name per dll (was: dll counts + flagged only - the first --all run was still name-blind for unflagged dlls). Re-drag dlls -> identify snippet's USER32/VERSION:3/ADVAPI32:3 exact names + engine's d3d12/dxgi fns. Zero risk file reads.
- PRIORITY UNCHANGED: run 27 (CALLBACKS=0 + first ntdll-detour outing). Then name listings; then CUDA-backend bind (run 28) if death persists.

### 2026-09-20 — name-level imports: SHIM VERSION-GATE HYPOTHESIS (best-fit mechanism); snippet console-log framework; NVSDK_NGX_LOG_LEVEL knob
- CORRECTIONS: engine has NO static D3D12CreateDevice (d3d12 import = D3D12SerializeRootSignature; device created dynamically - Agility pattern; dxgi = CreateDXGIFactory1 static; D3DCompile = own shaders; full crash battery: SetUnhandledExceptionFilter/RtlPcToFileHeader/IsDebuggerPresent). USER32 = GetWindowThreadProcessId (NOT MessageBox) - with AllocConsole/GetConsoleWindow/SetConsoleTitleA/WriteConsoleA = NVIDIA self-console diagnostics framework (shared by snippet+dlssg+FI engine).
- Snippet framework names: GetFileVersionInfoA/VerQueryValueA/GetFileVersionInfoSizeA (file-version queries), RegOpenKeyExW/RegQueryValueExW/RegCloseKey, VerSetConditionMask+VerifyVersionInfoW, GetSystemDirectoryW+LoadLibraryW/ExW+GetProcAddress+GetModuleHandleA(+ExA), FindResourceA/LoadResource/LockResource/SizeofResource (embedded resources - snippet ONLY, dlssg lacks), SetEnvironmentVariableW, NO CreateThread (single-threaded runtime!), IsDebuggerPresent+CRT.
- **SHIM VERSION-GATE HYPOTHESIS**: NGX runtimes find the core via GetModuleHandle("nvngx.dll") + validate via file-version queries. HIS process: no module named nvngx.dll (core file = _nvngx.dll) -> probe falls through to System32 real nvngx.dll (has VERSION resource) -> OK. OUR process: name nvngx.dll DELIBERATELY occupied by our hand-built shim (no VERSION resource) -> GetFileVersionInfoSizeA=0 / gate fails -> deliberate fastfail kill. Fits EVERYTHING: deliberate, exceptionless, post-callback evaluate death, works in his host, dodges IAT traps. REFS: run-20 note - snippet loads core DIRECTLY, no nvngx.dll thunk on his path.
- CONFIRMATION PATH: (1) NVSDK_NGX_LOG_LEVEL=1 (then 4) env line - snippet log framework may print gate decisions; check staged appdata/logs + %LOCALAPPDATA%\NVIDIA\NGX after run; (2) E1 use_shim=False DIRECT BIND (long-open) = decisive: no shim module in process = his host's exact situation; (3) if confirmed: ship VS_VERSIONINFO resource inside the shim builder (version-indistinguishable shim = permanent cure keeping the shim). DO NOT touch shim before A/B - it's rig-proven; muddying the experiment.
- RUN 27 final protocol: launch bat env: ANTS_NR_RUNTIME_CALLBACKS=0 (RET/DUMP stay) + set "NVSDK_NGX_LOG_LEVEL=1"; scoop; run once; after death ALSO check native-crash.log + NGX log dirs. Outcomes: works => callbacks poison (or log level side-effect - rerun with LOG_LEVEL=0+CALLBACKS=1 to separate); TERMINATION line => resolve offsets; silent => fastfail class AND gate hypothesis still standing => E1 next (shim removed) which is both diagnosis AND cure candidate.

### 2026-09-20 — RUN 27 = net-less (detour SKIPPED on variant stub) + env drift; detour relaxed to modern stub shapes; protocol made drift-proof
- Run 27 rig: new build confirmed (trap lines + detour line present). BUT (a) ntdll detour SKIPPED: stub starts standard (4C8BD1 B82C000000 = mov r10,rcx; mov eax,0x2C = NtTerminateProcess SSN) but no 0F05C3 within my old 20-byte window - Win11 builds grow check bytes between mov-eax and syscall; my parser was too strict => THE NET WAS NOT ARMED. (b) Env drift: callbacks STILL registered+fired (=1 survived the bat edits) => the callbacks A/B is STILL unrun; DUMP lines absent (dump env apparently gone). Console died right after 'callback fired'; crash file NOT updated (no TERMINATION, no exception) => death again silent, traps again dodged.
- KEY REALIZATION REFINED: IAT traps cover only STATIC thunks of the two NGX modules. The snippet imports LoadLibraryW/GetProcAddress => a DYNAMIC GetProcAddress("TerminateProcess"/"ExitProcess") kill bypasses module IATs entirely => only the ntdll detour catches it. Run 27 therefore tested nothing new - the detour MUST arm.
- FIXED crashlog detour: steal = verify 4C8BD1+B8 prefix, find 0F05 within 8..24, stolen = raw[:syscall+2] + C3 (append ret ourselves - kernel returns into our trampoline; rsp/flag-relative check bytes copy verbatim); skip now logs FULL 32 stub bytes for the obfuscated case. Pin updated.
- PROTOCOL DRIFT-PROOFED BY DELETION: owner removes the three ANTS_NR_* set lines from the launch bat entirely => code defaults define the experiment (no callbacks, no dump; traps + detour arm automatically). KEEP/ADD NVSDK_NGX_LOG_LEVEL=1 (snippet-side knob). Watch for a SECOND console window during the run (snippet AllocConsole/WriteConsoleA framework - its buffer dies with the process). Check after: console, native-crash.log, %LOCALAPPDATA%\NVIDIA\NGX.
- RUN 27b expected: 'ntdll detour ARMED on NtTerminateProcess (stolen N-byte...)' or SKIPPED-with-32-bytes (paste bytes = full stub characterization, handle obfuscated next). Outcomes: works => callbacks poison; TERMINATION line => resolve offsets; silent WITH detour armed => terminate paths fully exonerated => int29/fastfail class => E1 use_shim=False gate test next, int29->int3 patch after.

### 2026-09-20 — RUN 27b: CALLBACKS EXONERATED BOTH WAYS; my nesting bug stripped all nets (fixed + pinned); NGX log home corrected to C:\ProgramData\NVIDIA\NGX
- Run 27b rig (env lines deleted per protocol): NO callback registration/firing => the death STILL occurred => callbacks are exonerated as a variable (with=death 22-26, without=death 27b). Closed. His-host-parity argument for callbacks gone.
- MY BUG: the trap+detour install was NESTED inside the ANTS_NR_RUNTIME_CALLBACKS block => env deletion stripped the ENTIRE instrumentation (no trap lines, no detour line in console = the tell) => run went out net-less (runs-14-19 configuration again). FIXED: install_termination_trap + install_ntdll_terminate_detour now arm ALWAYS at NR session init (only ANTS_NR_TERMINATION_TRAP=0 opts out); structural test pin: install call must appear BEFORE the callbacks env check in ngx.py (docstring mention taught me to anchor the pin on the code occurrence).
- NGX LOG HOME CORRECTION (owner): %LOCALAPPDATA%\NVIDIA\NGX does NOT exist on his system; NGX lives at C:\ProgramData\NVIDIA\NGX (models dir with per-model dlls, no logs currently). After 27c check there for Logs/new files (NVSDK_NGX_LOG_LEVEL=1 kept in bat).
- RUN 27c: scoop, same bat (NO ANTS_ lines; NVSDK_NGX_LOG_LEVEL=1), run once. Console MUST show trap lines + 'ntdll detour ARMED' BEFORE 'NGX init ->' (27b proved their absence is the failure signature). Then: works => unexpected (14-19 say no) / TERMINATION line => resolve offsets / silent WITH nets armed => terminate paths exonerated => fastfail/int29 class => RUN 28 = E1 use_shim=False (no shim = his host geometry; VERSION-GATE test) + int29->int3 patch queued.

### 2026-09-20 — RUN 27c TERMINAL VERDICT: kill is KERNEL-DIRECT (__fastfail/int 29h class); every user-mode net armed and dodged; two gates armed for run 28
- Run 27c rig: FULL nets ('termination trap on snippet/driver core: patched TerminateProcess, ExitProcess' + audit + 'ntdll detour ARMED ... stolen 21-byte syscall stub') -> silent death at evaluate, NO TERMINATION line. MATRIX CLOSED: static IAT terminates (patched+verified) EXONERATED; dynamic GetProcAddress terminates (ntdll detour beneath them) EXONERATED; CRT exit-family imports (none in NGX modules) EXONERATED. Remaining classes: __fastfail (int 29h, CD 29 - kernel-direct, bypasses VEH/SEH/ntdll BY DESIGN; CRT abort in static CRT compiles to it) or raw syscall. DELIBERATE anti-host check, fired at first evaluate after the params callback.
- GATES ARMED (both default-on for run 28, ONE run carries both): (1) crashlog.install_int29_trap - CD 29 -> CC 90 (int3+nop) in EXECUTABLE sections of snippet+core (chars & 0x20000000, 4 MB chunk scan, VirtualProtect per 2-byte site); STATUS_BREAKPOINT 0x80000003 added to _INTERESTING so the VEH verdict NAMES the fast-fail module+offset (-> resolve_crash_offset). Opt-out ANTS_NR_INT29_TRAP=0. (2) ngx ANTS_NR_USE_SHIM=0 - E1 direct bind, NO shim module in process = Merserk host geometry (no nvngx.dll-named module -> snippet's module/version probes fall through to System32 real nvngx.dll WITH VERSION resource). Tests the VERSION-GATE-vs-shim hypothesis (RESEARCH/NOTES.md 3b).
- RUN 28 = single run: env line set "ANTS_NR_USE_SHIM=0" (+ keep NVSDK_NGX_LOG_LEVEL=1). Console must show trap lines + 'ntdll detour ARMED' + 'int29 trap: N fast-fail site(s) converted'. Outcomes: (a) WORKS => shim-VERSION-gate CONFIRMED => cure = add VS_VERSIONINFO resource to shim builder (Mingw32-windres or hand-built resource section), keep shim, ship; (b) dies + 'NATIVE CRASH: exception 0x80000003 (breakpoint (patched fast-fail site)) at MODULE+0xOFF' => resolve offset => the gate's address identified => decide: binary-diff vs stock nvngx_dlssnr (DLSS Swapper discriminator, still never run, zero-risk static via list_imports) or conclude hard anti-tamper; (c) dies + breakpoint at unexpected site => paste + judge. Fallback instruments untouched: ANTS_NR_RUNTIME_CALLBACKS (dead end, exonerated), ntdll detour (permanent), int29 trap (permanent).

### 2026-09-20 — RUN 28+ HOST LAYOUT IMPLEMENTED (owner-independent): the ecosystem's working hosts were decoded and our NR host rebuilt onto their contract
- TRIGGER: rather than idle on run 28, the working hosts of this exact runtime became readable offline (`gh api` on `kos94ok/ComfyUI-DLSS5-NR-Linux` + `ganarajpr/ComfyUI-DLSS5-Video`, MIT; credited) - INCLUDING `native/caller_shim.cpp` (the source of the 91 KB `nvngx.dll_comfy.dll` we had only as a binary) and `docs/ARCHITECTURE.md`. Evidence beats guessing: their contract is now our contract.
- CALLER-CHECK TRUTH (documented in their source): "The NR runtime validates the module that owns its RETURN ADDRESS. A trivial wrapper built with /O2 can be tail-call-optimized into a JMP, which would leave the return address in dlss5nr_bridge.dll and trigger 0xBAD00002." => the helper module is MANDATORY (never call the snippet from the loading module), and their helper is deliberately named `nvngx.dll_comfy.dll` (pefile: 5 exports `DLSSNR_CallInit/Create/Evaluate/Release/Shutdown`, KERNEL32+msvcrt only, **no resources = no VERSION**, 42 .pdata = real MinGW build of exactly that source). The bare `nvngx.dll` name survives in their loader ONLY as a "backward-compatible fallback for older builds" => naming a helper `nvngx.dll` is a hazard we can now drop (hijacks a real NVIDIA module name in-process).
- SNIPPET Init_Ext ABI (their source, verbatim): snippet = `(app, path, device, FeatureCommonInfo*, sdkVersion)` while the exported helper keeps the bridge-facing `(version, common_info)` order and reorders internally => CONFIRMS our `fwd_init_ext` swap thunk (it was right; now it is also documented). Both hosts also call the snippet init THROUGH the helper, after the core init, and register `NvAPI_Initialize` BEFORE the core init.
- SESSION + PARAMS: `Init_ProjectID(project, 0, engine_version, runtime_dir, device, ver, fci)` is the default core route; feature 18 consumes **the core's capability map** - "A fresh AllocateParameters map can still let CreateFeature succeed but then returns InvalidParameter at Evaluate". Slot map pinned by both hosts: resource 0, pointer/callback 2, int/uint 3, **float 6** ("Calling slot 1 silently invokes a different overload and leaves every float parameter at an invalid/default value, which the DLSS carrier reports as 0xBAD00005"). Their `ScalingRatioCallback` returns 0xBAD00005 on a NULL map and writes `DLSSNR.ScalingRatio = 1.0`.
- QUALITY/RATIO (new, fixes a real deviation we introduced in this rewrite): `PerfQualityValue` = the REQUEST's mode. 1x/native = 5 (DLAA, `FixedScalingRatio(5) => 1.0f`); **6 = the neural POST-PASS value, only correct on top of an ordinary DLSS carrier** (`neural_quality = upscale_active ? NR_POSTPASS_PERF_QUALITY : perf_quality`). We had shipped 6 for a 1:1 node = a ratio/network-shape mismatch against the 1.0 our callback reports. Now 5 by default, `ANTS_NR_PERF_QUALITY` overrides (`none` = never write it, which is what the still-image host does).
- SURFACES: both hosts re-write the FULL parameter set (resources + all four subrects) before EVERY evaluate; colour is `R16G16B16A16_FLOAT` both ways; MVec is `R16G16_FLOAT` (NULL + zero subrect in the still-image host = "no motion vectors" is legal!), depth is optional (video host: zeroed `R32_FLOAT`, `DepthInverted=1`); create-enqueued work must be submitted+waited before the first evaluate ("evaluating on the still-unsubmitted list makes the 310.8 snippet report the otherwise opaque 0xBAD00005") - our `create_feature` already flushes.
- CODE SHIPPED (working tree, all committed together): `shim.py` = 4 unwindable thunks incl. `fwd_init_ext` (mov r11,r9 / prolog / reload caller arg5 into r9 / park version in the inner slot 0 / real call - bytes pinned by test) + `DEFAULT_SHIM_NAME = "nvngx.dll_ants.dll"` + `ANTS_NR_SHIM_NAME` + `write_shim(dll_name)`; `parameters.py` = proven slot map (+`ANTS_NR_PARAM_ABI=header` for the public-header mapping), `OwnParameterObject` snippet-direct object with `backend`, `CoreParameterObject` with `backend` = `c-api`|`vtable` over NVIDIA's flat `NVSDK_NGX_Parameter_Set*` exports, callbacks stored as RAW ints (a ctypes callback in a c_void_p field raised TypeError); `ngx.py` = `NgxSession(gpu, module_path, ..., feature_module_path=..., project_id=..., ngx_log=...)` with core = session owner and the canonically-staged snippet as feature provider, `Init_ProjectID`-first owner init, `_init_snippet_module` (shim => public order through `thunk="init_ext"`; no shim => snippet order direct), `_open_core_parameters` (capability map first), `_bind_feature_lifecycle`, `_preload_nvapi`, always-armed `_instrumented` trap list, feature-module disposal in `close()`; `nr.py` = full create contract (Width/Height, Input/Output sizes, `Output.Width/Height`, PerfQuality, node masks, Enabled/Upscaling/UICorrection/DepthInverted/UseAutoMask, Hint.Render.Preset, Style, ScalingRatio/Scale/MVecScaleX/Y/GlobalToneStrength, Color/MVec/Depth/Output/Backbuffer + 16 subrect fields, callback), `_write_surfaces()` re-applied per evaluate, `NR_PERF_QUALITY_1X = 5`, RGBA8<->RGBA16F via numpy; `discovery.py` (both) = canonical-name staging (any-named build -> `nvngx_dlssnr.dll` in a content-addressed stage, canonical sets used in place); `d3d12.py` = `DXGI_FORMAT_R16G16B16A16_FLOAT = 10` + BPP.
- TESTS: `test_native_flow` now drives the WHOLE new route against the fake COM graph (real `stage_nr_runtime` from a temp source, `Init_ProjectID` -> snippet `Init_Ext` -> `GetCapabilityParameters` -> `CreateFeature(18)` -> per-frame evaluate, flat C-API backend observed, RGBA16F round-trip compared against numpy quantization, reset toggle, fence-wait path, legacy own-object route) - 25 checks; `test_dlsssr` +7 pins (swap thunk bytes, 4 RUNTIME_FUNCTIONs/one shared UNWIND_INFO with the `(2<<4)` fix, shim-name default + env override + export-dir name follows the file name, 1x quality contract, per-frame re-application, NvAPI pre-step, single instrumentation list).
- ENV KNOBS (full set now): `ANTS_NR_USE_SHIM` (E1), `ANTS_NR_SHIM_NAME`, `ANTS_NR_PERF_QUALITY`, `ANTS_NR_PARAM_ABI`, `ANTS_NR_USE_OWN_PARAMS`, `ANTS_NR_NVAPI`, `ANTS_NR_NGX_LOG`, `ANTS_NR_TERMINATION_TRAP`, `ANTS_NR_INT29_TRAP` (+ the exonerated callback knobs).
- NOT VERIFIED: nothing here has executed against the real runtime - it is contract parity with the two hosts that do work, plus a fake-COM harness. Run 28 stays the decisive experiment; run 29 candidates (shim name, `PerfQuality=none`, own-params) are recorded in CONTINUATION.md.

### 2026-09-20 — RUN 28 ATTEMPT #1: OUR OWN int29 SCANNER KILLED THE RUN (fixed + pinned); the staging step also loaded the WRONG FILE (fixed)
- RIG RESULT: the run died in `ants/dlsssr/ngx.py:424` -> `crashlog.install_int29_trap` -> `crashlog.py:421 u32` reading a SECTION HEADER: faulthandler printed the full all-thread dump, the process terminated with status 0xC0000005, and our ntdll detour logged `TERMINATION via ntdll!NtTerminateProcess(status=0xC0000005)`. NO `NGX init ->` line: the E1 shim gate never ran. **Nothing about the runtime was tested** - the run is VOID for the investigation (it did confirm the new instrumentation lines work: trap lines on session owner + snippet, the static audit line, `ntdll detour ARMED (stolen 21-byte syscall stub)`).
- ROOT CAUSE (mine): the scanner walked the PE section table with RAW dereferences (`c_uint32.from_address(base+off)`) and trusted `n_sec`/`sec0`. **`try/except` does NOT protect a raw dereference** - an AV in a ctypes scalar getter or `string_at` is a HARD Windows exception, so the "safe" wrapper around `string_at` protected nothing, and one bad section-table entry took the whole ComfyUI process with it. FIX: new `crashlog._Mem` guard - every read goes through VirtualQuery and only touches committed, non-guard, readable pages, region by region; `_scan_int29_sites` validates MZ/PE/e_lfanew/`n_sec` (1..96)/optional-header size/section-table-inside-mapping before walking, and skips with a REASON; `_rewrite_site` re-verifies the 2 bytes before patching; each module runs inside try/except so a diagnostic can never kill a run. Same guard now also covers `_patch_iat` (headers, descriptors, thunk names - its `cstr` "protection" was the same illusion) and the ntdll stub read.
- PINNED BY TESTS (they would have caught it): synthetic-PE fixture converts only EXECUTABLE-section `CD 29` sites (data-section site untouched, emit text asserted), plus a HOSTILE fixture set - base VirtualQuery reports unmapped, `n_sec = 0xFFFF`, section table running past the last mapped page, executable section pointing at nothing - all four must skip with a logged reason and zero exceptions.
- SECOND BUG, SAME RUN (would have confounded any result): the log shows `NR runtime staged under its canonical name: ...\models\DLSS\NR\nvngx_dlssnr.dll` - i.e. the folder ALSO holds a file literally named `nvngx_dlssnr.dll` (a different build than the RenoDX one selected in the node), and my staging rule ("folder carries a canonical name -> use it in place") silently made THAT the runtime. Runs 14-27c had loaded the selected file directly, so run 28's geometry also differed from every previous run. FIX: only the CHOSEN file is ever canonicalized (already-canonical selection is used in place; anything else is staged as a copy into `models/DLSS/staged/<name>-<size>/`), a sibling canonical file is called out with a warning, `nr.py` logs the exact runtime in use (path + size) and refuses to load when the canonical file is not a copy of the selection (size check). Pinned by a discovery test (selected build wins over the sibling; canonical selection stays in place).
- DIAGNOSTIC-I/O AUDIT (same class of bug swept everywhere): `_write_iat_ptr` now verifies the IAT slot through the guard before making it writable; `parameters._read_cstring` (the parameter NAMES the runtime hands our own parameter object) is bounded + guarded and returns "" for an unreadable pointer instead of faulting, with a `_Mem.read_some` helper for unknown-length strings; the ntdll stub read is guarded; the page-protection filter also refuses EXECUTE-only pages (PAGE_EXECUTE is not readable).
- Console hygiene: the caller-shim line now prints once per process instead of once per shim-routed module, and the int29 line announces `scanning N module(s)` before results so an ARMED-but-empty trap is visible.
- SECOND FAULT SIGNATURE (owner's full stream, same void run): the repeating dumps name `ctypes\__init__.py:546 string_at` <- `crashlog.py:446 install_int29_trap` - the OLD file's SECTION BYTE SCAN, i.e. the crash was reading a section whose VirtualSize claims more than the mapped pages (discardable/uncommitted tail). Both old crash sites (header read at `u32`, section bytes at `string_at`) are the same defect class, and the stream repeated forever because whatever handles the AV re-executes the instruction. Line numbers also prove the OLD build was deployed during that attempt - hence the new `HOST_BUILD` console marker (`[ANTs] NR/SR host build 2026-09-20.3 ...`) so deployment is verifiable at a glance. `_patch_section` now reads through `read_some` (exactly the guarded bytes), so readable data is never skipped and unmapped pages are never touched - pinned by a partial-mapping fixture (mapped site converts, unmapped site refused + reported).
- RUN 28 STATUS: still the decisive gate, now scheduled as a RE-RUN (previous attempt void). The console must show `int29 trap: scanning 2 module(s) ...` + either `N fast-fail site(s) converted` or `no fast-fail site (...)`, then `NGX init ->`, before the first evaluate.

### 2026-09-20 — RUN 29 (first guarded build): the NGX LOG FINALLY SPEAKS - the core rejects the community snippet as a feature provider (0xBAD00000); our own D3D12 staging bug was the node failure
- RIG RESULT (07:06): deployment verified (`NR/SR host build 2026-09-20.3`), staging verified (the SELECTED RenoDX build staged, sibling named in the warning, 165,830,144 bytes), traps + detour + int29 armed (`10 fast-fail sites in session owner _nvngx.dll`, `6 in snippet nvngx_dlssnr.dll`), `Init_ProjectID <- hr=1`, `GetCapabilityParameters <- hr=1`, `snippet Init_Ext <- hr=1`. NO kill this time - the node failed with **our own D3D12 error**: `ID3D12GraphicsCommandList.Close failed: HRESULT 0x80070057` (E_INVALIDARG) in `nr.py`'s first `submit_and_wait`.
- **THE NGX LOG IS THE NEW BLACK BOX** (it survives death and lands in OUR tree): `models/DLSS/staged/ANTs/appdata/logs/nvngx.log` (the core logs "Logging to requested file ... enabled successfully"). Everything below is quoted from it.
- **CORE REJECTS THE COMMUNITY SNIPPET AS ITS OWN FEATURE PROVIDER** - the version-gate hypothesis CONFIRMED, but on the SNIPPET and in the CORE's loader, not on our shim:
  `NGXSecureLoadFeature -> SnippetLocationInfo::load` for every known snippet, then for ours:
  `nvLoadSignedLibraryW() failed on snippet '...nvngx_dlssnr.dll' missing or corrupted - last error Cannot find the requested object.` →
  `NGXLoadMetaDataViaGetFileVersionInfo: swscanf_s() failed on snippet ...` →
  `unable to load DLL metadata via FileVersionInfo for snippet` →
  `NGXLoadFromPath failed for <dir>: 0xBAD00000` → `ModuleName - nvngx_dlssnr.dll doesn't exist in any of the search paths!`
  ⇒ On a real Windows driver the core requires a SIGNED snippet with parseable FileVersionInfo. A renamed community build can never pass this. Consequence: the core-owned FEATURE path is closed for these builds; the snippet must be hosted by us (which our layout already does - the core only owns the session + capability params). Nothing to fix, but the log noise is expected and this is why the core can never provide feature 18 for a community build.
- **UNSIGNED + VERSIONLESS CALLER HELPERS ARE PROVEN-WORKING** (offline pefile on the community helper the working hosts ship, `nvngx.dll_comfy.dll`): security directory = 0 (NO Authenticode), zero `VS_VERSION_INFO`/`FileVersion` strings, no resources at all - and it drives feature 18 successfully in their hosts. ⇒ the caller-check is NOT a signature/version gate on the helper; the "add a VERSION resource to our shim" cure is DEAD, and our own unsigned/versionless shim is exactly the geometry that works. (E1 `ANTS_NR_USE_SHIM=0` remains the test for "does the shim's PRESENCE matter", still unrun: this run routed the snippet through the shim, so E1 was not active.)
- **NODE FAILURE = OUR BUG, DOCUMENTED RULE VIOLATION**: `GpuContext.upload_texture` recorded barriers on the UPLOAD-heap staging buffer (`COMMON -> COPY_SOURCE`, then `-> COMMON`). D3D12 keeps staging heaps in a FIXED implicit state (upload = GENERIC_READ, readback = COPY_DEST) and COMMON is not even a legal state for them - the invalid command poisons the command list, and the next `Close()` answers `0x80070057`. This could never surface before: every earlier run's first Close happened on a list with no upload recorded (CreateFeature -> flush), and the process died at evaluate before the first upload's own Close. FIXES: `transition()` refuses (with a one-time loud warning) any transition of an UPLOAD/READBACK resource; `upload_texture` no longer transitions the staging buffer; resources carry `heap_type`; the guide zero-fill uploads are GONE (D3D12 guarantees a committed resource reads as zeros, so the whole first submit disappeared - nothing to go wrong before the feature exists).
- SECOND FIX (same class of silent-wrongness risk): `submit_and_wait` now treats ANY Close failure as "the recording is void" - drops it, resets allocator+list, and says so LOUDLY (GPU work may be lost; `ANTS_D3D12_STRICT_CLOSE=1` turns it into a hard error for A/Bs). The old code only handled 0x80004005, so E_INVALIDARG killed the node.
- THIRD FIX (instrument): the NGX log CALLBACK printed `<unreadable>` for every line because ctypes hands a CFUNCTYPE an int (not bytes) for `const char*`. It now decodes through the VirtualQuery guard (bounded) - the console shows the real core lines, and the callback cannot fault inside the runtime's logging path.
- FOURTH FIX (caller geometry, reference-host parity): the rig log showed `NGXInitContext: called from module nvngx.dll_ants.dll` - the CORE was being called through our helper, a geometry no working host exhibits (their bridges call the core directly and route ONLY the snippet through the helper). The session OWNER is now bound directly; only a snippet provider goes through the shim (`ANTS_NR_CORE_VIA_SHIM=1` restores the old geometry). Console prints which binding was used.
- ALSO CONFIRMED BY THE LOG: our ProjectID route works (`MapProjectId: Found cms id 876232c for engine: custom engineVersion ANTs 1.1.0 projectID 53f803cc-a12f-4d69-90d5-19b7599cad19`); the core found the adapter through NVAPI itself (so our best-effort NvAPI pre-step is Wine-only; the message now says "harmless on Windows"); the parameter backend is the vtable map (resource=0 pointer=2 int=3 float=6) - the core exports no flat C API on this driver.
- TESTS (all green): the fake COM graph now MODELS the D3D12 rules - staging heaps reject a non-canonical initial state with E_INVALIDARG, and a barrier referencing a staging resource poisons the list so the next Close returns E_INVALIDARG (the exact rig failure). New pins: staging buffers are created in their implicit state and tagged; a transition of a staging resource is refused and never recorded; the NR session records no copies/barriers at init; an unconclosable list is dropped + reset (and strict mode re-raises); the session owner is NOT routed through the shim while the snippet IS; the log callback decodes the message pointer. `test_native_flow` 31, `test_dlsssr` 72.
- NEXT RUN (no env lines needed, or keep `NVSDK_NGX_LOG_LEVEL=1`): expected sequence - build marker -> runtime in use -> int29 scan -> `NGX init -> _nvngx.dll (bound directly)` -> Init_ProjectID -> capability params -> snippet Init_Ext -> CreateFeature -> evaluate. If it dies at evaluate, send BOTH the console and `models/DLSS/staged/ANTs/appdata/logs/nvngx.log` (the core/snippet log is the first instrument that survives the kill), plus the crash file if the int29 trap names a breakpoint site.

### 2026-09-20 (later) — rig evidence collector + the staging-lifetime bug
- NEW OWNER TOOL `tools/collect_rig_evidence.bat` (+ `.py`, tested against a
  synthetic rig tree): one double-click produces ONE folder to send - the
  deployed build marker + hashes of every `ngx.py` copy, the models\DLSS
  inventory with sizes/hashes, COPIES of the NGX log + crash files, git HEAD,
  ANTS_/NVSDK_ env, GPU and torch state - and puts the report on the
  clipboard. READ-ONLY: it never writes outside `tools\rig_evidence\`
  (gitignored). Three runs in a row needed a follow-up "and also send ...",
  so this ends that round trip.
- BUG FIXED while auditing the same hot path: `upload_texture` released the
  staging buffer IMMEDIATELY after recording the copy, i.e. before the command
  list was even executed - undefined behaviour in D3D12 (the debug layer calls
  it "resource destroyed while still referenced by a command list"; the copy
  can then read whatever the allocator hands the next resource). Staging
  buffers now live on `GpuContext._pending_release` and are freed on the next
  `submit_and_wait()` (or when a recording is dropped, or at close). Pinned in
  `test_native_flow` with a spy on `submit_and_wait`.

### 2026-09-20 — RUN 30: EvaluateFeature REACHED, and the kill is gone - the snippet now throws a CATCHABLE MSVC C++ exception
- **RIG RESULT (07:26): the whole host contract ran end to end for the first
  time** - build marker, `NGX init -> _nvngx.dll (bound directly)`, traps +
  ntdll detour + int29 (10 sites session owner / 6 snippet), `Init_ProjectID
  <- hr=1`, `GetCapabilityParameters <- hr=1`, `snippet Init_Ext via caller
  shim <- hr=1`, **`NGX CreateFeature(feature 18) <- hr=1`**,
  **`NGX EvaluateFeature ->`** - i.e. the parameter contract (90 params),
  the feature creation and the first evaluate ALL worked. Run 29 died before
  any of that, on our own D3D12 staging barrier.
- **The failure mode changed: no silent kill, a real exception.** The snippet
  raised an MSVC C++ exception (`0xE06D7363`, i.e. `_CxxThrowException`) out
  of `EvaluateFeature`; ctypes converted it to
  `OSError: [WinError -529697949] Windows Error 0xe06d7363` and ComfyUI
  reported a clean node error (prompt finished, 12.14 s). That is the first
  CATCHABLE verdict in this investigation - and it is consistent with the
  earlier "silent kill" being the same throw unwinding into a host that had
  no handler (the old geometry ran the snippet as the session owner).
- **NEW INSTRUMENT: the first-chance handler now decodes C++ throws**
  (`crashlog._report_cxx`): exception record -> magic 0x19930520 + thrown
  object + ThrowInfo -> CatchableTypeArray -> CatchableType -> TypeDescriptor
  -> RTTI type name, plus a best-effort `what()` guess from the object's
  second qword, plus the live (filtered) stack in module+offset form. Every
  hop goes through the VirtualQuery guard; a hostile chain is refused, never
  faulted (tested with a synthetic record and two hostile ones:
  `type .?AVinvalid_argument@std@@ message guess 'DLSSNR: bad parameter:
  MVec'`). `ANTS_NR_CXX_TRAP=0` opts out.
- **The failure is now LOUD**: `nr.py` wraps `ngx.evaluate()` and raises a
  `[ANTs]` error that names the C++ type, the throw site and the black-box
  file; the node then DROPS the native session (`_close_native`) so a
  half-dead NGX feature is never evaluated into again.
- **Caller geometry fix confirmed in the core's log**:
  `NGXInitContext: called from module libffi-8.dll` - the core is now called
  straight from the process (before it saw `nvngx.dll_ants.dll`). Also
  visible: `NvAPI_DRS_FindApplicationByName -166` (python.exe is not a
  registered app - harmless, init continued), the core enumerating every
  known snippet (`nvngx_dldenoiser`, `nvngx_dlssd`, `nvngx_fpgx`, ... - the
  full list of features it tries), and the same documented
  `nvLoadSignedLibraryW`/FileVersionInfo refusal for our staged community
  build (expected: we host the snippet, the core only owns the session).
- **Console de-flooded**: the NGX log callback echoed ~300 lines into the
  node log (each line twice: core file log + console). Echo is OFF by
  default now (`ANTS_NR_NGX_ECHO=1` restores it) - the same text is in
  `nvngx.log`, which is what we ask for after a failure anyway.
- **Collector fixed for the owner's real environment**: his first attempt ran
  it from `C:\\tools\\` and the argument `--repo "C:\\"` was mangled by cmd
  (a trailing backslash before the closing quote escapes it) - the script
  now auto-detects the pack, the ComfyUI root and `models\\DLSS` (env
  overrides: `ANTS_EVIDENCE_REPO` / `ANTS_EVIDENCE_COMFY` /
  `ANTS_EVIDENCE_DLSS`), refuses mangled paths explicitly, never crashes on a
  NOT FOUND (it reports what it searched and exits non-zero), and the bat
  passes no path arguments at all.
- NEXT: re-run (the new first-chance decode will name the exception type and
  the snippet offset that threw, which is the branch that rejects our frame
  or parameter set). Also still unexecuted: E1 (`ANTS_NR_USE_SHIM=0`).

### 2026-09-20 (run-30 follow-up) — corpus sweep: the caller check is PUBLISHED, and the command-list hygiene we were missing
- GitHub CODE search works here (`gh api -X GET search/code -f q='"DLSSNR.ScalingRatio"'`)
  - six more public feature-18 hosts/mirrors are now in
  `/home/user/ext_research/dlss5_*` (Veyra param header, vapourkit param
  header, OptiScaler proxy, plus three design notes). Distilled into
  `RESEARCH/NOTES.md`.
- Two independent headers confirm our `DLSSNR.*` namespace exactly, and give
  the reason names had to be recovered from the binary: **NGX silently
  ignores unknown parameters** - a typo is a no-op, never an error.
- **The caller check has a published answer**: the host hooks the SNIPPET's
  own `KERNEL32!GetModuleFileNameW` IAT slot to answer `nvngx.dll` - *"the
  snippet verifies its caller is `nvngx.dll`"*. That is the mechanism our
  shim satisfies structurally (real frame + module name containing
  `nvngx.dll`; the working LQCCS helper does the same from
  `nvngx.dll_comfy.dll`). Our Init_Ext hr=1 is evidence the check passes;
  E1 stays the presence test.
- **Feature 18 never goes through the core** (core CreateFeature(18) →
  `0xbad0000b`, "core has no NR implementation") - every working host calls
  the snippet's five exports, which is our route.
- **BUG FIXED (contract parity)**: `nr.py::evaluate` handed the runtime a
  command list with our frame copy still pending and never executed the
  runtime's own recording. The proven hosts close+execute+fence-wait around
  every copy (before the call, empty list) and right after the feature call
  (their work committed before the readback). `nr.py` now drains before
  evaluate and submits right after; two order-sensitive pins in
  `test_native_flow` fail if either goes away.

### 2026-09-20 (later still) — the crash file decoded; the collector moved OUT of the repo and now names crash offsets itself
- **THE OWNER'S `native-crash.log` CONTAINS THE SILENT-KILL MECHANISM, WITH A
  FAULTING ADDRESS**: two `NATIVE CRASH: exception 0xC0000005 (access
  violation) at C:\WINDOWS\System32\KERNEL32.DLL+0x27799`, then two
  `TERMINATION via ntdll!NtTerminateProcess(handle=0x0 /
  0xFFFFFFFFFFFFFFFF, status=0x00000002)` with libffi/ctypes/python frames.
  Reading: the faulting INSTRUCTION was inside KERNEL32 itself (a deref in
  kernel32-resident code - GetProcAddress-family lives there, unlike most
  APIs which forward to KernelBase), and the process was then terminated
  DELIBERATELY (status 2, not a crash unwind). The same offset was already
  the example in `resolve_offsets.bat`, i.e. this is a RECURRING fault from
  the earlier runs too. Note the file's mtime (07:30) is a DIFFERENT run
  from the 07:26 one whose console we saw - and `neuroframe_caller-104960`
  staging at 07:29 says that run was the LEGACY route, so legacy mode is not
  clean either (contrary to the 11.37 s run).
- **Hardware faults now carry a caller chain** (`_stack_chain`, capped at 8
  emissions): the AV line ends with `from <frames>` - that is what tells us
  whether the kernel32 fault was entered from our ctypes frame or from the
  runtime's own code. Cheap, and decisive for the next occurrence.
- **COLLECTOR MOVED OUT OF THE REPO** (owner instruction: GitHub Desktop kept
  offering the dumps): the default output root is now
  `C:\ComfyUI_PORTABLE\NODE_CODING\RIG_EVIDENCE\<timestamp>`, i.e. a
  sibling of the ComfyUI folder - never inside the checkout. Override with
  `ANTS_EVIDENCE_OUT`; a `[!]` note is printed if the resolved folder IS
  inside the pack. (The old `tools/rig_evidence/` stays gitignored as belt
  and braces.)
- **The report now names crash offsets itself**: every `MODULE+0xRVA` in the
  collected logs is resolved to an export through the sibling
  `resolve_crash_offset.py` (module found in System32, the python folder or
  the staged runtime folders), with the neighborhood printed. Case-insensitive
  (crash lines say `KERNEL32.DLL`, the file is `kernel32.dll`), a full path in
  a crash line is handled, and a non-PE file is reported, never fatal.
- **The black box is INLINED in the report** (last 60 lines of
  `native-crash.log`), so the clipboard paste carries the verdict itself.
- **OWNER CORRECTION (this file had it wrong)**: `models/DLSS/NR/
  nvngx_dlssnr.dll` is the FULL 158.15 MB RenoDX runtime, not a caller copy.
  The 102.50 KB caller-named file lives in
  `staged/neuroframe_caller-104960/nvngx_dlssnr.dll` (legacy staging output),
  which is where the confusion came from. Never call the NR-folder file a
  caller again.

### 2026-09-20 (owner Q&A) — the helper pair gets ONE home; exact paths documented; the black box is session-scoped
- **Owner request answered: `docs/MODELS_DLSS_LAYOUT.md`.** It lists every
  path the code reads (NR/, SR/, FG/, Merserk_DLLS/, staged/), why `staged/`
  must exist (the NGX core resolves the literal names `nvngx_dlssnr.dll` /
  `nvngx_dlss.dll`, the snippet imports its dependencies from its own folder,
  the legacy engine is handed a directory), and a keep/delete table for the
  owner's tree (per-category helper copies = delete, `staged/**` = delete
  while ComfyUI is closed).
- **CODE CHANGED so the duplication is not needed** (owner preference:
  pair stored/called from `models/DLSS/Merserk_DLLS` ONLY):
  `stage_nr_runtime` no longer copies every sibling of the source folder into
  the stage. It copies the chosen runtime under the canonical name plus the
  helper pair from the ONE stash - `Merserk_DLLS` > `HELPERS`/`HLP*` > the
  package `dll/` folder; a stash only counts when it actually contains .dll
  files (the owner's `HELPERS/` is empty and must never win). No stash at all
  -> the old sibling-copy behaviour stays as a loud compatibility fallback.
- **Auto selection can no longer pick a helper DLL as "the runtime"**: auto
  considers `nvngx_dlssnr*` files first; helper-named files are skipped and,
  when nothing else exists, the failure is a loud `[ANTs]` error naming
  `Merserk_DLLS`. Rig evidence for this: the 07:29 stage was named
  `neuroframe_caller-104960` - the legacy route had been handed the caller as
  the "runtime" by the old first-flat-dll rule.
- **The crash black box is session-scoped**: `arm()` writes a
  `=== ANTs crash black box: session <time> pid <pid> build <..> ===` header
  every time (the file is append-only and outlives the process), the collector
  shows only the newest session, and hardware-fault/termination chains now go
  through `_caller_chain`, which drops libffi/_ctypes/python noise (run 30's
  chain was eight FFI frames and named no native caller).
- **The collector audits the models tree**: the report carries a
  "MODELS/DLSS LAYOUT AUDIT" section (KEEP / SAFE TO DELETE / YOUR CALL),
  including same-size-same-folder pairs with a hash verdict, so the keep/delete
  answer is printed from the owner's own disk, not from a folder map.
- **OWNER'S 14:02 RIG REPORT PROVED A CONFOUND** *(the risk classification
  below was removed the same day - see the owner-correction entry further
  down; keep this only as history)*: his
  `NR/nvngx_dlssnr.dll` and `NR/nvngx_dlssnr_RenoDX_4000_series_friendly.dll`
  are the SAME 165,830,144-byte build (identical SHA-256, and both are the
  RenoDX build). The force-terminator list matches NAMES, so `auto` would
  have run the risky build under the safe-looking name. `auto` now compares
  BYTES (`same_bytes`/`twin_of_known_bad`, size gate first, SHA-256
  memoised): a candidate byte-identical to a listed sibling is refused with a
  warning naming the twin, in both the flat-file and the set-directory passes;
  an explicit widget pick is still honoured. The audit prints the same
  verdict (`[!!] ... IS ...`).
- **The evidence report is ONE self-contained file** (owner request): all
  collected logs are inlined (400 KB per file, `[truncated: last ...]` when
  clipped, crash box excluded because it has its own section), so the folder
  is only needed for an oversized log. The empty `files/` in the 14:02 report
  was expected - the owner deleted `staged/` right before collecting, and the
  logs live there.
- Suite at this commit: **373 checks** (dlsssr 85, dlssnr_bridge 82,
  native_flow 35) + pyflakes/scope/smoke green.

### 2026-09-20 (owner Q&A #2) — THE BUILD NAMING RULE: names carry versions, auto loads the newest
- **Owner context (important, previously unknown to us):** both NR files in his
  tree are the SAME RenoDX build hacked for 3000/4000-series support, taken
  from Merserk's `Visual.Enhancer.v10.0` bundle - the same bundle the
  neuroframe pair comes from, which is why the pair is tied to those
  non-standard runtimes; the build itself originates in RenoDX's ReShade
  bundle. He keeps BOTH names **deliberately, to test our automatic picking
  and manual selection**. One of them will later be replaced with a newer
  build - the picker must work before that. (Also explains why the previous
  session's per-folder helper copies existed: nobody had written the rule
  down.)
- **THE RULE (now in `CLAUDE.md` 2b, `README.md`, `docs/MODELS_DLSS_LAYOUT.md`
  and implemented in the new `ants/dlsssr/versions.py`):** the FILE NAME
  carries the version - `nvngx_dlssnr_<date>.dll` for the community NR
  builds, `nvngx_dlss_<version>.dll` / `nvngx_dlssg_<version>.dll`
  (NVIDIA numbering) for SR/FG. `auto` = newest by that rule; date > dotted
  version > bare number > no version (unversioned sorts last, ordered by file
  date); free text after the version is ignored; hardware tags (4000, 3090,
  series, friendly) are never versions. The selector lists builds newest
  first; an explicit pick always wins.
- **Decision changed (my previous session's byte-twin refusal was too strict):**
  `auto` NEVER refuses an NR folder whose builds are all on the rig-proven
  risk list - his own folder is exactly that case. New policy: prefer the
  newest SAFE build when one exists (loud note naming what was skipped and how
  to override), otherwise use the newest risky build with a loud warning
  (runs 14-19 lost the session; run 30 reached EvaluateFeature and threw a
  catchable exception - these builds are the live line of work, not a dead
  end). A rename still counts as the same risky build (byte-identity), and an
  unversioned file newer ON DISK than the picked build is reported, so nobody
  has to guess why it lost.
- **SR side unified**: `_sr_set_dll` picks the newest inside a set folder, and
  `resolve_sr_dll("auto")` ranks flat files and subfolders together by the same
  rule (a stale widget selection falls back to the newest build, loudly).
  `ants/dlsssr/discovery.py` got a fallback logger (importable without ComfyUI).
- **Collector**: the audit now prints `AUTO would load in <category>/: <name>
  (<version>)` for every category and annotates each KEEP line with the
  version it parsed - the owner sees which build wins, in his own report.
- Suite: **397 checks** (dlsssr 89, dlssnr_bridge 99, native_flow 38) +
  pyflakes/scope/smoke green.

### 2026-09-20 (run 20:39/20:40) — DEVICE REMOVED is the root event; cascades removed

- **Reading the log correctly matters**: the two `Close 0x80070057` failures,
  the successful `EvaluateFeature` (hr=1, internal C++ throw caught) and the
  fatal `CreateCommandAllocator 0x887A0005` are ONE event -
  `DXGI_ERROR_DEVICE_REMOVED`. The legacy "Could not create a D3D12 device
  matching CUDA ordinal 0 by LUID" immediately after, in the same process, is
  the wedged-driver state; the stage line names the same `Merserk_DLLS` stash
  as the working 18:16 run, so the staging change is not implicated.
- **Code**: `device_status()`/`mark_device_removed()`/`device_removed_error()`
  name the reason (DEVICE_HUNG = ~2 s TDR timeout, DEVICE_RESET,
  DRIVER_INTERNAL_ERROR) and raise ONE actionable error; dead contexts refuse
  further submits and build nothing (no more allocator cascade); `node.py`
  drops the session AND the GPU context so the next frame rebuilds a fresh
  device; the legacy LUID failure explains itself (restart first, then the
  helper pair + the collector inventory); the adapter name/LUID we bound is
  logged; `ANTS_D3D12_CHECKPOINT=1` (diagnostic) closes/executes/waits after
  every recorded command and names the invalid one.
- Suite: **403 checks** (dlsssr 89, native_flow 42, dlssnr_bridge 101).

### 2026-09-20 (run 18:16/18:25) — CUDA regression diagnosed + fixed; the native host's command list isolated

- **Owner rig run 18:16 (legacy engine): the auto picking works** with a
  deliberately renamed `nvngx_dlssnr_RenoDX_4000_series_friendly_2026-08-13.dll`
  - staging named the build + version from the file name and the engine ran
  (18.69 s for the prompt). The stage line now also states where the helper
  pair came from.
- **BUG (owner-reported, high priority): "engine lacks CUDA interop (False)"**
  - GPU acceleration silently fell back to CPU staging on a 4090, ~20-25x
  slower; the message even printed a BOOL instead of the engine's reason.
  Fixes:
  * `node.py` prints the engine's own explanation and, right before it, an
    `engine:` line naming the build + which CUDA entry points it exports.
    A loud warning follows whenever acceleration is ON but we use CPU.
  * NEW `ants/dlssnr/peexports.py`: a **read-only export-table reader** (bounded
    file reads, no loading, hostile/truncated images return "no names"). This
    is how the pack learns what a 158 MB DLL exports without running it.
  * `core.find_engine_dll` now prefers a build that exports
    `dlss5nr_init` + the CUDA pair over an alphabetically-earlier plain one.
  * `discovery.choose_helper_stash()` stages from the stash whose engine HAS
    the CUDA entry points, even when that is the lower-priority folder; the
    log names it. No CUDA-capable build anywhere -> normal order + a reason.
  * Collector: new **HELPER / ENGINE INVENTORY** section listing every helper
    DLL (size, sha head, exports) across models/DLSS, the staged folders and
    the owner's `I:\ComfyUI\MODELS\DLSS` mirror, flagging when no build on
    disk can do GPU.
- **BUG (native engine): `ID3D12GraphicsCommandList.Close` → 0x80070057 after
  the first EvaluateFeature killed the node** (ComfyUI survived). Two changes:
  * the runtime now records into a **dedicated command list**
    (`GpuContext.command_list()`); Create/Evaluate are handed that one, so a
    runtime-mangled recording can never take our frame uploads with it;
  * a recording that cannot be closed is VOID: the list+allocator are PARKED
    and a FRESH pair replaces them (`_drop_recording`) - recovery never raises
    unless `ANTS_D3D12_STRICT_CLOSE=1`. The runtime path warns that this
    frame's output is stale and counts the recoveries
    (`gpu.runtime_recoveries`), which the next evidence run should carry.
  * pre/post bracketing is unchanged: our copies drain BEFORE the feature call,
    the runtime's own recording is executed right AFTER it.
- Suite: **397 checks** (dlsssr 89, dlssnr_bridge 99, native_flow 38) +
  pyflakes/scope/smoke green.

### 2026-09-20 (owner correction #3) — there is NO "safe vs risky" build: the community RenoDX build IS the path
- **Owner clarification (authoritative):** the official DLSS 5 NR runtime targets
  RTX 50-series; on RTX 30/40 series (most users, and his own 4090) the
  community RenoDX-derived builds are the ONLY ones that work. The pair in his
  tree is that build, from Merserk's `Visual.Enhancer.v10.0` bundle (the
  neuroframe pair comes from the same bundle, hence the tie-in); the build's
  lineage is RenoDX's ReShade bundle.
- **Therefore the whole risk-gating layer is GONE** (`KNOWN_FORCE_TERMINATOR_
  MARKERS`, `is_known_force_terminator`, `risk_reason`, `twin_of_known_bad`,
  and the `skip_known_bad` parameter of `resolve_nr_runtime_path`). Selection is
  now ONE rule: explicit pick wins, otherwise the newest by the naming rule -
  no name is ranked, skipped or refused. `node.py` no longer prints the
  scare-warning; it prints an informational `provenance_note` status line
  (RenoDX/clshortfuse + Merserk credit, RTX 30/40 reality).
- **The history is kept, but only as history**: a provenance header at the top
  of `ants/dlssnr/discovery.py` (runs 14-19 killed the process on the
  pre-run-30 host; run 30 reached EvaluateFeature and threw a catchable
  exception; the gap was in OUR provider, never in the build). The traps and
  the black box remain the watchdogs - they were never part of the gate.
- **`same_bytes` stayed** (duplicate detection, reporting only): the owner
  keeps two names of one build ON PURPOSE to test the picker, and the rig
  audit now says `[note] the renaming does not change the build ... both load
  the same way; the pack does not rank builds by name` instead of the old
  "[!!] auto will refuse BOTH".
- Docs updated to the same language: README, `CLAUDE.md` 2b,
  `docs/MODELS_DLSS_LAYOUT.md` ("Which NR builds exist, and why the community
  ones are the normal path").

### 2026-09-20 (owner lead) — CUDA LUID adapter identity; the #15255 multi-GPU family
- **Owner pointed at ComfyUI PR #15451** (OPEN, `mergeStateStatus: BEHIND`,
  refs issue **#15255 / CORE-398**): on Windows, once a process touches more
  than one GPU, host->device copies can fail with `CUDA_ERROR_OUT_OF_MEMORY`
  (result=2) with plenty of VRAM free, and that CUDA context never recovers.
  Maintainer rattus128 reproduced it with raw CUDA **only in the multi-GPU
  shape** (`windows_cuda_host_memory_diagnostic.py` in the issue): clean
  single-GPU probe = 1000 pinned transfers PASS (registration to 22.750 GiB);
  all-GPU probe = registration fails after 12.000 GiB, then EVERY
  `cuMemcpyHtoD_v2` fails, `free=0/total=0` after. Workaround today:
  `--cuda-device 0` (or one UUID) and/or `--disable-pinned-memory`; PR #15451
  makes the single current device the core default. Our 20:39/20:40
  DEVICE_REMOVED + legacy "by LUID" report is the same family on the D3D12
  side.
- **NEW `ants/dlsssr/cuda_luid.py`**: CUDA identity via the driver API from
  `nvcuda.dll` (`cuInit`, `cuDeviceGetCount/GetName/GetLuid`; the
  `cudart64_*.dll` next to torch is the fallback). `device_luid(ordinal)`,
  `view()`, `count()`, `format_luid()` (`hi:lo` hex, like aimdo /
  nvidia-smi), `reset_cache()`. Identity queries only, one device at a time,
  every failure is a reason string - never an exception in ComfyUI's process.
- **Adapter pick fixed (`d3d12.py`)**: `AdapterInfo` (name/LUID/vendor/device
  id/flags + `software`/`nvidia`/`describe()`), `enumerate_adapters()`,
  `pick_adapter(adapters, ordinal)` (CUDA LUID -> DXGI adapter; fallback =
  first hardware adapter, NVIDIA preferred, a software/WARP adapter can never
  win) and **`make_gpu_context(ordinal)`** - the ONE way to build a
  GpuContext, creating the device on the matched adapter via
  `D3D12CreateDevice(adapter, ...)` instead of the default adapter. Both
  paths (`dlsssr` SR, `dlssnr` native NR) use it. This also makes the
  `--cuda-device N` workaround safe: it renumbers CUDA only, so DXGI index
  != CUDA ordinal there.
- **LUID offset was WRONG**: `DXGI_ADAPTER_DESC1` keeps its `LUID` as EIGHT
  bytes at **0x128** (LowPart, HighPart); the old `desc + 0x12C` pair printed
  `{HighPart, Flags}`. Vendor 0x100, DeviceId 0x104, Flags 0x130, desc 312 B.
- **Loud lines**: one `[ANTs] D3D12 host adapter ... - CUDA ordinal N LUID
  ... -> adapter ...` status line per context; a one-shot advisory when >1
  CUDA device is visible (#15255 + the exact flags); the device-removal error
  gains clause (3) with the same flags; the legacy "by LUID" error now names
  cause (1) wedged process before (2) fresh-process multi-GPU.
- **New owner tool `tools\check_cuda_multigpu.bat` (+ `.py`)**: fresh-process
  A/B - a child with `CUDA_VISIBLE_DEVICES=0` registers a pinned host buffer
  and round-trips it, then this process touches every GPU (primary contexts
  retained) and repeats the copy. Exit 10 = `BUG REPRODUCED` (keep ComfyUI on
  one GPU), 0 = not reproduced, 2 = could not run / single GPU. Writes
  nothing; its own process on purpose (it may poison its CUDA context).
- **Collector**: new `CUDA / MULTI-GPU VIEW` section - every device with its
  LUID (from the pack's `cuda_luid.py`, loaded by file) + the launch flags
  found in the collected logs, so the next report says whether #15255 even
  applies.
- Suite: **413 checks** (dlsssr 90, native_flow 51, dlssnr_bridge 101) +
  pyflakes/scope/smoke green.

### 2026-09-20 (owner evidence 21:17) — ONE GPU (so #15255 is out), the LUID read is verified, and the kill is captured
- **The rig is single-GPU**: collector's new `CUDA / MULTI-GPU VIEW` and
  `check_cuda_multigpu.bat` both report one RTX 4090 (LUID
  `00000000:000497ea`) → the #15255 / PR #15451 multi-GPU CUDA bug CANNOT
  apply here; the `--cuda-device 0` A/B is dropped from the ladder. The
  adapter-by-LUID fix stays (it is what makes `--cuda-device N` safe at all).
- **The LUID fix is VERIFIED against NVIDIA's own line**: `nvngx.log` says
  `Found matching adapter with NVAPI physical GPU handle: 0xc00 and LUID:
  { 0x0, 0x497ea }` = exactly what our reader prints. (The old `desc+0x12C`
  read produced a bogus pair; 0x128 is right.)
- **The kill is captured with a status**: `native-crash.log` shows four
  caught `0xE06D7363` C++ throws, then
  `TERMINATION via ntdll!NtTerminateProcess(handle=0x0, status=0x2)` (invalid
  handle → fails) and `(handle=0xFFFFFFFFFFFFFFFF, status=0x2)` = NtCurrentProcess
  → **the process is terminated with exit code 2**, from inside one of our
  ctypes calls (chain is libffi → _ctypes → python only).
  OPEN: did ComfyUI actually die at 20:39:50? If yes, the 20:40 legacy "by
  LUID" failure happened in a FRESH process and is a real bug, not the wedged
  state. Asked the owner.
- **New diagnostics**: every ctypes call into the engine (`dlss5nr_*`), NGX
  (`NVSDK_NGX_*`, via `NgxModule.fn`/`fn_raw`) and the D3D12 vtables
  (`ComObject.call`/`call_hr`) sets `crashlog.set_phase(...)`, and every
  kill/exception line prints `[in-flight call: ...]` — the next report says
  WHICH call the process died in. Plus `ANTS_NR_BLOCK_TERMINATION=1`
  (opt-in A/B): the trap logs and refuses `ExitProcess`/`TerminateProcess`
  (`abort`/fast-fail never blocked) so the node can fail loudly.
- **Collector bug found + fixed (owner-facing false alarm)**: with no
  arguments (how the bat calls it) `load_pack_peexports(args.repo)` got `""`,
  so the export reader was missing → EVERY helper printed "not a neuroframe
  engine" + `[!] NO helper build on disk exports the CUDA entry points` (a
  false alarm; the node's own `find_engine_dll` had named a CUDA-capable
  engine at 20:39). Now the resolved `repo` is passed, the report says loudly
  when no reader was loaded, and `test_dlsssr` runs the collector the way the
  bat does (subprocess, no `--repo`, neutral cwd) — verified to fail with the
  bug and pass with the fix.
- Suite: **414 checks** (dlsssr 91, native_flow 51, dlssnr_bridge 101) +
  swapper_state 13 + pyflakes/scope/smoke green.

### 2026-09-20 (owner runs 21:47 / 21:52 / 21:53) — the native path's first real evaluate, and the CUDA gate in the engine's own words
- **21:52 = the furthest the native host has ever got**: Init_ProjectID hr=1,
  GetCapabilityParameters hr=1, snippet Init_Ext (via shim) hr=1,
  CreateFeature(18) hr=1, EvaluateFeature returned **hr=1** (with an internal
  caught C++ throw). Then the frame died in three steps.
- **Step 1 — our copy list**: `Close 0x80070057` while
  `Device status: 0x00000000 (device present and healthy)`. So something IN
  that recording was invalid - not a removal. The log could not say which list
  it was; it now names the list + its last recorded commands.
- **Step 2 — the device**: the runtime list's Close reported the device gone
  with `0x887A0001`, which the pack called "REMOVED ... unknown removal
  reason". 0x887A0001 is **DXGI_ERROR_INVALID_CALL** (DEVICE_REMOVED is
  0x887A0005), and it is also what the next frame's `D3D12CreateDevice`
  answered at 21:53:17 - the process was already finished with D3D12. Now:
  `describe_hresult()` names every code, a failed CreateDevice in a process
  that has lost a device says RESTART ComfyUI, `_removal_text` is honest.
- **Step 3 — the cleanup crashed the driver**: `NATIVE CRASH 0xC0000005 at
  nvwgf2umx.dll+0x6D3471 [in-flight call: ID3D12GraphicsCommandList]` with the
  Python stack `com.py release <- d3d12.py close <- _node._close_native`.
  Releasing a dead device's objects is what crashed. `GpuContext.close()` now
  releases NOTHING when the device is gone, parks the objects, and logs it;
  the removal is remembered process-wide (`note_wedged`).
- **THE LIKELY ROOT CAUSE of the E_INVALIDARG Close (all three runs)**: the
  colour/guide INPUT textures were in the UNORDERED_ACCESS state. The engine's
  contract (and the shipped open-source ComfyUI host `lisitskyaa/
  ComfyUI-DLSS5-NR`, read for technique: same project id, same shim, D3D12,
  single command list, inputs in NON_PIXEL_SHADER_RESOURCE, output in UAV) is
  that inputs are SHADER RESOURCES. Fixed in nr.py + sr.py, with
  `ANTS_NR_INPUT_STATE=uav` as the A/B; the contract line prints both states.
- **The CUDA gate, decoded** (21:47): the engine's own status text says
  "active CUDA primary context does not use FFmpeg blocking-sync flags" - it
  wants `CU_CTX_SCHED_BLOCKING_SYNC` (0x04) on the process's primary context,
  which PyTorch never sets. New `ants/dlsssr/cuda_flags.py`: read
  `cuCtxGetFlags`, try `cudaSetDeviceFlags(0x04)` (legal only before the
  context exists), `cuDevicePrimaryCtxSetFlags`, `cuCtxSetFlags`, report every
  return code; called at IMPORT from `ants/__init__.py`; one-line summary in
  the node's CPU-staging warning; `ANTS_NR_CUDA_FORCE=1` tries the CUDA entry
  point anyway (A/B, crash box armed). Same machine: OreX's node does the same
  DLL with CUDA zero-copy in 2.65 s vs our 14-18 s.
- Suite: **434 checks** (dlsssr 93, native_flow 56, dlssnr_bridge 101) +
  pyflakes/scope/smoke green.

### 2026-09-20 (owner runs 22:35 / 23:08) — CUDA zero-copy SOLVED, the native input recipe fixed, and the node split
- **CUDA zero-copy works (legacy engine)**: 1.18 s per frame vs 14-18 s on host staging, same DLL.
  The blocking-sync flag armed at IMPORT (`cudaSetDeviceFlags(0x04)` before torch built the primary
  context) opened the engine's own gate. CLOSED - do not re-debug, do not re-run the
  `--cuda-device` / pinned-memory experiments.
  - `0x0C (unknown scheduling)` was a WRONG MASK: scheduling is the low 3 bits (CU_CTX_SCHED_MASK =
    0x07); 0x0C = blocking-sync + map-host. Fixed, and `log_early` now names the arming ROUTE.
- **Native host, new wall (23:08)**: `CreateCommittedResource(nr motion) 0x80070057` - D3D12 rejects
  **(ALLOW_UNORDERED_ACCESS, NON_PIXEL_SHADER_RESOURCE)**. Inputs are now PLAIN shader resources
  (`FLAG_NONE` + NP_SRV, the proven host's recipe) via `create_input_texture2d`, with a recipe ladder
  and a LOUD line naming the accepted recipe when the intended one is refused.
- **Node split (owner request)**: `ANTsDLSS5Processor` "Processor (ReShade based)" = legacy DLL
  engine forced, drops engine/sr_dll_version/sr_model/pre_denoise_mode/nr_model_preset/fg_dll_version;
  `ANTsDLSS5ProcessorNative` "Processor (Native NGX, experimental)" = native forced, drops
  engine/gpu_acceleration/fg_dll_version. Both subclass the full enhancer (one implementation),
  both take the same scheduler output, each has its own JS header + refresh button + greying.
  - Naming answer: the DLLs come from the RenoDX DLSS-5 addon (a ReShade addon) -> "ReShade based" is
    accurate; **OptiScaler is a different project** (DLSS/XeSS/FSR redirector) and is not involved.
- **Scheduler**: default `passes` = 3 with the cycle **Cinematic -> Natural -> Default** (Python +
  JS), and the JS now RE-FITS the node after rebuilding the dynamic rows (`fitNode`:
  computeSize/setSize) - it used to grow on toggle-ON and never shrink back on toggle-OFF.
- Suite: **440 checks** (dlsssr 98, native_flow 56, dlssnr_bridge 101) + pyflakes/scope/smoke green.

### 2026-09-20 (owner run 23:32) — the third layer, and it was our own fallback
- **The driver refused `(ALLOW_UNORDERED_ACCESS, UNORDERED_ACCESS)` for `nr output`** (never a working
  recipe: the byte was 0x8 = DENY_SHADER_RESOURCE and 21:52 was the silent flags-0x0 fallback - see the
  2026-09-21 root-cause block), our recipe ladder silently degraded to `(FLAG_NONE, COMMON)`, and the NR path then
  recorded `Barrier(nr output -> 8)` on a resource WITHOUT the UAV flag = an INVALID COMMAND. That is
  the E_INVALIDARG that shows up as "command list not closable" — the 18:25 / 20:39 / 21:52 mystery
  was, at least in part, our own degraded fallback.
- Fixes: the ladder can never drop the UAV flag (a UAV texture fails LOUDLY, naming the device status
  at that moment); a refused/degraded recipe is NEVER cached on the device; `GpuContext.health` reads
  `GetDeviceRemovedReason` at creation and announces a device that is born unusable.
- **OPEN**: was the 23:32 native run in the same ComfyUI process as the 22:35 legacy CUDA run? Clean
  experiment = restart, native node FIRST and alone.
- **NODE PARITY (owner rule, enforced by test)**: `set(proc inputs) == set(enhancer inputs) -
  {"engine"}` for BOTH focused processors - only the engine selector is dropped; the engine-only
  widgets stay and explain themselves (SR pre-denoise warns on the legacy node, CPU/host-staging says
  "native is always a GPU path").
- Suite: **444 checks** (dlsssr 100, native_flow 58, dlssnr_bridge 101, nr_schedule 33).

### 2026-09-20 (owner runs 23:46 / 23:48) — the UAV refusal is process state, and the suspect is our own CUDA path
- The new loud failure proves the description is VALID: `nr color` (plain shader resource) is created in
  the same sequence, the device reports healthy, and every UAV recipe is refused.
- Correlation across all runs: native-only processes created UAV textures (21:52, 23:08); every process
  where the LEGACY CUDA zero-copy path had run created NONE (23:32, 23:46, 23:48). Prime suspect:
  **a process that ran the legacy CUDA path no longer accepts UAV-capable D3D12 textures**.
- Shipped `tools/check_d3d12_uav.{bat,py}`: 4 phases (fresh 11_0 / fresh 12_0 / after plain CUDA work /
  after the staged legacy engine), verdict + exit code (10 = REPRODUCED, 11 = fails fresh, 12 = level
  decides, 2 = not Windows), READ-ONLY, uses the pack's own d3d12 path.
- Shipped `ANTS_D3D12_FEATURE_LEVEL=12_0` (the reference host asks for 12_0; we default to 11_0) and
  the level is printed with the adapter pick; the texture error names the workaround.
- WORKAROUND for the owner: restart ComfyUI, run the NATIVE node FIRST and alone.
- Suite: **446 checks** (dlsssr 103, native_flow 58, dlssnr_bridge 101, nr_schedule 33).

### 2026-09-21 (owner run 01:06 + the probe) — the legacy engine is CLEARED; the UAV refusal is pack-armed-CUDA-flag or a degraded driver
- Probe result on the rig: **every phase failed**, including phase A (fresh process, fresh device,
  256x256 RGBA16F UAV texture at 11_0 -> E_INVALIDARG, device healthy). Phase B (12_0), C (after
  plain CUDA work) and D (after the legacy engine) failed too. The legacy engine is therefore NOT the
  trigger - the probe never loads it before phase A.
- Remaining candidates: (a) the pack's own import-time arming (`cudaSetDeviceFlags(0x04)`) - the probe
  arms it before phase A, the one successful native run (21:52) predates that code; (b) a driver left
  degraded by the earlier removals/TDRs (a reboot test).
- Probe now: **phase A0** = the same test in a fresh CHILD process with `ANTS_NO_CUDA_FLAG_ARM=1`
  (exit **13** when A0 passes and A fails = the arming is the trigger), plus a **plain-texture control**
  in every phase. `ANTS_NO_CUDA_FLAG_ARM=1` is a documented knob.
- **cp1251 console bug (owner-visible)**: the packaging glyph U+26A1 in the logger name made EVERY
  line raise `UnicodeEncodeError` inside `logging.emit` - the line was lost and a traceback printed
  instead. `ants/log.py::_SafeStream` encodes with errors="replace" now.
- **pre-denoise OFF** (owner request): third mode value, stage fully disabled (model/strength ignored
  and logged), JS greys `pre_denoise_strength` (`(stage OFF)`) and keeps the value; mode greying beats
  schedule greying.
- Suite: **449 checks** (dlsssr 105, native_flow 58, dlssnr_bridge 101, nr_schedule 33).

### 2026-09-21 (ROOT CAUSE) — the native UAV wall was ONE WRONG BIT: 0x8 is DENY_SHADER_RESOURCE, the UAV flag is 0x4
- **`d3d12.h` / `D3D12_RESOURCE_FLAGS` docs**: ALLOW_RENDER_TARGET 0x1, ALLOW_DEPTH_STENCIL 0x2,
  **ALLOW_UNORDERED_ACCESS 0x4**, **DENY_SHADER_RESOURCE 0x8**, ALLOW_CROSS_ADAPTER 0x10,
  ALLOW_SIMULTANEOUS_ACCESS 0x20. `ants/dlsssr/d3d12.py` had defined 0x8 as the UAV flag since the
  first commit: the pre-rig line in the shipped-history table below records that edit as a FIX (0x4 ->
  0x8) - it was the bug. Every texture this pack meant as a UAV carried DENY_SHADER_RESOURCE instead.
- **Owner-run probe, one variable**: same device, same 256x256 RGBA16F description, same
  `D3D12_HEAP_TYPE_DEFAULT`, same initial state COMMON -> flags **0x8 REFUSED** (0x80070057, device
  healthy), flags **0x0 accepted**. 0x4 was never sent. That single comparison explains the 21:52
  silent fallback to a flagless texture (and its `Close 0x80070057` + NGX C++ exception + AV + device
  removal), the 18:25 / 20:39 / 23:08 Close mystery, and the 23:46 -> 01:22 loud creation refusal.
- **Only the byte and the process-state theories die with it**: A0 (pack CUDA flag arming), B (feature
  level 12_0), A/C/D (legacy engine) were all cleared by the probe, and the owner's 3ds Max / 10 GB VRAM
  observation is consistent - a plain texture is always fine because it has no flags at all.
- **The documented rule** (learn.microsoft.com, D3D12_RESOURCE_FLAGS): DENY_SHADER_RESOURCE "Must be
  used with D3D12_RESOURCE_FLAG_ALLOW_DEPTH_STENCIL" - so 0x8 alone is an INVALID description, refused
  by any D3D12 runtime on any machine, not a driver/CUDA/process-state symptom. Same conclusion reached
  independently (owner asked Claude Sonnet 5 with the same log; its one off note: the texture FORMAT is
  irrelevant to this refusal, and the `_fwd_stub` fix it mentions was already applied + credited).
- **The instrument that would have named it in one run**: the D3D12 debug layer, opt-in
  (`ANTS_D3D12_DEBUG_LAYER=1`, `ANTS_D3D12_DEBUG_MESSAGES` cap, needs the Windows "Graphics Tools"
  feature) - `d3d12.enable_debug_layer()` arms it before device creation, and a refused description
  drains the runtime's own sentences out of `ID3D12InfoQueue` (GetMessage slot 5, GetNumStoredMessages
  slot 8, 32-byte D3D12_MESSAGE header, all pinned against d3d12sdklayers.h). Bounds-checked, capped,
  never raises; the probe arms it by default and prints the state. `com.ComObject.query_interface` is
  the new door (returns None instead of raising: a release runtime has no InfoQueue).
- **Shipped**: `D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS = 0x4`; `DENY_SHADER_RESOURCE` named;
  `RESOURCE_FLAG_NAMES` + `resource_flag_name()` so every recipe/log line prints the BYTE next to the
  name (`0x4 (ALLOW_UNORDERED_ACCESS), initial state ...`); `create_texture2d_with_flags()` (one exact
  recipe, no ladder) for this kind of bisect; the probe's **FLAGS MATRIX first** (0x4 / 0x8 / 0x0) with
  exit **14** = "THE FLAGS BYTE WAS THE BUG" and exit 11 = "even the correct byte is refused fresh ->
  reboot, then probe"; the refusal text and README no longer blame the legacy CUDA path.
- Tests: the whole flag table is pinned against d3d12.h (the old check pinned `== 0x8` with the comment
  "(0x4 = render target)" - the suite was holding the bug in place). Suite: **452 checks** (dlsssr 108,
  native_flow 58, dlssnr_bridge 101, nr_schedule 33).


### 2026-09-21 (RIG VERDICT) — probe exit 14, and the NATIVE NODE RUNS: unique image per style
- **Probe (owner, ~01:44): exit 14.** The flags matrix runs FIRST: **0x4 CREATED**, **0x8 REFUSED**
  (0x80070057) with the runtime's own sentence out of the debug layer -
  `ID3D12Device::CreateCommittedResource: D3D12_RESOURCE_DESC::Flags cannot have
  D3D12_RESOURCE_FLAG_DENY_SHADER_RESOURCE set without D3D12_RESOURCE_FLAG_ALLOW_DEPTH_STENCIL,
  D3D12_RESOURCE_FLAG_VIDEO_DECODE_REFERENCE_ONLY or D3D12_RESOURCE_FLAG_VIDEO_ENCODE_REFERENCE_ONLY.`
  - and **0x0 CREATED**. `[DOC]` control (flags 0xA on D32_FLOAT) ACCEPTED; device `0x00000000`
  (present and healthy) after the whole matrix. Note the runtime accepts two companions beyond the
  docs quote: the VIDEO_DECODE/ENCODE_REFERENCE_ONLY flags also legitimize 0x8.
- **Native node (owner, 01:50-01:51, pid 12940, build 2026-09-21.2): RUNS end to end.** Three
  prompts, styles 0/1/2 = Default / Natural / Cinematic: `Init_ProjectID`, `GetCapabilityParameters`,
  snippet `Init_Ext`, `CreateFeature` and `EvaluateFeature` all `hr=0x00000001`; recipe line
  `0x4 (ALLOW_UNORDERED_ACCESS), initial state UNORDERED_ACCESS`; contract 2368x1760 quality 5,
  surfaces RGBA16F colour/output + R16G16_FLOAT mvec + R32_FLOAT depth inverted, inputs
  NON_PIXEL_SHADER_RESOURCE. **The three modes produce visibly different images** (~1.6-1.9 s per
  prompt). Owner: "it actually runs and produces unique images for each mode!"
- **Per-prompt re-init (measured, not a defect):** every prompt runs the whole NGX init again, with no
  `Shutdown1` between prompts. ComfyUI instantiates a NEW node object per prompt, so the size-keyed
  session (dll, size, preset) is rebuilt by the node lifecycle, not because of a cache miss; the ~1 s
  is the provider/CreateFeature cost, not the frame.
- **Owner-forwarded follow-ups (Claude Sonnet 5, same day)** — recorded as the next work, not as done:
  (1) regression tests: the `_fwd_stub` address and the whole flag table are already pinned; add a
  smoke test on a synthetic image (output differs from input, no NaN, in range); (2) a ~50-prompt soak
  watching VRAM and process handles (per-prompt re-init ~1 s; reuse the size-keyed session only if that
  re-init is unintended); (3) keep the literal-name staging rule (canonical `nvngx_dlssnr.dll` staged
  copy + the sibling-name warning); (4) still-image depth A/B: flat zero depth (current) vs a real
  estimated depth map for `DLSSNR.Depth` - note the native node has NO depth socket today (the guide is
  zero-filled), so that A/B needs an optional depth input first. Sonnet:
  "I wouldn't revert one to find out" which of the two shipped bugs caused the original C++ exception -
  both were masked by silent behaviour, and removing the silence is what enabled progress.
- **Shipped in the same commit** (the flag fix itself is the entry above):
  - **First-frame output smoke test** — pure `rgba_bytes_from_rgb8` + `native_output_verdict`, and
    `DlssNrSession.evaluate(..., check_anomalies=True)` + `fp16_anomalies()` which counts NaN/Inf and
    out-of-range values in the RGBA16F READBACK, i.e. before the host clamp would hide them. The node
    logs one line per prompt: error on non-finite, warning on a saturated readback or on an output that
    is byte-identical to its input. One whole-payload pass on the first frame of a prompt only; never
    raises (intensity 0 is a legal no-op).
  - **Soak instrument** — `ANTS_NR_SOAK=1` prints one line per prompt with the process handle count
    (`win32.process_handle_count`, `GetProcessHandleCount`, None off-Windows), torch VRAM
    allocated/reserved and the session's frame counter; both numbers should stay flat over the soak.
  - **Opt-in cross-prompt session cache** — `ANTS_NR_SESSION_CACHE=1` keeps the size-keyed session (and
    its device) across prompts (`_NR_SESSION_CACHE`, a key -> (session, gpu) dict), so the NGX init is
    paid once per size/dll/preset instead of once per prompt; default OFF because per-prompt init is
    what the working run used, and the knob exists to measure the ~1 s. Lifecycle: a session that is
    closed is evicted from the cache (`_forget_cached`, called from both `_close_native` and
    `_drop_session`) so a closed feature can never be reused, and the dict means two sizes/engines in
    one workflow never close each other's feature (pinned by test).
- Suite: **461 checks** (dlssnr_bridge 110, dlsssr 108, native_flow 58, runtime_surface 58,
  nr_schedule 33).

### 2026-09-21 (owner request) — the SR pre-denoise stage now RUNS, and it runs on BOTH engines
- **What was wrong** (three invisible things): (1) the stage was INERT - both plan builders
  (`ants/dlssnr/node.py` single pass, `schedule.build_pass_plan`) wrote `denoise_strength = 0.0`
  whenever no denoise MODEL was connected, and the SR gate is `strength > 1e-4`; picking
  "SR (DLSS denoise)" - the DEFAULT - with no model therefore ran nothing, which is why the SR stage
  was never A/B-able; (2) on a legacy engine the node logged "SR pre-denoise needs the native NGX
  engine - using the denoise_model input for this run" and fell back to the model path; (3) with a
  schedule, the pass-list log line then ran `next(spec["denoise_model"] ...)` without a default - a
  legacy run with a strength would have raised StopIteration there.
- **Implemented**: ONE selection rule, `pre_denoise_action(mode, strength, has_model)` -> "sr" /
  "model" / None, used by the pass list AND all three engine paths (native, legacy CUDA, legacy host
  staging). SR mode needs no model (strength is only the gate); the stage is OUR `ants/dlsssr` 1:1
  DLAA host, so it runs before whichever NR engine is selected. `build_pass_plan(..., sr_stage=...)`
  keeps the main widget strength so schedules carry the gate too; `_sr_denoise_frame` takes an
  explicit output device (`cuda:<ordinal>` on the CUDA path, CPU on host staging via the new
  `_sr_denoise_np`); `_close_native` drops the SR sessions together with the device they live on (a
  session must never outlive its device); the pass-list line is engine-explicit ("1:1 DLAA before the
  native NGX / legacy engine").
- **Behaviour to expect on the rig**: with the default mode (SR) the 1:1 DLAA pass now RUNS on every
  prompt, on both engines. It needs `nvngx_dlss*.dll` in `models/DLSS/SR/` (a loud error names the
  path if missing). A/B = mode OFF vs SR; strength 0 also disables it by the same rule.
- **Untested on the rig yet**: the SR stage has never run inside a prompt, and on the legacy engine
  our SR session now shares the process with the neuroframe engine's own NGX session. First rig run:
  small frame, native first, then legacy; if the legacy engine misbehaves with the stage ON, mode OFF
  reproduces the previous behaviour exactly.
- **Still-image depth A/B: TABLED (owner, same message)** - our inputs are finished renders /
  AI-generated images, and no render-pass-grade depth exists outside render-time apps (which already
  ship their own DLSS5 NR path). Revisit only if a real depth source appears; it would need an
  optional depth socket on the native node first.
- Suite: **468 checks** at that commit (dlssnr_bridge 112, dlsssr 109, native_flow 60,
  runtime_surface 58, nr_schedule 35).

### 2026-09-21 (rig run 30) - SR init, round 2: the caller geometry + the ONE NGX context
**Rig facts (owner's 02:48 retry of `5c1b8f3`)**: no crash any more - the fault is gone and the
stage fails LOUDLY (loud `DlssSrError` + traceback, process survives, prompt finishes in 0.40 s).
But SR still could not init, on both routes:
  1. route 1 (staged `nvngx_dlss.dll` 58 956 912 B as the app-facing module, direct) ->
     `NGX init <- failed (last hr=0xBAD00002)`;
  2. route 2 (driver core alone) -> init OK, `CreateFeature(feature 1) <- hr=0xBAD0000B`, with the
     core's own log line `[NVSDK_NGX_CreateFeature_Validate:729] app id is 141959980` (= 0x876232C,
     the NR session's CMS id from prompt 1) and NO path scan / config load at all.
**What that means (Claude Sonnet 5 round 3, corroborated by the core's log)**: NGX has ONE context
per process. The core's FIRST `Init` pins the app id and the feature-library search paths; every
later init (or a second Init_Ext) only re-uses that context - it cannot add a folder. Prompt 1's NR
init had searched only its own staged dir + `ProgramData\NVIDIA\NGX\models` + `python_embeded`,
so the SR stage in prompt 2 had no way to register `nvngx_dlss.dll`.
**Label correction (Claude's catch, verified against `nvsdk_ngx_defs.h`)**: `0xBAD0000B` is
`Fail|11 UnableToInitializeFeature` ("feature misconfigured or not available on the system"), NOT
`FeatureNotSupported` (= `Fail|1`/`0xBAD00001`). Our old hand-written table was wrong on three of
its four entries (`0xBAD00003` = FeatureAlreadyExists, not InvalidParameter; `0xBAD0000C` =
OutOfDate, not "PlatformNotSupported"). It is now the full header table, decoded by
`ngx.ngx_result_name()` everywhere a code is printed.
**Fixed in code**: (a) the complete result-name table; (b) ONE geometry for every ANTs session -
`ngx.feature_search_paths()` unions the caller's folders, every staged feature library under
`models/DLSS/staged` (the SELECTED SR build is staged on demand by
`discovery.ensure_staged_sr_dir()`, whose choice is recorded by the nodes through
`discovery.remember_sr_choice()`) and NVIDIA's models dir; the SR stage now inits with the same app
id (`NR_APP_ID`) and, on the core lane, the same project id as NR; (c) `_note_geometry()` prints the
first init's identity + path list and WARNs loudly when a later init differs (app id / paths);
(d) the SR ladder gained the one caller geometry neither run had tried: the runtime as the
app-facing module called THROUGH THE CALLER SHIM in the public order (`owner_via_shim=True`) -
run 30 proved direct+public is refused with `0xBAD00002` and run 29 proved shim+swapped faults
reading the version constant (0x15) out of the feature-info slot.
- Suite: **473 checks** at that commit (native_flow 65, dlsssr 109, dlssnr_bridge 112,
  runtime_surface 58, nr_schedule 35). `HOST_BUILD` `2026-09-21.6`.

### 2026-09-21 (rig run 31) - the union paths WORK; the SR ladder is reordered onto them
**Prompt 1 (native NR, no SR) of the 03:49 run proved the run-30 fix**: the very first NGX init
logged its identity and its three search paths - the NR staged dir, the SR staged dir
(`...\staged\ANTs\sr_staged\nvngx_dlss_310.9.1`) and `ProgramData\NVIDIA\NGX\models` - and the
core's own log then showed what that buys: `NGXSecureLoadFeature` validated the staged
`nvngx_dlss.dll`, loaded the DLSS snippet from its managed cache and recorded
`app 876232C feature dlss snippet: ... version: 310.9.0` for OUR app id. The provider
`CreateFeature(1)` needs was registered during the first init. (Note: the core served 310.9.0
from `ProgramData\NVIDIA\NGX\models\dlss\versions\...` - its config pins `app_E658700=310.9.0`
- so the model the DLAA pass runs is the core's managed one, not necessarily the staged build.)
The NR prompt itself was healthy end to end (Init / Init_ProjectID / snippet Init_Ext /
CreateFeature(18) / EvaluateFeature all `hr=0x1`, 3.98 s).
**Prompt 2 (legacy engine + SR) then failed on BOTH routes, and the failures are informative**:
  * route 1 (the SR runtime as the app-facing module, called DIRECTLY in the public order) ->
    `NGX init <- failed (last hr=0xBAD00002)` - the refusal runs 30 and 31 both show;
  * route 1b (rig 31's new geometry: the same runtime THROUGH the caller shim, public order) ->
    FAULTED inside the runtime: `access violation writing 0x0000000001E73BF0`, black box naming
    `[in-flight call: Init_Ext]`; the loud `DlssSrError` + RESTART text did its job and the
    process survived (`Prompt executed in 0.56 seconds`).
**What was fixed (this commit)**: the ladder is reordered onto the geometry the core log
endorses and the measured dead ends are opt-in: (1) the DRIVER CORE leads, with the union search
paths; (2) the runtime as the app-facing module, direct, public order (its refusal is an ERROR,
so the ladder steps over it - it never faults there); (3) the runtime through the caller shim,
now `ANTS_SR_OWNER_SHIM=1` only (rig 31 measured the fault; a fault stops the ladder by design,
so it must not be a default route); (4) snippet-direct stays `ANTS_SR_SNIPPET_DIRECT=1`.
The fault message now names the route and both fingerprints (read of 0x15 = swapped snippet
ABI; write to a low address = shim-owner geometry).
**Open question this run raised**: in the LEGACY path the bridge loads (and its NGX work shows
in the log) BEFORE the SR stage creates its session - `DLSS-5 Bridge initialized` is logged
ahead of `[ANTs] SR stage:`. In the 03:49 runs our own prompt-1 init pinned the context first,
so the bridge inherited the union; a FRESH process whose first prompt is the legacy engine may
instead be pinned by whatever the bridge inits with. The tell is our own first-init line and the
core's `SnippetLocationInfo ... Module not found at` lists in `nvngx.log`. If the legacy SR run
fails in a fresh process while the SR-first run passes, the fix is to prime the NGX context
with the union (a core `Init` + capability map, no feature) before `load_bridge` in the legacy
path - tracked in plan.md, not done yet.
- Suite: **475 checks** (native_flow 67, dlsssr 109, dlssnr_bridge 112, runtime_surface 58,
  nr_schedule 35). `HOST_BUILD` `2026-09-21.7`. Rig confirmation of the core-led ladder is
  PENDING - the next SR prompt is the test.

### 2026-09-21 (rig run 29) - the SR pre-denoise stage FAULTED at init; the route is fixed
- **Owner's A/B**: `pre_denoise_strength` 0 = "ran as usual" (the rule skips the stage); strength 1
  died in the SR session init. Two nested causes:
  1. the primary route asked the DRIVER CORE alone to create feature 1 -> `0xBAD0000B`
     (name corrected in the run-30 entry: `Fail|11 UnableToInitializeFeature` - the feature is
     not available in the context the core holds: a core has no provider module for a feature
     nobody registered, so the core-alone route cannot create feature 1);
  2. the fallback then loaded `nvngx_dlss.dll` as a snippet with the SWAPPED `Init_Ext` order (the
     order `nvngx_dlssnr.dll` wants) - ctypes reported
     `OSError: exception: access violation reading 0x0000000000000015`, and `0x15` IS
     `NGX_VERSION_API`: the runtime dereferenced our version constant as the `FeatureCommonInfo`
     pointer. The SDK runtime is called in the PUBLIC argument order.
- **Fixed** in `ants/dlsssr/sr.py` as an explicit ladder:
  route 1 (default) = the staged runtime is the session OWNER and the feature provider, called in
  the public order (`Init_Ext(appId, path, device, sdkVersion, featureInfo)`), capability map from
  the runtime, driver core preloaded for presence (`preload_core=True` - the reference host's loader
  order); route 2 = the driver core alone with the SR search path (kept for sets where the core does
  own the SR implementation); route 3 = the NR-style swapped-ABI snippet route, now OPT-IN
  (`ANTS_SR_SNIPPET_DIRECT=1`) because it was the thing that faulted.
- An NGX *error* moves to the next route; a FAULT stops the ladder and raises ONE loud `[ANTs]`
  error naming the file, the access violation and "RESTART ComfyUI", with `pre_denoise_mode OFF` /
  `sr_strength 0` as the way out. The runtime file, its size and its export verdict (probed from the
  export table - never the file name) are logged before any call goes into it, and the crash
  instrumentation is armed on EVERY route (it used to be gated to the snippet routes).
- Suite: **468 checks** (dlsssr 109, native_flow 60, dlssnr_bridge 112, runtime_surface 58,
  nr_schedule 35).

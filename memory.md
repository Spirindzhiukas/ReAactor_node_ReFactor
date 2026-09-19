# memory.md — Persistent Project State (living ledger)

Update this file in the same commit as any state-changing work. Rules of the project
live in `CLAUDE.md`; the active checklist lives in `plan.md`.

## Snapshot

- **Version:** v1.1.0-alpha1 · branch: `arena/01a0b5e0-reaactor-node-refactor` (work branch;
  `main` moves via PR merge)
- **Head at last update:** GPU acceleration commit (on top of `588f790` DLSS5 hybrid,
  `88305cb` pre-pass/rebrand)
- **Suite:** ALL GREEN — 295 checks + gates (details below, fixes after)
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
- RIG run 11 = **NGX CONVERSATION LIVE**: SR path passed Init_Ext +
  AllocateParameters + CreateFeature THROUGH THE SHIM on the real driver
  core (caller-check satisfied - no fault); core ANSWERED 0xBAD0000B
  (FeatureNotSupported) for feature 1 -> most likely the core refusing a
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

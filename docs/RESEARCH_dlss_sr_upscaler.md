# Research: regular DLSS Super Resolution (upscale modes + J/K/L/M) for the DLSS5 node

*Owner question: can we drive the regular DLSS upscaler (modes x1/x1.5/x1.724/x2/x3 +
DLAA, the J/K/L/M model presets) from our node, storing its DLL in the same
`models/DLSS/` tree — and which DLL is the upscaler anyway?*

## 1. The owner's ReShade confusion — resolved

The J/K/L/M presets do **not** live in the DLSS-NR runtime we drive today
(`nvngx_dlssnr.dll`, feature 18). They belong to the **regular DLSS Super
Resolution dll — `nvngx_dlss.dll`** (feature 1: SR + DLAA; the DLL DLSS
Swapper swaps). ReShade-side tools can "control presets" because they inject
into a process that already hosts NGX and can set the preset parameter
(see §3) — which is exactly what we could not do inside the NR-only ABI.

## 2. Which DLL does what (owner asked for the pointer)

| File | Role | Needed for the SR node? |
|---|---|---|
| **`nvngx_dlss.dll`** | **DLSS Super Resolution + DLAA (feature 1) — THE upscaler**, J/K/L/M weights inside | **YES — user-procured, never bundled** (same license situation as nvngx_dlssnr) |
| `_nvngx.dll` + `nvngx.dll` | NGX core + loader shim — **shipped inside the NVIDIA display driver** (`C:\Windows\System32\DriverStore\FileRepository\nv*.inf_amd64_*\`, older systems also `System32`) | NO — already on the owner's system; the host LoadLibrary-exists it from there; never redistributed |
| `nvngx_dlssnr.dll` | Neural Rendering runtime (feature 18) — what our current node drives | already in place |
| `nvngx_dlssd.dll` / `nvngx_dlssg.dll` | Ray Reconstruction / Frame Generation | not needed |

**Get: `nvngx_dlss.dll`** (DLSS Swapper, TechPowerUp driver packages, or a
game folder). Storage: `ComfyUI/models/DLSS/dlss_<version>/nvngx_dlss.dll` —
sibling convention to the `dlssnr_<version>/` sets (any filenames accepted
there; discovery extended when the node lands).

## 3. The preset mystery — solved (host-settable)

`nvngx_dlss.dll` reads the NGX parameter
**`NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset`** (per quality-mode variants
`DLSS.Hint.Render.Preset.DLAA/.Quality/.Balanced/.Performance/.UltraPerformance`;
the DLAA variant only applies when input size == output size). Enum from
NVIDIA's SDK header (`NVIDIA/DLSS`, `nvsdk_ngx_defs.h`):
`Default=0, A..F=1..6 (CNN era), J=10, K=11, L=12, M=13, N=14, O=15 (reserved)`.
**Which model a preset letter maps to is a property of the loaded
nvngx_dlss.dll** — so our artist naming (Transformer I · Crisp/Stable,
Transformer II · Quality/Fast) gets applied to a *real* mechanism once we
host NGX. This also settles §8 of RESEARCH_dlss5_hybrid.md: the "flip"
trigger is our own NGX host, not a future neuroframe ABI change.

Upscale modes = the host picks output dims + `PerfQualityHint`:
DLAA 1.0x, Quality 1.5x, Balanced ~1.724x, Performance 2.0x, Ultra
Performance 3.0x — then we rescale back to the input resolution with a
pixel interpolation of choice (lanczos/bilinear/bicubic/nearest — comfy's
`common_upscale`), the owner's proposed SR-then-downsample detail pass.

## 4. Feasibility — proven, and without a compiled bridge

**Reference implementation: HicirTech/DLSS-Video-Transcoder (DVT).** It
drives NGX entirely through Bun's FFI — `LoadLibraryExW` on the driver's
`_nvngx.dll` core (+ the `nvngx.dll` loader shim found next to it) — with
its own TypeScript D3D12 COM bindings (`src/native/d3d12.ts`), and its CLI
does **`sr <in.png> <out.png>` — real DLSS SR on still images**, presets
included, plus `nr` (feature 18, same-size enhance — our current node's
job) and `fg` (frame gen). Runtime DLLs are user-supplied, never
redistributed (matches our policy exactly).

Implication: an NGX host is **plain flat-C DLL calls + D3D12 COM plumbing —
no C++ toolchain required**. Python can do this with `ctypes`
(+ `comtypes`/manual vtables for the D3D12 device, textures, residency).
That is the implementation route for us:

1. `ants/dlsssr/` — pure-Python NGX host: locate `_nvngx.dll`/`nvngx.dll`
   (System32 → DriverStore scan, newest wins), bind the NGX C exports,
   create a D3D12 device, wrap torch-CUDA or D3D12 resources as NGX
   parameters, `NGX_EvaluateFeature(SuperSampling)` with our mode/preset
   params. Zero-MV still-image feeding (the DVT-proven pattern: static
   "sequence", reset on the first frame; expectations set in §5).
2. Node surface (after the host works): `sr_upscale_mode`
   (DLAA/Quality/Balanced/Performance/Ultra Performance), `sr_preset`
   (artist names, per §3), `sr_return_interpolation`
   (lanczos/bilinear/bicubic/nearest — output always resized back to the
   input resolution), sharing `models/DLSS/` discovery and the loud-[ANTs]
   error style. Runs standalone (as a second stage) and as an optional
   pre/post stage inside the DLSS5 enhancer's pass plan.
3. Caveat to respect: DLSS SR is a temporal feature (motion vectors +
   jitter). Stills work via zeroed MVs (DVT does it), but quality on clean
   generated frames must be A/B'd on the owner's rig before we promise
   anything.

## 5. Honest expectations

- SR-then-downsample-to-input can add real micro-detail (it is effectively a
  learned detail-recovery pass), but it is not magic: zero-MV stills forfeit
  the temporal accumulation that makes DLSS shine in games.
- Preset letters only mean anything for the weights inside the installed
  `nvngx_dlss.dll` — old DLLs ignore/absent newer letters; the probe script
  reports the DLL's version so the owner knows what he loaded.
- First run of any NGX feature compiles/loads weights — expect a slow first
  frame; per-run host init cost is the price of not being a game.

## 6. Probe now, build next

`tools/probe_dlss_rig.py` (this commit, stdlib-only, Windows) reports on the
owner's rig: NGX core presence (System32 + DriverStore, newest), any
`nvngx_dlss*.dll` under `models/DLSS/` with version resources, and the
neuroframe engine's exports (so we also learn whether its frame path could
give NR-side upscaling — `process_cuda_video_frame` has separate output dims
in Merserk's driver, formats NV12/P010, RGBA8 in the descriptor set — as a
bonus route with zero new host code).

## 7. Recreating the neuroframe pair in pure Python (owner question) — verdict: yes, phased

What the pair actually does: `neuroframe_caller.dll` is an ABI shim;
`neuroframe_engine.dll` is the real work — a D3D12/NGX host that initializes
feature 18 (NR) with the `nvngx_dlssnr.dll` from the folder it is pointed
at, feeds frames (HOST pointers or CUDA-imported buffers), calls
`NGX_EvaluateFeature`, and returns results. That is *exactly* the job the
DLSS-Video-Transcoder project already proves doable in pure FFI (it hosts
THREE NGX features — SR, NR, FG — from TypeScript with hand-rolled D3D12 COM
bindings). Python has the same primitives (ctypes + COM vtable structs or
comtypes; torch/CUDA for the buffer path).

Plan (phased, per the owner's control-and-maintainability goal):

1. **Build the pure-Python NGX host for SR first** (`ants/dlsssr/`): it
   proves the D3D12 device/resource/residency plumbing and NGX
   parameter/evaluation flow on the owner rig with the simplest feature
   (still-image SR; the DVT-verified pattern). This is the prerequisite
   step already in plan.md.
2. **Port feature 18 onto the same host** (same code path, different
   feature id + parameters): from then on the neuroframe pair is an
   OPTIONAL legacy path — discovery keeps supporting it, but our own host
   gives full control of parameters, ABI, updates, per-feature presets and
   logging, and removes the 3rd-party-DLL dependency entirely.
3. Non-goals: FFmpeg/NVENC video pipelines (out of ComfyUI scope).

Owner-rig facts (probe v2 run): NGX core present (DriverStore
`nvmdsi.inf_amd64_05d1e242e80cf105`, core 32.0.16.1692 + loader
30.0.14.9516) — SR hosting is GO; `nvngx_dlss.dll` 310.9.1.0 (DLSS 4.5-era:
J/K/L/M presets all present); neuroframe engine 1.4.0.0 with the FULL
v1-v6 export family (incl. `process_frame_v6` — separate output dims, i.e.
NR-side upscaling is available to probe — and `dlss5nr_shutdown`, which we
should call on bridge teardown); a RenoDX-tuned NR build 310.8.SF.0 is in
use. Merserk's repo may carry newer engine builds than the Gourieff zip —
moot once step 2 lands.

# Research: DLSS5 enhancer hybrid (OreX vs neuroframe) + HDR Colour Bridge

*Owner-requested investigation behind the v1.1 DLSS5 node upgrade. Status:
hybrid implemented (same node, upgraded); findings below.*

## 1. The two implementations compared

| | Ours (inherited base) | OreX ([ComfyUI-DLSS5-orex](https://github.com/orex2121/ComfyUI-DLSS5-orex)) |
|---|---|---|
| Bridge | Gourieff-distributed helper pair `neuroframe_engine.dll` + `neuroframe_caller.dll` (author Merserk), ctypes ABI v6, HOST-memory float frames | Own C++ `dlss5nr_bridge.dll`, **in-process D3D12/NGX**, prebuilt in repo, C++ sources included |
| Data path | numpy float32 → helper DLL (CPU staging, helper talks to GPU) | CUDA/D3D12 external-memory interop — pixels never leave VRAM when already on GPU; embedded PTX kernels, no CUDA toolkit; CPU fallback |
| Upscaling | none (frame enhancer at native size) | `1x (DLAA) … 3x (Ultra Performance)` modes |
| Temporal | per-frame reset (no history management) | `auto` (scene-change threshold + warmup) / `none` |
| Batches/video | per-image loop | chunked processing (memory-bounded), VIDEO node |
| Unique controls | color_strength, tone_preservation, face_skin_protection, grain_preservation, nr_passes, shimmer, auto_mask+MASK socket, **dll_version multi-set selector** | style/intensity/tone/structure/skin, auto_mask, upscaling_mode, model preset, temporal, gpu_mode, chunk_frames, JS single-frame preview |
| Maturity | stable, feature-frozen | actively developed (v0.9.x changelog culture), EN/RU docs, MIT |

**Verdict (owner asked):** OreX is architecturally superior (in-process
bridge, zero-PCIe interop, upscale modes, temporal management, chunking,
active maintenance). Our node's superiority is in the *control surface*:
the neuroframe helper exposes parameters OreX's bridge does not
(color_strength, tone_preservation, face/grain protection, NR passes,
MASK input) — and our multi-version DLL selector stays unique. Hence the
hybrid: keep our helper path + controls, adopt OreX's *adoptable-at-Python-
level* ideas, credit him.

### Adopted in v1.1
- **Temporal history management** (OreX-style): `Auto (scene-aware)` with a
  scene-change threshold / `Continuous` / `Per-frame reset` — the helper's
  `reset` flag finally gets used properly (was hardcoded True every frame).
- **DLL-set pragmatism**: any filenames in a set; engine found by probing
  exports (`dlss5nr_init`), not by name — so OreX-style renamed sets load.

### Not adoptable without changing the native bridge (documented, not done)
- Upscale modes (1x–3x), CUDA interop, chunking-in-VRAM, VIDEO node: these
  live inside OreX's own bridge DLL; the neuroframe helper's ABI has no
  upscale/motion-vector entry point. A true merge would mean building a new
  native bridge — a separate project (OreX's C++ sources under `bridge/`
  would be the starting point, MIT).

## 2. DLL location policy (owner decision, implemented)

```
ComfyUI/models/DLSS/dlssnr_<version_name>/   <- enforced, ANY .dll filenames
ComfyUI/models/DLSS/ (flat)                  <- one unnamed set
models/dlssnr/... + package dll/             <- legacy fallbacks (graceful)
```

`nvngx.dll_comfy.dll` practice (OreX ships a project-owned caller): our
equivalent is the neuroframe pair from
[Gourieff's dataset](https://huggingface.co/datasets/Gourieff/ReActor/tree/main/DLSSNR).
**Difference vs OreX's single file:** OreX's `nvngx.dll_comfy.dll` is his own
caller shim that loads the NVIDIA runtime in-process; the neuroframe pair
splits the same job into caller (API shim) + engine (D3D12/NGX host with
HOST-memory entry points `dlss5nr_init`/`dlss5nr_process_v6`). They are two
implementations of the same concept, not interchangeable: OreX's bridge
expects his own ABI; the neuroframe engine exposes the v6 struct our node
drives. Neither replaces the other; both still need the user-supplied
`nvngx_dlssnr.dll` next to them.

## 3. HDR Colour Bridge (implemented)

Both current pipelines inherit the first-generation RenoDX approach
(`renodx-dlss5.addon64`; colour composition by clshortfuse, MIT — see
OptiScaler_DLSSNR's credited port for the modern form). Implemented in
`rfactor/dlssnr/hdr_bridge.py` (pure numpy, post-bridge, unit-tested):

- **Classic (Paper-White Gain)** — default: sRGB→linear → gain by
  `scene_paper_white_scale` (default 1.0) → extended-Reinhard shoulder
  kneeing at `diffuse_white_nits` (default 220) → chroma preserved around
  luma by `color_strength` (default 1.0) → transfer blend by
  `hdr_transfer_strength` (default 1.0). Owner note: the original defaults
  (2.537 / 237 nits) were game-engine reference values and overbrighten
  regular 8/16-bit images — at 1.0 / 220 the bridge is neutral until pushed.
- **Anchored (Auto White Point)** — secondary mode: the shoulder's white
  point anchors to the frame's measured highlight exposure (95th-percentile
  luma + bias) — the single-image analogue of RenoDX's exposure-scan
  anchoring (multi-point anchoring design: see OptiScaler_DLSSNR
  `dlssnr/design/multi-point-anchoring.md`). `black_lever` then restores the
  shadow floor the gain lifted.

## 4. "Denoise before upscaling (pre-SR)" — shipped via the comfy upscale-model pipeline

There is **no separate NVIDIA pre-SR denoise DLL** in the DLSS5 NR stack:
denoising is part of what `nvngx_dlssnr.dll` itself does (NR = neural
rendering with temporal denoise, driven by motion vectors/history). The two
"native" denoiser routes were evaluated and rejected:

- **OIDN** — trained on Monte-Carlo path-tracing noise patterns, which
  generated images usually lack; near-useless here (the owner runs OIDN
  separately, with a noise injector that simulates path-tracing noise, for
  the renders that actually need it).
- **OptiX denoiser (driver-shipped)** — its guide buffers (albedo/normal)
  are in fact optional since OptiX 7.x, so "needs render passes" is only
  half-true; the real blockers are (a) invocation through the OptiX
  device-side ABI — no ctypes-callable export, we would have to ship a
  compiled CUDA/OptiX host program (the OreX-native-bridge class of effort),
  and (b) the model is still MC-noise-domain, same mismatch as OIDN.

Shipped instead (owner's idea): **reuse the comfy Upscale-Model pipeline as
the pre-SR denoise stage** — `ANTsUpscaleModelLoader` → optional
`denoise_model` socket on the DLSS5 node + `pre_denoise_strength` blend.
1x pure-denoise/restoration models are a perfect fit: **SCUNet** (1x,
`scunet_color_real_psnr/gan`) and **PureScale2 `1x_PureVision`**
(limitlesslab, ESRGAN-pixel-unshuffle restoration model trained on
compression artifacts + moderate noise, explicitly intended "as a
preparatory step before upscaling"). The stage runs through our
comfy-core-mirrored `rfactor/upscaler.py` (spandrel loading, tiled
inference, OOM tile-halving), keeps the frame resolution invariant (non-1x
model outputs are resized back), works on-GPU in the CUDA path, and blends
by strength. Credit: limitlesslab (PureScale), Zhang et al. (SCUNet).

## 5. Credits

- **OreX** — ComfyUI-DLSS5-orex: temporal-management design adopted; bridge
  architecture reference. MIT.
- **clshortfuse (ShortFuse/Carlos Lopez)** — RenoDX; the HDR Colour Bridge
  concept and Classic parameter set (Diffuse white 237 nits, Scene
  Paper-White Scale, HDR Transfer Strength, Color Strength). MIT.
- **Merserk** — neuroframe helper DLLs (our engine), via Gourieff's
  distribution. NVIDIA `nvngx_dlssnr.dll` remains user-supplied (license).

## 6. GPU acceleration (owner measurement → implementation)

Owner measurement on RTX 4090 / 5950X: host-staging was ~20× slower than OreX GPU-ON at
default settings and ~100× at 4K with `nr_passes = 4`. Root cause: we called only the
engine's HOST entry (`dlss5nr_process_v6`, numpy RAM pointers) and force-uploaded every
frame from the CPU (per-frame `.cpu().numpy()` → process → back to torch).

Facts from Merserk's own driver (`dlss5-visual-enhancer`, `src/core/neural_bridge.py`,
the engine's reference client):

- The engine exports a CUDA device-pointer variant `dlss5nr_process_cuda_v6(src_dev,
  dst_dev, w, h, mask_dev=0, params*, err, n)` plus `dlss5nr_cuda_supported()` /
  `dlss5nr_cuda_status()` / `dlss5nr_gpu_name()` / `dlss5nr_rebind()`.
- Masks travel as HOST memory inside the params struct even in CUDA mode (the dedicated
  mask device-pointer argument is passed as 0).
- The engine runs on the CUDA **primary context** — the same context PyTorch uses — so
  torch CUDA tensor pointers can be passed **directly** (no driver-API plumbing, no
  staging buffers needed at all; one step better than Merserk's own numpy pipeline).

Implementation (v1.2): `gpu_acceleration` widget — `Auto (GPU when available)` (default) /
`Force GPU (CUDA)` / `CPU (host staging)`. GPU path: whole batch staged to
`cuda:<ordinal>` once (no copy if already VRAM-resident), per-frame device pointers into
`process_cuda`, `torch.cuda.synchronize()` to publish results, HDR bridge executed
**on-GPU** via the bridge's new torch backend (one math path behind op shims, unit-tested
on numpy). Host path kept as fallback, now with one-time batch conversion and a reused
destination buffer. Engine capability is probed at load (`cuda_available()`); missing
exports or a failed probe produce a loud `[ANTs]` message naming the fix (update
`neuroframe_dlls.zip` or switch to CPU mode).

Sandbox limitation: huggingface.co is TLS-blocked from the dev sandbox, so the exact
export table of Gourieff's distributed zip could not be inspected here — the code probes
at runtime instead, which also covers older engine builds gracefully.

Future option: the engine also exports `dlss5nr_scene_score_v1` — a native scene-change
score that could replace our numpy thumbnail heuristic for temporal Auto mode.

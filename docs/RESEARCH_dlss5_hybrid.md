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
  `scene_paper_white_scale` (default 2.537) → extended-Reinhard shoulder
  kneeing at `diffuse_white_nits` (default 237) → chroma preserved around
  luma by `color_strength` (default 1.0) → transfer blend by
  `hdr_transfer_strength` (default 1.0).
- **Anchored (Auto White Point)** — secondary mode: the shoulder's white
  point anchors to the frame's measured highlight exposure (95th-percentile
  luma + bias) — the single-image analogue of RenoDX's exposure-scan
  anchoring (multi-point anchoring design: see OptiScaler_DLSSNR
  `dlssnr/design/multi-point-anchoring.md`). `black_lever` then restores the
  shadow floor the gain lifted.

## 4. "Denoise before upscaling (pre-SR)" — investigated, not implementable

There is **no separate NVIDIA pre-SR denoise DLL** in the DLSS5 NR stack:
denoising is part of what `nvngx_dlssnr.dll` itself does (NR = neural
rendering with temporal denoise, driven by motion vectors/history). The
ReShade "Denoise before upscaling" UI entries belong to addon/shader stacks
(ReShade denoise shaders or third-party denoisers feeding the NR input), not
to the NGX runtime. In our single-image context temporal denoising is
inherently limited (no motion vectors); the honest levers are the helper's
`nr_passes` (already exposed) and, if ever wanted, an external CPU denoiser
(e.g. OIDN) as a new dependency — not planned.

## 5. Credits

- **OreX** — ComfyUI-DLSS5-orex: temporal-management design adopted; bridge
  architecture reference. MIT.
- **clshortfuse (ShortFuse/Carlos Lopez)** — RenoDX; the HDR Colour Bridge
  concept and Classic parameter set (Diffuse white 237 nits, Scene
  Paper-White Scale, HDR Transfer Strength, Color Strength). MIT.
- **Merserk** — neuroframe helper DLLs (our engine), via Gourieff's
  distribution. NVIDIA `nvngx_dlssnr.dll` remains user-supplied (license).

# Third-party DLLs (enforced location: `ComfyUI/models/DLSS/<CATEGORY>/<version>/`)

DLLs are models — they live under ComfyUI's models tree, one **category**
folder per feature, one folder per version inside, and **any .dll filenames
are accepted** (the engine is identified by its exports at load time, not by
name; OreX-style single-file sets work too).

Categories:

- **`NR/`** — Neural Rendering (the ANTs⚡DLSS5 Frame Enhancer);
- **`SR/`** — Super Resolution (`nvngx_dlss.dll` builds; reserved for the
  upcoming SR feature);
- **`FG/`** — Frame Generation (reserved for a future video feature).

RR (Ray Reconstruction) is out of scope — there is no way to drive it from a
still-image host.

## Neural Rendering sets (`models/DLSS/NR/<version>/`)

1. Create e.g. `ComfyUI/models/DLSS/NR/v1/`
2. Put the bridge/helper DLLs there:
   - [neuroframe_dlls.zip](https://huggingface.co/datasets/Gourieff/ReActor/blob/main/DLSSNR/neuroframe_dlls.zip)
     (`neuroframe_caller.dll` + `neuroframe_engine.dll`), and/or
   - an OreX-style bridge (`dlss5nr_bridge.dll` / `nvngx.dll_comfy.dll`, from
     [ComfyUI-DLSS5-orex](https://github.com/orex2121/ComfyUI-DLSS5-orex)) —
     note: OreX's own bridge targets his node's ABI; ours probes for the
     neuroframe exports, so keep the neuroframe pair for this node.
3. Put your `nvngx_dlssnr.dll`** next to them.

Also scanned (fallbacks): `models/DLSS/dlssnr_<version>/` (the previous
convention), flat DLLs directly in `models/DLSS/` (the selector labels the
set after the `nvngx_dlssnr*` runtime found inside, e.g.
`(models/DLSS - nvngx_dlssnr_RenoDX_4000_series_friendly)`),
`models/dlssnr/<version>/`, and the package `dll/` folder.

<sub>* neuroframe helper author [Merserk](https://github.com/Merserk), [LICENSE](https://huggingface.co/datasets/Gourieff/ReActor/blob/main/DLSSNR/LICENSE-Merserk.txt)
<br>
** Public distribution of this file is prohibited by NVIDIA, [LICENSE](https://huggingface.co/datasets/Gourieff/ReActor/blob/main/DLSSNR/LICENSE-NVIDIA-DLSS.txt)

## Regular DLSS Super Resolution (upscaler) — planned sibling feature

To add regular DLSS SR/DLAA (the x1/x1.5/x1.724/x2/x3 modes with the J/K/L/M
model presets), obtain **`nvngx_dlss.dll`** (the SR feature DLL — DLSS Swapper,
TechPowerUp driver packages, or a game folder) and put it into a set:

```
ComfyUI/models/DLSS/SR/<preset-or-version>/nvngx_dlss.dll
```

The NGX core (`_nvngx.dll` + `nvngx.dll`) is part of your NVIDIA display
driver and is loaded from System32/DriverStore — it is never shipped or
downloaded. Preset letters (J–M) map to models inside the installed
`nvngx_dlss.dll` (old DLLs do not know newer letters). See
`docs/RESEARCH_dlss_sr_upscaler.md` for the design; run
`python tools/probe_dlss_rig.py <ComfyUI root>` to check your rig.

## Recommended folder naming — DLSS model presets as versions

The current `nvngx_dlssnr` runtime exposes **no model-preset parameter**
(verified by OreX against the DLL string table: 61 `DLSSNR.*` parameters,
none for model selection), so a preset = which DLL build you install. Name
your version folders after the preset, with the community/artist names:

| Folder | Preset | Artist name |
|---|---|---|
| `NR/J_Transformer1_Crisp` | J | Transformer I · Crisp (sharpest, a bit more flicker) |
| `NR/K_Transformer1_Stable` | K | Transformer I · Stable (DLSS 4 "Latest") |
| `NR/L_Transformer2_Quality` | L | Transformer II · Quality (heavy, Ultra-Perf default) |
| `NR/M_Transformer2_Fast` | M | Transformer II · Fast (Performance default) |
| `NR/Default` | — | whatever the installed build ships as default |

## Nature vs Natural — settled

The engine's own reference client (Merserk's Visual Enhancer, `runtime.py`)
maps style 1 to **"Natural"** (OreX agrees). "Nature" was a transcription
artifact of the inherited node — the semantics differ (*Natural* = an
unaltered, naturalistic treatment; *Nature* reads as an outdoors scene
style), so the pack displays **Natural** everywhere. The engine ABI int is
unchanged (0 Default / 1 Natural / 2 Cinematic).

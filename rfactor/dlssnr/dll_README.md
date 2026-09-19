## Third-party DLLs (enforced location: `ComfyUI/models/DLSS/dlssnr_<version>/`)

DLLs are models — they live under ComfyUI's models tree, one folder per
version, and **any .dll filenames are accepted** (the engine is identified by
its exports at load time, not by name; OreX-style single-file sets work too).

1. Create e.g. `ComfyUI/models/DLSS/dlssnr_v1/`
2. Put the bridge/helper DLLs there:
   - [neuroframe_dlls.zip](https://huggingface.co/datasets/Gourieff/ReActor/blob/main/DLSSNR/neuroframe_dlls.zip)
     (`neuroframe_caller.dll` + `neuroframe_engine.dll`), and/or
   - an OreX-style bridge (`dlss5nr_bridge.dll` / `nvngx.dll_comfy.dll`, from
     [ComfyUI-DLSS5-orex](https://github.com/orex2121/ComfyUI-DLSS5-orex)) —
     note: OreX's own bridge targets his node's ABI; ours probes for the
     neuroframe exports, so keep the neuroframe pair for this node.
3. Put your `nvngx_dlssnr.dll`** next to them.

Legacy locations (`models/dlssnr/<version>/`, the package `dll/` folder) are
still scanned as fallbacks.

### Recommended folder naming — DLSS model presets as versions

The current `nvngx_dlssnr` runtime exposes **no model-preset parameter**
(verified by OreX against the DLL string table: 61 `DLSSNR.*` parameters,
none for model selection), so a preset = which DLL build you install. Name
your version folders after the preset, with the community/artist names:

| Folder | Preset | Artist name |
|---|---|---|
| `dlssnr_J_Transformer1_Crisp` | J | Transformer I · Crisp (sharpest, a bit more flicker) |
| `dlssnr_K_Transformer1_Stable` | K | Transformer I · Stable (DLSS 4 "Latest") |
| `dlssnr_L_Transformer2_Quality` | L | Transformer II · Quality (heavy, Ultra-Perf default) |
| `dlssnr_M_Transformer2_Fast` | M | Transformer II · Fast (Performance default) |
| `dlssnr_Default` | — | whatever the installed build ships as default |

<sub>* neuroframe helper author [Merserk](https://github.com/Merserk), [LICENSE](https://huggingface.co/datasets/Gourieff/ReActor/blob/main/DLSSNR/LICENSE-Merserk.txt)
<br>
** Public distribution of this file is prohibited by NVIDIA, [LICENSE](https://huggingface.co/datasets/Gourieff/ReActor/blob/main/DLSSNR/LICENSE-NVIDIA-DLSS.txt)
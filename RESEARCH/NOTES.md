# NOTES — what Merserk's stack teaches us about our silent kill

Analysis of the v10.0 source (2026-09-20), aimed at one question: why does the
RenoDX/Merserk `nvngx_dlssnr.dll` die silently at the first evaluate on our
pure-Python D3D12 host, while his host runs the same bytes fine?

## 1. The architecture, end to end

```
python app
  ├─ src/core/neural_bridge.py      (feature 18 = DLSS NR)
  ├─ src/upscale/video/native.py    (RTX Video VSR/HDR)
  └─ src/frame_interpolation/native.py (DLSS-G)
        │  ctypes only; EVERY native call runs on a daemon thread with a
        │  timeout (45 s × passes, cap 180 s); timeout/exception ⇒ the
        │  session is "poisoned" until app restart; buffers are pinned forever
        ▼
compiled bridge dlls (closed source; bin/runtime/...)
  ├─ neuroframe_engine_neural_rendering.dll   (dlss5nr_* exports, ABI 6)
  ├─ neuroframe_engine_frame_interpolation.dll
  └─ (RTX Video engine, rtxv_* exports)
        │  internally: driver-core _nvngx.dll + neuroframe_caller.dll +
        │  nvngx_dlssnr.dll; Init_Ext 0x13..0x20 via the caller shim;
        │  CreateFeature(18); evaluate
        ▼
NVIDIA NGX core + snippet
```

- **Python never touches NGX.** `nvngx_dlssnr.dll` is listed in `paths.py`
  only for file validation — no load, no bind, nowhere in the whole repo.
  The snippet is ALWAYS at least one compiled hop away from python.
- Our host is a configuration that **exists nowhere in his stack**: python
  loading the snippet directly (one hop). Whatever the snippet checks, his
  ecosystem never exercises that path — so "it works in his app" never
  actually tested direct loading.
- Process-wide `threading.RLock` around ALL NGX features; never call
  Shutdown, never unload ("both operations have been observed to wedge").
- DLSS-G is initialized BEFORE feature 18 ("the NVIDIA D3D12 NGX core keeps
  the first feature search path for the lifetime of the process") — and the
  FFmpeg CUDA primary context is created BEFORE NGX loads (driver-ordering
  hang guard).

## 2. What the import listings say (owner's list_imports.bat run)

| dll | imported dlls | funcs | termination imports | NVSDK_NGX_* imports |
|---|---|---|---|---|
| neuroframe_engine_neural_rendering.dll | 5 | 92 | ExitProcess, TerminateProcess | **none** |
| nvngx_dlssnr.dll (snippet) | 4 | 127 | TerminateProcess, ExitProcess | (it IS the impl) |
| neuroframe_caller.dll | 1 | 69 | ExitProcess, TerminateProcess | **none** |
| nvngx_dlssg.dll | 4 | 126 | TerminateProcess, ExitProcess | **none** |
| neuroframe_engine_frame_interpolation.dll | 17 | 110 | terminate (CRT) | **none** |

- **No binary imports any NVSDK_NGX_* function.** Engine (92) and caller (69)
  are kernel32-dominated import tables ⇒ NGX binding is **dynamic**
  (`LoadLibrary` + `GetProcAddress` at runtime). Static import analysis
  cannot see which NGX backend they bind. (Run `list_imports.bat --all` on
  the engine to see the other 4-5 imported dlls — d3d12.dll? nvcuda.dll?
  nvngx.dll? — which narrows it further.)
- The run-21 probe's caller finding ("imports kernel32 only") generalizes:
  the whole bridge layer resolves its targets dynamically.

## 3. Consequences for the silent kill on our rig

1. **Dynamic resolution dodges IAT patches.** Our run-26 traps rewrote the
   static import thunks of the snippet + driver core for ExitProcess /
   TerminateProcess — armed, verified — and the death used NEITHER. Both
   dlls also import those APIs statically, so a *static-thunk* kill was
   already excluded; the listings now add that his toolchain *prefers*
   dynamic resolution anyway. A `GetProcAddress`-based kill would have
   dodged our IAT patch by construction.
2. **The ntdll detour sits beneath every one of those paths** —
   `ExitProcess`, `TerminateProcess`, dynamic or static, all funnel into
   `ntdll!NtTerminateProcess` — and it shipped AFTER the owner's last fatal
   run. **It has never been on the rig yet.** Run 27 is its first outing.
3. Remaining invisible paths: statically-linked CRT `abort()` → `__fastfail`
   (int 29h) and raw syscalls. If run 27 still dies with no TERMINATION
   line, that's the class — and the next countermeasure is patching the
   snippet's in-memory int-29 instructions to int-3 so the VEH names the
   abort site.
4. **The callback A/B is still unrun.** His python registers no callbacks
   anywhere; our host registers three (env-gated). Runs 22–26 all had them
   ON. Run 27 = `ANTS_NR_RUNTIME_CALLBACKS=0`.

## 4. The CUDA hypothesis (the next big move if run 27 fails)

His NR bridge calls itself the "D3D12/NGX CUDA bridge" (runtime.py
`inspect_runtime_bundle`), the whole video pipeline is CUDA-first (device
pointers end-to-end, DLPack out to the encoder), and the snippet exports a
full `NVSDK_NGX_CUDA_*` family (our resolver found it; import listings show
nothing D3D12-specific). Plausible reading: **feature 18 is evaluated
through the snippet's CUDA backend with plain device pointers — no D3D12
heap/queue/resource choreography at all.** That is directly implementable in
pure python (cuInit → primary ctx → cuMemAlloc in/out + cuMemcpy, then
NVSDK_NGX_CUDA_Init / CreateFeature / EvaluateFeature on the direct-load
path we already have working to CreateFeature hr=1). If run 27 still dies,
run 28 = the CUDA-backend bind; it sidesteps every D3D12-only assumption
the snippet's guard might make about our host.

## 5. Small pins worth keeping

- NR styles: `Default = 0, Natural = 1, Cinematic = 2` ("Natural" naming
  validated at source, again).
- `prefer_nvof` is set to true only when the encode pipeline does NOT use
  NVENC; `nr_passes` 1–4.
- Restart-poison detail markers in his bridge errors: "corrupt", "access
  violation", "device recovery failed/aborted", "device removal",
  "reinitialization failed" — his own host expects AV-class failures too.
- Runtime layout: `bin/runtime/dlssnr/{engine, caller, snippet}`,
  `bin/runtime/dlssg/`; requirements: Win11 D3D12, current driver; DLSS-G
  wants HAGS.

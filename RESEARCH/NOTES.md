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

### 2b. The --all listings: dll-by-dll decode

| dll | non-kernel32 static imports | reading |
|---|---|---|
| neuroframe_engine_neural_rendering (engine) | d3d12:1, dxgi:1, D3DCOMPILER_47:1, ole32:1 | builds its **own D3D12 device** (D3D12CreateDevice) on its **own DXGI adapter enum**; compiles **shaders** (D3DCompile); CoInitializeEx. **No CRT dlls** (statically linked CRT) and **no nvcuda** => the engine's CUDA (every result struct has cuda_result) is loaded **dynamically**. His host = a standalone device owner, exactly like ours. |
| nvngx_dlssnr (snippet) | ADVAPI32:3, USER32:1, VERSION:3 | the classic **gate machinery**: VERSION.dll = GetFileVersionInfo family (**driver/file-version gating by metadata**), ADVAPI32 = registry reads (driver/NGX settings), USER32 single import (MessageBoxW candidate — a *blocking popup* failure mode, or a system-metrics query). 120 kernel32 imports = the dynamic-resolution surface (LoadLibrary/GetProcAddress/...). |
| nvngx_dlssg | ADVAPI32:3, USER32:1, VERSION:3 | same gate shape as dlssnr. |
| neuroframe_caller | (none — kernel32 only) | pure dynamic dispatch shim, 69 kernel32 imports. |
| neuroframe_engine_frame_interpolation | ADVAPI32:3, USER32:1, d3d12:1, dxgi:1, D3DCOMPILER_47:1, MSVCP140/VCRUNTIME140(+1)/api-ms-crt-* (dynamic CRT) | the only CRT-dynamically-linked binary (MSVC /MD build); its only termination import is CRT `terminate`. |

- Earlier finding refined: "0 GPU-gate strings in the 158 MB runtime" stands,
  and now we can see the gate *machinery*: the snippet ships VERSION +
  ADVAPI32 + USER32 plumbing — environment checks done via **file-version
  queries and registry values**, which leave no readable strings.
- The NR engine statically binds d3d12/dxgi (device creation) but resolves
  its NGX *and* CUDA dynamically — one binary, two dynamic backends.

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

## 3b. Name-level listings: the SHIM VERSION-GATE hypothesis (the best fit for every observation)

Key names from the --all dump:

- snippet + dlssg (identical framework): `GetFileVersionInfoA/VerQueryValueA/
  GetFileVersionInfoSizeA` (file-version queries), `RegOpenKeyExW/
  RegQueryValueExW/RegCloseKey` (registry), `VerSetConditionMask +
  VerifyVersionInfoW`, `GetSystemDirectoryW + LoadLibraryW/LoadLibraryExW +
  GetProcAddress + GetModuleHandleA + GetModuleHandleExA`,
  `AllocConsole + GetConsoleWindow + SetConsoleTitleA + WriteConsoleA +
  OutputDebugStringA/W` (self-console diagnostics framework),
  `FindResourceA/LoadResource/LockResource/SizeofResource` (embedded
  resources — snippet only, dlssg lacks them), `SetEnvironmentVariableW`.
  USER32 = `GetWindowThreadProcessId` in both (console self-identification,
  not a message box).
- engine: `D3D12SerializeRootSignature` + `D3DCompile` (its own shaders) +
  `CreateDXGIFactory1` — and NO D3D12CreateDevice: the device is created
  dynamically (Agility pattern). Static CRT; full crash battery.
- caller: pure static CRT + GetProcAddress/LoadLibraryExW + VirtualProtect
  (its per-call slot-repoint mechanics) — consistent with the DVT-parity
  shim pins.

### The hypothesis

The NGX runtime family normally locates the driver core with
`GetModuleHandle("nvngx.dll")` and validates the environment with
file-version queries. In HIS process there is no module named
`nvngx.dll` (the core file is `_nvngx.dll`), so a missing/failing probe
falls through to `LoadLibrary` on System32's real driver nvngx.dll —
which has a valid VERSION resource. In OUR process the name
`nvngx.dll` is DELIBERATELY occupied by our shim (loaded first, by
design) — a hand-built PE **with no VERSION resource**. A
`GetFileVersionInfoSize` on our shim returns 0 / a version gate fails ⇒
deliberate kill (fastfail class — which is why no trap saw it: our IAT
patches were verified armed and dodged).

This fits: deliberate death (runs 24–26), no exception, kill after the
params callback at first evaluate, works in his host, dodges IAT traps.

### Cheap experiments riding run 27, and the follow-ups

1. `set "NVSDK_NGX_LOG_LEVEL=1"` (then 4 if silent) in the launch bat —
   NVIDIA SDK log-level convention; logs expected under the NGX appdata
   area (check our staged appdata/logs AND %LOCALAPPDATA%\NVIDIA\NGX).
2. **E1 direct bind (use_shim=False)** becomes decisive: no shim module in
   the process at all ⇒ the name nvngx.dll resolves to nothing (his
   host's exact situation) ⇒ if the death disappears, the gate-vs-shim
   hypothesis is CONFIRMED.
3. If confirmed: ship a VS_VERSIONINFO resource inside our hand-built
   shim (we control the builder — make the shim version-indistinguishable
   from a real nvngx.dll) — the permanent cure that keeps the shim.

### UPDATE (run-28+ layout) — the community caller shims are now readable

`ComfyUI-DLSS5-NR-Linux` / `DLSS5-Video` (MIT, credited) ship
`native/caller_shim.cpp`, the source of the 91 KB `nvngx.dll_comfy.dll` we
only had as a binary. Facts from it and from their bridges:

- The caller check is real and documented there: *"The NR runtime validates
  the module that owns its RETURN ADDRESS. A trivial wrapper built with /O2
  can be tail-call-optimized into a JMP, which would leave the return address
  in dlss5nr_bridge.dll and trigger 0xBAD00002."* Their helper is a separate
  module with 5 `DLSSNR_Call*` exports, KERNEL32+msvcrt only, and — pefile —
  **no resources at all, i.e. no VERSION resource**. So a missing VERSION
  resource is demonstrably NOT fatal to the caller check in a working host.
- Their helper's file name is `nvngx.dll_comfy.dll`; the bare `nvngx.dll`
  name appears only as a *legacy fallback* for older release ZIPs.
- The snippet's `Init_Ext` order is `(app, path, device, FeatureCommonInfo*,
  sdkVersion)` — swapped against the public header order (their helper does
  the reorder, ours does it in `fwd_init_ext`).

Consequence for this hypothesis: the *name* mechanism is now testable
directly — our shim default moved off `nvngx.dll` (`nvngx.dll_ants.dll`,
`ANTS_NR_SHIM_NAME` restores the old name), so run 28's E1 (`ANTS_NR_USE_SHIM=0`)
tests shim **presence**, while run-29 candidate 1 (`ANTS_NR_SHIM_NAME=nvngx.dll`)
tests the **name** hypothesis on its own. The version-resource variant stays a
valid cure candidate if E1 outcome 1 fires.

### UPDATE (run-30 follow-up) — the wider DLSS5-NR corpus, and the caller check has a published answer

GitHub CODE search works in this sandbox (`gh api -X GET search/code -f
q='"DLSSNR.ScalingRatio"'` — 89 hits); `repos/<r>/contents/<p>` with
`Accept: application/vnd.github.raw` fetches them. Six more hosts/mirrors
saved to `/home/user/ext_research/dlss5_*`: Veyra-NRVideo
`DlssNrParameters.h`, vapourkit `dlssnr_params.h`, OptiScaler
`DlssNr_Proxy.cpp`, plus three design notes (`veyra_playbook.md`,
`vklayer_notes.md`, `royaltracer_dlss5.md`).

- **The parameter namespace is confirmed independently twice** (Veyra and
  vapourkit list the same `DLSSNR.*` strings we set, including
  `Output.Width/Height`, all four subrect families, `MVecScaleX/Y`,
  `ScalingRatio`, `Hint.Render.Preset`, `UICorrection`, `UseAutoMask`).
  vapourkit adds the reason the names had to be recovered from the binary:
  *"NGX silently ignores parameters it does not recognise"* — a misspelling
  is a no-op, so our set cannot be "wrong" by typo; only by value.
- **The caller check, stated plainly** (`vklayer_notes.md` §3.2): the host
  `LoadLibraryEx`es the snippet, `GetProcAddress`es exactly five exports
  (`NVSDK_NGX_D3D12_{Init_Ext,CreateFeature,EvaluateFeature,ReleaseFeature,
  Shutdown1}`), and then hooks the SNIPPET's own
  `KERNEL32!GetModuleFileNameW` IAT slot to answer **`nvngx.dll`** whenever
  the snippet asks about the host module: *"This is the
  signature/authorization bypass: the snippet verifies its caller is
  `nvngx.dll`."* That is the mechanism our caller shim satisfies
  structurally (real frame, return address inside a module named
  `nvngx.dll_ants.dll` — the LQCCS helper relies on the same and is named
  `nvngx.dll_comfy.dll`). Both names contain the `nvngx.dll` prefix; E1
  (`ANTS_NR_USE_SHIM=0`) remains the presence test.
- **Feature 18 never goes through the core**: their notes record core
  `CreateFeature(18)` → `0xbad0000b` ("Core has no NR implementation"), and
  every working host calls the SNIPPET's own five exports (the same route
  our shim-routed `_create`/`_evaluate` use). Success code is `0x1`.
- **Every call is wrapped in SEH** (`__try/__except`, fail-closed, capture
  the SEH code) — "All Feature-18 calls must be wrapped in SEH/VEH-style
  guards". Our ctypes calls inherit that: SEH surfaces as a Python
  exception, which run 30 proved is catchable.
- **Command-list hygiene (now matched)**: the proven hosts close+execute+
  fence-wait around every copy and *after* the feature call
  (`ExecuteAndWait()` in `kos_bridge2.cpp:328`, called before/after
  `g_shim_eval` at :1182/:1204), so the runtime always receives a freshly
  reset, empty list and its own recorded work is committed before the
  readback. Our `nr.py` now does the same (it used to hand over a list with
  the frame copy still pending and never execute the runtime's recording).
- **Guides**: their zero-frame provider creates exactly our resources for a
  host with no motion/depth — `R32_FLOAT` depth, `R16G16_FLOAT` motion,
  zero-filled (`ZeroFrameGuidanceProvider.cpp`). Ours now rely on D3D12's
  committed-resource zero-init instead of an upload.
- **Formats/quality seen across hosts**: Magpie feeds `R8G8B8A8_UNORM`
  in/out and sets `PerfQualityValue = Balanced` with the scaling-ratio
  callback forced to 1.0; the OptiScaler proxy sets no `PerfQualityValue` at
  all; kos maps 5→1.0 (DLAA) and *rejects* 6. Our 1× contract (quality 5,
  ratio 1.0 + callback, RGBA16F surfaces like kos/gan) stays as is.

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

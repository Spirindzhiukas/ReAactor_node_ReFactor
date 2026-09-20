# CONTINUATION — NR silent-kill investigation handoff (2026-09-20)

Read this at the start of the next session, after `CLAUDE.md` → `memory.md`
(runs 14–27c entries) → `plan.md` (🔍 NR section) → `RESEARCH/NOTES.md`.
This file exists because the investigation hit its instrumentation ceiling at
run 27c and the next session starts at the two final gates, not in the middle.

## Where the investigation stands

**The question**: the RenoDX/Merserk `nvngx_dlssnr.dll` (bytes identical to his
public build, renamed) kills the whole ComfyUI process at the first
`EvaluateFeature` on our pure-Python D3D12 host. Merserk's own app runs the
same bytes fine. The owner ruled (run 20): this must be *investigated*, not
refused — "it is something on our pure python implementation."

**The terminal verdict (run 27c)**: the kill is **deliberate and
kernel-direct** — `__fastfail` (int 29h, opcode `CD 29`) class or a raw
syscall. Every user-mode instrument was armed simultaneously and dodged:

| run | configuration | result |
|---|---|---|
| 14–19 | plain native NR (pre-core-preload) | instant silent death |
| 20–21 | owner rule + core preload, Init_Ext-first sweep, callbacks shipped | Init_Ext hr=1 FIRST TRY, CreateFeature(18) hr=1 |
| 22 | callbacks registered | hook FIRES inside evaluate; death after |
| 23 | callback dump | ABI decoded (params REPORT hook, printf-style) — but the 2 "crash" banners were our own IsBadReadPtr dumper (false alarms, retired) |
| 24 | fault-free VirtualQuery dumper | dump completes; silent death; NO exception anywhere |
| 25 | — | stale build (owner scoop timing), no new data |
| 26 | IAT traps on snippet+core (verified armed) | death dodges BOTH patched thunks |
| 27 | detour skipped on Win11 variant stub (fixed) + env drift (fixed by deletion) | net-less |
| 27b | callbacks OFF (env deleted) — but my nesting bug stripped ALL nets | death persists; **callbacks exonerated both ways** |
| 27c | ALL nets armed (traps verified, ntdll detour ARMED, 21-byte stub stolen) | silent death, NO TERMINATION line → kernel-direct |

Also exonerated: `models\` symlink (file APIs never AV), co-residence
(run-22/23 logs: no prior NGX in-process), return values (0/1 both die).

## What we know about his host (from his v10 source, license-local)

- His Python **never loads the snippet** — three compiled bridge dlls own all
  NGX contact (watchdog + poison doctrine). Our one-hop python→snippet host is
  a configuration **that does not exist in his ecosystem**.
- Engine imports: `D3D12SerializeRootSignature`, `CreateDXGIFactory1`,
  `D3DCompile`, kernel32-only NGX binding (**dynamic**, LoadLibrary/GetProc).
- Snippet/dlssg gate plumbing: `GetFileVersionInfoA/VerQueryValueA` (VERSION
  resource queries), `RegOpenKeyExW/RegQueryValueExW` (registry),
  `VerSetConditionMask/VerifyVersionInfoW`, self-console framework
  (`AllocConsole/WriteConsoleA`), `GetWindowThreadProcessId`.
- `NVSDK_NGX_LOG_LEVEL` env knob may activate the snippet's own log framework
  (kept `=1` in the owner's bat; check `C:\ProgramData\NVIDIA\NGX`).

## The leading hypothesis (test = run 28)

**VERSION-gate-vs-shim**: NGX runtimes find the core via
`GetModuleHandle("nvngx.dll")` and validate via file-version queries.
- His process: **no module named `nvngx.dll`** (core file is `_nvngx.dll`) →
  probes fall through to `C:\Windows\System32\nvngx.dll` — real driver dll,
  valid VERSION resource → gate passes.
- Our process: the name `nvngx.dll` is **deliberately occupied by our shim**
  (loaded first, by design) — a hand-built PE **with no VERSION resource** →
  `GetFileVersionInfoSizeA` returns 0 → gate fails → deliberate fastfail kill.
This fits every run since 14: deliberate, exceptionless, fires at first
evaluate, fine in his host, invisible to IAT/ntdll instruments.

## RUN 28 — one run, both remaining gates (already shipped, defaults on)

Owner does: scoop via GitHub Desktop, add `set "ANTS_NR_USE_SHIM=0"` to the
launch bat (keep `set "NVSDK_NGX_LOG_LEVEL=1"`, no other ANTS_ lines — they
were deleted on purpose), run once.

Console must show, before `NGX init ->`: the two trap lines, the audit line,
`ntdll detour ARMED`, and `int29 trap: N fast-fail site(s) converted`.

Outcomes:
1. **NR completes** → VERSION-gate-vs-shim CONFIRMED. Cure (next session):
   add a real `VS_VERSIONINFO` resource to the shim builder
   (`shim.py` `build_shim_dll`) so the shim is version-indistinguishable from
   a real nvngx.dll. Keep the shim; verify the shim identity check + suite.
2. **Dies with** `NATIVE CRASH: exception 0x80000003 (breakpoint (patched
   fast-fail site)) at MODULE+0xOFF` → run `resolve_offsets.bat OFF` against
   the named module → the gate's address is identified → next session decides:
   binary-diff the gate code vs the stock `nvngx_dlssnr.dll` (DLSS Swapper
   copy — discriminator still never run; `list_imports.bat` is zero-risk
   static), or conclude hard anti-tamper and close per owner instruction.
3. **Dies with NO breakpoint line** → the kill used a raw syscall, not
   CRT fastfail. Remaining options are binary analysis only — recommend
   closing the RenoDX build per the run-19 outcome-3 path unless the owner
   wants a deeper dive.

After NR settles: drop-Merserk cleanup (plan.md), credit Merserk + document
provenance in the nodepack docs.

## Run 28 attempt #1 — VOID (our scanner faulted; fixed 2026-09-20)

The first run-28 attempt never reached the runtime: the process died inside
`crashlog.install_int29_trap` (our diagnostic) reading a PE section header -
`Windows fatal exception: access violation` at `crashlog.py:421 u32`, full
all-thread faulthandler dump, then `TERMINATION via ntdll!NtTerminateProcess
(status=0xC0000005)` from our own detour. The E1 shim gate therefore never
ran and the run says NOTHING about `nvngx_dlssnr.dll`.

It did prove the instrumentation lines fire: trap lines for the session owner
+ snippet, the static audit line, `ntdll detour ARMED (stolen 21-byte syscall
stub)` - all before the fault.

Both bugs are fixed and pinned by tests, so the next run is comparable:

1. **Fault-proof diagnostics.** `try/except` cannot catch a raw dereference
   (an AV in a ctypes getter is a hard Windows exception). Every read in
   `crashlog` now goes through `_Mem` (VirtualQuery-verified, region by
   region); the int29 scanner validates MZ/PE/e_lfanew/section-count/
   optional-header-size/section-table-bounds and skips with a logged reason;
   `_patch_iat` and the ntdll stub read use the same guard; each module is
   wrapped so a diagnostic can never kill a run.
2. **The staged file is the selected file.** The NR folder also contains a
   file literally named `nvngx_dlssnr.dll`; the old staging rule used it in
   place, so run 28 loaded a DIFFERENT build than the node selection (and
   than every run before it). Now only the chosen file is canonicalized
   (`models/DLSS/staged/<name>-<size>/`), the sibling is named in a warning,
   the exact runtime in use is logged with its size, and a canonical file
   that is not a copy of the selection is refused.

Both crash sites from that attempt were the same defect class: the header
read (`u32`) and the section BYTE scan (`ctypes.string_at` - the owner's full
stream names this one). Section VirtualSize is a claim, not a promise: the
loader can leave pages unmapped, and the pre-fix scanner read them raw. The
scan now takes exactly the bytes VirtualQuery vouches for (`read_some`) and
steps over holes in page strides, so coverage is complete for mapped memory
and nothing else is ever touched.

DEPLOYMENT CHECK: the console must show
`[ANTs] NR/SR host build <date>.<n> - core-owned session, guarded diagnostics`
(`HOST_BUILD` in `ants/dlsssr/ngx.py`). If that line is missing, an older
build is still deployed - the first attempt was diagnosed from a stack trace
precisely because of that.

Re-run the same bat (`ANTS_NR_USE_SHIM=0`, `NVSDK_NGX_LOG_LEVEL=1`). Expected
new lines before `NGX init ->`:
`[ANTs] NR runtime in use: <path> (<bytes>)`, `[ANTs] int29 trap: scanning 2
module(s) ...`, and then either `N fast-fail site(s) converted to breakpoints
in <module>` or `no fast-fail site (...)`. The three outcomes below are
unchanged.

## Run 29 (2026-09-20, first guarded build) — what the NGX log proved

**Deployment + diagnostics all verified in-console** (`NR/SR host build
2026-09-20.3`, selected runtime staged with the sibling named in the warning,
`ntdll detour ARMED`, int29 scan `10 sites in session owner _nvngx.dll`,
`6 in snippet nvngx_dlssnr.dll`). No silent kill this time: the node failed
with **our own** D3D12 bug (`ID3D12GraphicsCommandList.Close 0x80070057` from
staging-heap barriers) — fixed, and the guide zero-fill uploads are gone (a
committed D3D12 resource already reads as zeros, so the first submit
disappeared entirely).

**The new black box is the core's own log** (it survives any death and lands in
OUR tree): `ComfyUI/models/DLSS/staged/ANTs/appdata/logs/nvngx.log`; the core
says `Logging to requested file ... enabled successfully`. Always ask for it
with the console.

**The core refuses to host a community snippet as its own feature provider.**
Quoted from that log, for every snippet it tries: `NGXSecureLoadFeature ->
SnippetLocationInfo::load`, then for ours
`nvLoadSignedLibraryW() failed on snippet '...nvngx_dlssnr.dll' missing or
corrupted - last error Cannot find the requested object.` →
`NGXLoadMetaDataViaGetFileVersionInfo: swscanf_s() failed` →
`unable to load DLL metadata via FileVersionInfo for snippet` →
`NGXLoadFromPath failed for <dir>: 0xBAD00000` →
`ModuleName - nvngx_dlssnr.dll doesn't exist in any of the search paths!`
The version/FV hypothesis is confirmed, but it applies to the SNIPPET inside
the CORE's loader — not to our helper. A renamed community build cannot pass a
signed-snippet gate, so **the core-owned FEATURE path is closed for community
builds**; the snippet must be hosted by us (which our layout already does —
the core only owns the session + capability parameters).

**The caller helper is NOT signature- or version-gated** (offline pefile on the
community helper that drives feature 18 in their hosts,
`ext_research/nvngx.dll_comfy.dll`): security directory `0x0` (no Authenticode
table), zero `VS_VERSION_INFO`/`FileVersion` strings, no resource directory at
all — and it works there. Our own unsigned, versionless shim is the same
geometry as the proven-working one; do not add a version resource looking for
a cure.

**Caller geometry fix (reference-host parity).** The log showed
`NGXInitContext: ... called from module nvngx.dll_ants.dll` — i.e. the CORE was
being called through our helper, a geometry no working host exhibits (their
bridges call the core's entry points directly and route ONLY the snippet
through the helper). The session OWNER is now bound directly; a snippet
provider still goes through the shim; `ANTS_NR_CORE_VIA_SHIM=1` restores the
old geometry, and the console announces which binding was used
(`NGX init -> _nvngx.dll (bound directly)`).

**Also confirmed by the log:** the ProjectID route works
(`MapProjectId: Found cms id 876232c for engine: custom engineVersion
ANTs 1.1.0 projectID 53f803cc-...`); the core resolved the adapter through
NVAPI itself, so our `NvAPI_Initialize` pre-step is Wine/vkd3d-only (the
message now says so); the parameter backend on this driver is the vtable map
(resource=0 pointer=2 int=3 float=6), the flat C API is absent.

**Next run (30) needs no env lines** — or `set "NVSDK_NGX_LOG_LEVEL=1"` for a
looser core log. After the run, double-click
`tools\collect_rig_evidence.bat` and send what it produces. Expected: build marker → runtime in use → int29 scan →
`NGX init -> _nvngx.dll (bound directly)` → `Init_ProjectID` → capability
params → snippet `Init_Ext via caller shim` → `CreateFeature` → `evaluate`.
Send: console + `staged/ANTs/appdata/logs/nvngx.log` (+ the crash file if the
int29 trap names a breakpoint site). E1 (`ANTS_NR_USE_SHIM=0`) is still
unexecuted and now matters only for "does the shim's PRESENCE change anything"
— the helper's own signature/version is exonerated.

## Run 28+ host layout — IMPLEMENTED while run 28 is pending (2026-09-20)

The whole NR host was rebuilt onto the layout the **working** hosts of this
runtime use, because their sources became readable offline
(`ComfyUI-DLSS5-NR-Linux` / `DLSS5-Video`, MIT, credited) — the strongest
evidence available without the rig:

- **The runtime is called through a SEPARATE helper module** — never from the
  module that loaded it ("the NR runtime validates the module that owns its
  RETURN ADDRESS", `0xBAD00002` when it is the bridge). Their helper ships as
  **`caller/nvngx.dll_comfy.dll`** (91 KB MinGW, 5 `DLSSNR_Call*` exports,
  **no VERSION resource**) and the bare `nvngx.dll` name survives there only as
  a *legacy fallback* for older release ZIPs.
- **The snippet's `Init_Ext` argument order is swapped** against the public
  header one: snippet = `(app, path, device, FeatureCommonInfo*, sdkVersion)`.
  A host that passes the header order hands the runtime an `int` where it
  expects a pointer (our shim now performs the swap in native code —
  `fwd_init_ext` — so the return address stays inside the helper image).
- **Core-owned session**: `Init_ProjectID` first (project id + engine version +
  runtime dir), then the snippet init, then **the CORE's capability parameter
  map** (`GetCapabilityParameters`; `AllocateParameters` "can still let
  CreateFeature succeed but then returns InvalidParameter at Evaluate").
- **ABI slot map** (both hosts, pinned): resource = 0, generic pointer/callback
  = 2, int/uint = 3, **float = 6**. The public-header float slot (1) "silently
  invokes a different overload and leaves every float parameter at an
  invalid/default value" → `0xBAD00005`.
- **Quality/ratio**: `PerfQualityValue` is the request's own mode — a 1×
  (DLAA/native) request is **5**, ratio 1.0, and the
  `DLSSNRComputeScalingRatioCallback` must confirm 1.0. `6` is the *neural
  post-pass* value and only correct **on top of an ordinary DLSS carrier**.
- **Per-frame contract**: the whole surface/subrect/parameter set is re-written
  before every evaluate; a zero-filled `R16G16_FLOAT` MVec (or NULL) and an
  optional zeroed `R32_FLOAT` depth with `DepthInverted=1` are both proven
  working (still-image vs video hosts).
- **`NvAPI_Initialize` runs before the NGX core init** (mandatory under
  Wine/vkd3d-NVAPI, harmless on Windows).
- Colour is handled in **`R16G16B16A16_FLOAT`** on both sides of the feature.

Our package now mirrors all of that: `ants/dlsssr/ngx.py` (session owner =
driver core, feature provider = the canonically-staged snippet, swap thunk,
NvAPI pre-step, always-on traps), `ants/dlsssr/nr.py` (full create contract,
1× quality 5, per-frame re-application, RGBA16F surfaces + guides),
`ants/dlsssr/shim.py` (swap thunk + **module name knob**), `ants/dlsssr/
parameters.py` (shim ABI slot map + flat C-API backend),
`ants/dlsssr/discovery.py` + `ants/dlssnr/discovery.py` (canonical-name
staging), `tests/test_native_flow.py` (fake-COM end-to-end for the new route).

Run 28 is unchanged (E1 still tests the shim-is-the-poison question). New
**run 29** candidates, in the order the evidence supports them:
1. `set "ANTS_NR_SHIM_NAME=nvngx.dll"` — restores the historical geometry
   against the new default `nvngx.dll_ants.dll` (the ecosystem's working shim
   deliberately avoids the exact `nvngx.dll` name).
2. `set "ANTS_NR_PERF_QUALITY=none"` — the still-image reference host never
   writes `PerfQualityValue` at all.
3. `set "ANTS_NR_USE_OWN_PARAMS=1"` — legacy snippet-direct route (the only
   geometry that ever reached the evaluate callback).

## Tool inventory (all rig-proven or tested)

**`tools\collect_rig_evidence.bat` - run this FIRST after any failed run.**
Double-click it (the pack's `tools\` folder, either in the GitHub checkout or
through the `custom_nodes` symlink). It finds the DEPLOYED build marker - the
run is void if that does not match the console's `NR/SR host build ...` line -
inventories `models\DLSS` (every staged runtime, size + hash), COPIES the NGX
core log (`staged\ANTs\appdata\logs\nvngx.log`) and any crash file, records
git HEAD, your `ANTS_`/`NVSDK_` variables, the GPU and torch state, leaves
everything in `tools\rig_evidence\<date-time>\` and puts the report on the
clipboard. READ-ONLY: nothing outside that folder is written, ever. Send the
clipboard text plus the `files\` folder it opens.

- `tools/resolve_offsets.bat` + `tools/resolve_crash_offset.py` (owner copies:
  `C:\ComfyUI_PORTABLE\`) — names `MODULE+0xRVA` via export table; owner
  verified `KERNEL32 0x27799 → IsBadReadPtr+0x29`.
- `tools/list_imports.bat` + `tools/list_imports.py` — drag-drop static
  imports, flags termination APIs + NGX backends; `--all` always.
- `ANTS_NR_*` env contract: defaults define the experiment; traps/detour/
  int29 arm ALWAYS on the NR path; opt-outs `ANTS_NR_TERMINATION_TRAP=0`,
  `ANTS_NR_INT29_TRAP=0`; `ANTS_NR_USE_SHIM=0` = E1; `ANTS_NR_RUNTIME_CALLBACKS`
  = legacy experiment (exonerated, keep for A/Bs).
- Crash black box: console stderr + `models/DLSS/staged/ANTs/appdata/logs/
  native-crash.log` (survives the death — prefer it when the console is lost).

## Rig facts (owner environment)

Windows portable ComfyUI `C:\ComfyUI_PORTABLE\ComfyUI`, RTX 24 GB; node at
`I:\AI SHITE\CODING\GITHUB\ReAactor_node_ReFactor` symlinked into
custom_nodes; sync via **GitHub Desktop** after agent push (~1 min wait);
launch bat has a CONFIGURATION block (env must precede the `start` line);
NGX home `C:\ProgramData\NVIDIA\NGX`; the snippet may flash a **second
console window** during evaluate (its own framework — its buffer dies with
the process, the crash log does not).

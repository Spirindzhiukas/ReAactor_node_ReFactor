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

## Run 30 (2026-09-20) — the silence broke: evaluate reached, C++ throw caught

The full host contract ran for the first time: build marker, `NGX init ->
_nvngx.dll (bound directly)`, both trap sets + int29, `Init_ProjectID` hr=1,
capability params hr=1, snippet `Init_Ext` via caller shim hr=1,
**`CreateFeature(feature 18)` hr=1**, **`EvaluateFeature ->`** with the whole
90-parameter contract written.

Then, instead of a silent kill: the snippet raised an **MSVC C++ exception
(`0xE06D7363`)** which ctypes surfaced as
`OSError: [WinError -529697949] Windows Error 0xe06d7363`, and ComfyUI
reported a normal node error (prompt finished). That is the first catchable
verdict in the whole investigation, and it is consistent with the earlier
"silent kill" being the same throw unwinding into a host without a handler.

**What the black box now does for you:** the first-chance handler decodes a
C++ throw into its RTTI type name (e.g. `.?AVinvalid_argument@std@@`), a
best-effort `what()` message, and the filtered live stack as
`module+0xoffset`. It is written to the same file as the other crash lines
(`.../staged/ANTs/appdata/logs/native-crash.log`) and the node error repeats
it in the console. `ANTS_NR_CXX_TRAP=0` opts out; the decode is
VirtualQuery-guarded and validated against hostile structures.

**The core's own log also confirmed the geometry fix**: `NGXInitContext:
called from module libffi-8.dll` (straight from our process - no shim in the
call chain), `NvAPI_DRS_FindApplicationByName -166` (python.exe is not a
registered driver-settings app - harmless), and the same signed-snippet
refusal for our staged community build, which is expected because WE host
that file.

**Console noise**: the NGX log callback is no longer echoed by default (run
30's console was ~90% core chatter, each line duplicated); the same text is
in `nvngx.log`. `ANTS_NR_NGX_ECHO=1` brings the echo back for a debugging
session.

**Contract parity fix before run 31** (from the wider public corpus swept
after run 30, see `RESEARCH/NOTES.md`): the proven hosts close+execute+
fence-wait around every copy they make *and right after* the feature call,
so the runtime always receives a freshly reset, empty command list and its
own recorded work is committed before the readback. Our `nr.py::evaluate`
handed it a list with the frame copy still pending and never executed the
runtime's recording - now fixed (two order-sensitive pins guard it). Also
published in that sweep: the caller check answers "is my caller
`nvngx.dll`" via the snippet's own `GetModuleFileNameW` IAT slot, which is
what the shim satisfies; feature 18 never goes through the core (core
`CreateFeature(18)` → `0xbad0000b`); and every working host wraps these
calls in SEH guards.

**Next run (31)**: plain re-run, no env lines needed. What we want from it
is the **C++ type + throw site** line - that names the branch inside the
snippet that rejects our frame/parameters. Run `tools\collect_rig_evidence.bat`
afterwards (it now auto-detects everything and can be run from anywhere) and
send the report plus `files\`.

## The crash black box (what it told us, 2026-09-20)

The owner's `native-crash.log` (appended across runs; 07:30 entry) holds:

```
[ANTs] NATIVE CRASH: exception 0xC0000005 (access violation) at
       C:\WINDOWS\System32\KERNEL32.DLL+0x27799
[ANTs] TERMINATION via ntdll!NtTerminateProcess(handle=0x0, status=0x00000002);
       call chain: libffi-8.dll+0x4771 <- ... <- python313.dll+0x6D38F
```

Two readings, both useful: the faulting instruction was inside **KERNEL32**
itself (kernel32-resident code - `GetProcAddress`-family; most other APIs
forward to KernelBase), and the process was then terminated **deliberately**
with status 2. That offset is the same one `resolve_offsets.bat` uses as its
example, so this is a recurring fault, not a one-off. Hardware fault lines
now also carry a caller chain, which is what will say whether the fault was
entered from our ctypes frame or from the runtime's code.

**The evidence collector now answers this without a second step**: it resolves
every `MODULE+0xRVA` in the collected logs to an export name (and prints the
neighbouring exports), inlines the last 60 lines of `native-crash.log` in the
report, and writes everything to
`C:\ComfyUI_PORTABLE\NODE_CODING\RIG_EVIDENCE\<timestamp>\` - outside the
checkout, so GitHub Desktop can never sweep it into a commit.

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

**Output location (changed 2026-09-20)**: the dump now goes to
`C:\ComfyUI_PORTABLE\NODE_CODING\RIG_EVIDENCE\<date-time>\` - a sibling
of the ComfyUI folder, NEVER inside the checkout (GitHub Desktop kept
offering the dumps for the PR). `ANTS_EVIDENCE_OUT` overrides it. The report
now inlines the crash black box, resolves every `MODULE+0xRVA` to an export
name, and carries a **MODELS/DLSS LAYOUT AUDIT** (KEEP / SAFE TO DELETE /
YOUR CALL) for the models tree.

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

## Owner Q&A (2026-09-20): exact paths, and what to delete

The owner asked which paths the current code expects, and which of his
duplicated folders are redundant. The authority is
**`docs/MODELS_DLSS_LAYOUT.md`**; the collector prints the audit for the tree
that is actually on the disk. Summary:

| path | who reads it |
|---|---|
| `models/DLSS/NR/nvngx_dlssnr*.dll` (any filename) | node `dll_version`, native host + legacy engine |
| `models/DLSS/SR/nvngx_dlss*.dll` | SR node (staged as `nvngx_dlss.dll`) |
| `models/DLSS/FG/nvngx_dlssg*.dll` | reserved; listed, no effect yet |
| `models/DLSS/Merserk_DLLS/neuroframe_{caller,engine}.dll` | the ONE home of the helper pair (legacy neuroframe engine only) |
| `models/DLSS/staged/**` | written by this pack: canonical-name copies, shim PE, NGX log, crash box |

Rules now enforced in code:

- the helper pair is copied **from the stash into the stage** - `NR/`, `SR/`,
  `FG/` and the DLSS root no longer need their own copies (delete them);
- a stash folder must actually contain `.dll` files, so the empty `HELPERS/`
  can not win over `Merserk_DLLS/`, and `Merserk_DLLS` wins over `HELPERS/`
  even when both are populated;
- if NO stash exists the old "copy every sibling" behaviour runs and warns
  loudly - that is compatibility only;
- `auto` in `dll_version` only ever picks an `nvngx_dlssnr*` runtime; if the
  folder holds nothing but the helper pair it raises a loud `[ANTs]` error
  naming `Merserk_DLLS`;
- `staged/**` is disposable: delete it (ComfyUI closed) and it is rebuilt -
  and it is also the only place the logs live, so the collector finds nothing
  if it was deleted before the run (owner, 14:02 report: `files\` was empty
  because the tree had just been nuked).
- **A rename is not a different build.** The 14:02 audit proved the owner's
  `NR\nvngx_dlssnr.dll` and `NR\nvngx_dlssnr_RenoDX_4000_series_friendly.dll`
  are the same 165,830,144-byte build (identical SHA-256), kept on purpose to
  test the picker. *(Superseded the same day: the "refuse a renamed copy"
  behaviour was removed by the owner correction below - duplicates are now
  detected by content for REPORTING only, and selection is the naming rule.)*
- **THE BUILD NAMING RULE (owner directive, now a standing rule in CLAUDE.md 2b).**
  `auto` loads the newest build of a category, and the version comes from the
  file name: `nvngx_dlssnr_2026-09-14.dll` (date, preferred for the community
  NR builds) or `nvngx_dlss_310.9.1.dll` / `nvngx_dlssg_310.9.1.dll` (NVIDIA
  numbering). Priority: date > dotted version > bare number > no version
  (unversioned sorts last and is ordered by file date). Free text after the
  version is ignored; hardware tags (4000, 3090, series, friendly) are never
  versions. The `dll_version` selector lists builds newest first and an
  explicit pick always wins. Implementation: `ants/dlsssr/versions.py`.
  The owner's rig (`nvngx_dlssnr.dll` + `nvngx_dlssnr_RenoDX_4000_series_friendly.dll`,
  one build under two names, from Merserk's Visual.Enhancer.v10.0 bundle) is a
  deliberate picker test - so `auto` must never refuse it: it prefers the
  newest SAFE build when one exists, otherwise warns loudly and uses the
  newest (the risk list is a queue-safety preference, not a veto).

### NO "safe vs risky" builds (owner correction, 2026-09-20)

The community RenoDX-derived NR builds are the ONLY ones that work on RTX 30/40
series (the official DLSS 5 NR runtime targets RTX 50), so they are the normal
path - nothing is ranked, skipped or refused by name. That supersedes every
"risk list" note above and below in this file: `resolve_nr_runtime_path` has no
risk parameter, `node.py` prints an informational provenance line (credit:
RenoDX/clshortfuse + Merserk), and the runs 14-19 -> run 30 history lives in the
header of `ants/dlssnr/discovery.py` and the docs. Duplicates are found by
content (`same_bytes`) for reporting only; the owner keeps two names of one
build on purpose to test the picker.
- **The report is ONE file.** The raw logs (nvngx.log etc.) are inlined in
  `rig_evidence.txt` (400 KB per file cap, `[truncated: last ...]` marker when
  hit), on top of the already-inlined black box and resolved offsets. The
  `files\` folder is only needed for the rare oversized log.

## Run 18:16 / 18:25 (2026-09-20, owner) — what broke, and what is fixed

**Auto picking works**: a deliberately renamed
`nvngx_dlssnr_RenoDX_4000_series_friendly_2026-08-13.dll` was selected and
staged as expected (the naming rule + the version read out of the name showed
in the status line).

**Bug 1 — GPU acceleration fell back to CPU** (`engine lacks CUDA interop
(False)`, 20-25x slower). Causes we could act on without the rig: the message
printed a bool instead of the reason, nothing named the loaded engine build,
and a folder/stash could hold a plain engine next to a CUDA-capable one.
Fixed: `ants/dlssnr/peexports.py` (read-only export reader), CUDA-capable
engine preference in `core.find_engine_dll` and
`discovery.choose_helper_stash()`, an `engine:` status line + the engine's own
reason on the CPU path, and a HELPER / ENGINE INVENTORY section in the
collector report. Next run tells us definitively whether the pair on disk has
the entry points.

**Bug 2 — the native host died** on `ID3D12GraphicsCommandList.Close`
(E_INVALIDARG) right after the first `EvaluateFeature`, and the recovery's
`Reset` failed on the same poisoned list, killing the node (ComfyUI survived).
Fixed: the runtime records into a **dedicated** command list
(`GpuContext.command_list()`), and a recording that cannot be closed is now
dropped with the list+allocator PARKED and replaced (`_drop_recording`) - it
never raises unless `ANTS_D3D12_STRICT_CLOSE=1`. The runtime path warns that
the frame is stale and increments `gpu.runtime_recoveries`, so the next run
carries the count.

## Run 20:39 / 20:40 (2026-09-20) — the DEVICE was removed; the legacy error is the same event

**What the log actually shows** (this is the important reading):

1. 20:39 native: the CUDA-capable helper pair was selected and staged
   correctly; NGX logged the usual unsigned-snippet refusal (unchanged, we
   host the snippet ourselves); `Init_ProjectID`/`GetCapabilityParameters`/
   `Init_Ext`/`CreateFeature(18)` all returned `hr=1`.
2. Then `Close` answered `0x80070057` on our copy list, the first
   `EvaluateFeature` ran (its internal C++ throw is caught - `hr=1`), the
   runtime's list also failed `Close`, and then
   `CreateCommandAllocator` failed with **`0x887A0005` =
   DXGI_ERROR_DEVICE_REMOVED**. Both `Close` failures are the *same* event
   seen from different sides: the device was gone.
3. 20:40/20:41 legacy, same ComfyUI process: `Could not create a D3D12 device
   matching CUDA ordinal 0 by LUID`. That is the wedged-process state after a
   device removal (the driver refuses new devices until the process restarts),
   not a second bug and not caused by the staging change - the stage line
   names `Merserk_DLLS` in both the 18:16 (working) and 20:40 (failing) runs.

**Code changes (this commit)**:

* `GpuContext.device_status()` / `mark_device_removed()` /
  `device_removed_error()`: the reason is now reported in plain English
  (`DEVICE_HUNG` = the driver's ~2 s TDR timeout, `DEVICE_RESET`,
  `DRIVER_INTERNAL_ERROR`), the "not closable" warnings carry the device
  status, and a removed device raises ONE loud actionable error instead of
  cascading into more HRESULTs.
* A dead context refuses further work and creates no new objects;
  `node.py` drops the NGX session **and** the GPU context on a removal, so the
  next queued frame builds a fresh device.
* The legacy init failure now explains itself: restart ComfyUI first; if it
  persists in a fresh process, the helper pair is the suspect and the
  collector's HELPER / ENGINE INVENTORY is the evidence.
* `ANTS_D3D12_CHECKPOINT=1` (diagnostic, off by default): every recorded
  command is closed/executed/waited immediately, so a poisoned recording
  names the exact command (`... failed at command #N (Barrier(...))`)
  instead of "something in this list".
* The native host logs the adapter name and LUID it bound (the reference for
  any "by LUID" failure from the engine).

## Run 20:39+ (2026-09-20) — the owner's PR link: this is ONE Windows multi-GPU CUDA family

The owner pointed at **ComfyUI PR #15451** ("Limit default GPU management to
current device", still OPEN, `mergeStateStatus: BEHIND`; it refs **issue
#15255 / CORE-398**). Read together with our own log, the two are the same
family of failure:

* **#15255** is a Windows CUDA driver bug: once a process has touched more
  than one GPU, host->device copies start failing with
  `CUDA_ERROR_OUT_OF_MEMORY` (result=2) *with plenty of VRAM free*, and the
  CUDA context never recovers in that process. ComfyUI's maintainer
  (rattus128) reproduced it with raw CUDA only in the **multi-GPU** shape
  (`windows_cuda_host_memory_diagnostic.py`, posted in the issue): the clean
  single-GPU probe passed 1000 pinned transfers; the all-GPU probe failed the
  host registration after 12.000 GiB and then **every** `cuMemcpyHtoD_v2`,
  with `free=0, total=0` afterwards.
* **ComfyUI core enumerates every visible GPU at startup** (system_stats /
  `get_all_torch_devices()` for the model-management path), so on a multi-GPU
  machine the process is already in the risky shape *before* our node runs.
  The workaround today is to restrict the device list (`--cuda-device 0`, or
  `--cuda-device <one UUID>`) and/or `--disable-pinned-memory`; PR #15451
  makes the single current device the default (core), while other GPUs stay
  visible to custom nodes.
* Our 20:39/20:40 report is the same story on the D3D12 side: a poisoned /
  reset GPU state, a `Close` that answers `0x80070057`, and then
  `CreateCommandAllocator 0x887A0005` (DEVICE_REMOVED); the legacy "cannot
  match CUDA ordinal 0 by LUID" right after is the wedged process.

**What this commit changes** (adapter identity — and the `--cuda-device`
workaround only works once this is right):

* `ants/dlsssr/cuda_luid.py` (new): the CUDA side of "which adapter is this?"
  via the CUDA **driver** API (`nvcuda.dll` - `cuInit`, `cuDeviceGetCount`,
  `cuDeviceGetName`, `cuDeviceGetLuid`), with the CUDA runtime
  (`cudart64_*.dll` next to torch) as a fallback. Identity queries only,
  one device at a time, and every failure is a reason string - never an
  exception inside ComfyUI's process. `format_luid()` prints `hi:lo` the way
  aimdo/nvidia-smi do.
* `d3d12.py`: **the LUID is read at 0x128**, not 0x12C. `DXGI_ADAPTER_DESC1`
  is `Description[128] WCHAR; VendorId; DeviceId; SubSysId; Revision; three
  SIZE_T; LUID; Flags`, so the old `desc + 0x12C` pair returned
  `{HighPart, Flags}` - a wrong reference value in exactly the message the
  engine prints.
* `AdapterInfo` + `enumerate_adapters()` now carry name, LUID, vendor, device
  id and flags; `pick_adapter(adapters, ordinal)` matches the CUDA ordinal's
  LUID to the DXGI adapter, and **`make_gpu_context(ordinal)` creates the
  D3D12 device on that adapter** (`D3D12CreateDevice` with an explicit
  adapter, not NULL/default). Both engine paths (`dlsssr` SR and `dlssnr`
  native NR) go through it.
* The pick is logged as one line: `[ANTs] D3D12 host adapter #0 'NVIDIA
  GeForce RTX 4090' (vendor 0x10de, LUID 00000000:0000b412) - CUDA ordinal 0
  LUID 00000000:0000b412 -> adapter #0 ...`. If the LUID cannot be read the
  fallback picks the first hardware adapter (NVIDIA preferred, a software /
  WARP adapter can never win) **and says why** - the old code silently
  guessed by index.
* When more than one CUDA device is visible, one advisory line names the
  #15255 bug and the exact flags; a device-removal error gains clause (3)
  with the same flags. On a single-GPU rig nothing extra is printed.
* `tools\check_cuda_multigpu.bat` (+ `.py`): the owner-facing test of the
  bug itself, in its own process (a child with `CUDA_VISIBLE_DEVICES=0` does
  the pinned host copy first, then this process touches every GPU and repeats
  the copy). Verdict: `BUG REPRODUCED` (exit 10) => keep ComfyUI on one GPU;
  `not reproduced` (exit 0) => this is not the bug we are chasing.
* Collector: new **CUDA / MULTI-GPU VIEW** section (every device with its
  LUID + the launch flags found in the logs), so the next report answers
  "does #15255 even apply here".

### The 21:17 evidence (owner) — one GPU, the LUID line matches NGX, and the kill has a status

**1. This machine has ONE CUDA device — the multi-GPU CUDA bug is OUT.**

```
--- CUDA / MULTI-GPU VIEW (ComfyUI #15255) ---
  1 CUDA device(s) visible to this python:
    0: NVIDIA GeForce RTX 4090 (LUID 00000000:000497ea)
  single CUDA device: the multi-GPU CUDA bug (#15255) cannot apply ...
```

`check_cuda_multigpu.bat` agrees (phase A passed, phase B: "Only one CUDA
device is visible in this process"). #15255 needs **more than one** GPU
visible to the process, so it cannot explain the 20:39 device removal, and the
`--cuda-device 0` A/B is pointless on this rig — drop it. The adapter-identity
fix stays (it is what makes `--cuda-device N` safe on multi-GPU machines, and
it removed the wrong-LUID read).

**2. The LUID fix is now verified against NVIDIA's own log.** NGX writes:

```
[NGXCheckArchitectureSupport:229] Found matching adapter with NVAPI physical GPU
handle: 0xc00 and LUID: { 0x0, 0x497ea }
```

which is exactly the value our reader prints (`LUID 00000000:000497ea` —
`{HighPart, LowPart}`). The old `desc + 0x12C` read produced a bogus pair; the
new 0x128 read matches the vendor's own numbers, so the host and the engine
now agree about which GPU this process is on.

**3. The black box caught the kill (and it has a status now).**

```
[ANTs] C++ exception 0xE06D7363 ... thrown from libffi-8.dll+... <- _ctypes.pyd+... <- python313.dll+...
   ... (four of these: the runtime's internal throws, which we catch)
[ANTs] TERMINATION via ntdll!NtTerminateProcess(handle=0x0, status=0x00000002); call chain: libffi ...
[ANTs] TERMINATION via ntdll!NtTerminateProcess(handle=0xFFFFFFFFFFFFFFFF, status=0x00000002); call chain: libffi ...
```

`handle=0x0` is an invalid-handle call that fails; `handle=-1` is
NtCurrentProcess — **that one terminates the process with exit code 2**. Both
came from inside one of our ctypes calls (the chain is libffi → `_ctypes` →
python), which is the "silent kill" this project has chased since run 14: the
runtime deliberately terminates the host, and now we know the status (2) and
that it happens inside a ctypes call.

What was still missing was **which** call. This commit adds that:
every ctypes call into the engine (`dlss5nr_*`), NGX (`NVSDK_NGX_*`) and the
D3D12 vtables carries an in-flight label, and every kill/exception line prints
it — so the next report says
`TERMINATION via ... [in-flight call: EvaluateFeature]` instead of a chain of
FFI frames. Plus `ANTS_NR_BLOCK_TERMINATION=1` (opt-in A/B): the trap logs and
then **refuses** the kill (`ExitProcess`/`TerminateProcess` only; `abort` and
fast-fail are never blocked), so the node can fail loudly and ComfyUI stays
alive to print why. State after such a refusal is undefined by definition —
that is the experiment.

**4. A collector bug made the 21:17 helper inventory say the opposite of the
truth.** With no arguments (exactly how the bat calls it) the export reader
was never located, so every helper DLL printed "not a neuroframe engine" and
the report shouted `[!] NO helper build on disk exports the CUDA entry
points`. The reader path is fixed (the resolved repo is passed, not the empty
`--repo` argument), the report now says outright when no reader was loaded,
and the test invokes the collector the way the bat does. Re-run the collector
for the real HELPER / ENGINE INVENTORY.

**Open question for the owner (it decides how to read 20:40):** did ComfyUI
itself die at ~20:39:50? The `status=2` termination says the process *was*
asked to exit during the native evaluate. If the window closed and you
restarted before the 20:40 legacy run, then "cannot match CUDA ordinal 0 by
LUID" happened in a **fresh** process and is a real bug worth chasing; if it
was the same window, it is the wedged-process state as assumed.

### Run 22:35 (owner) — the CUDA zero-copy path WORKS now, in the legacy engine

```
[ANTs] CUDA interop armed (the engine's zero-copy gate is open): ...
[ANTs] engine: neuroframe_engine.dll (570880 bytes) exports dlss5nr_init + dlss5nr_process_cuda_v6
DLSS5 processing via CUDA - CUDA device-pointer path
[INFO] Prompt executed in 1.18 seconds
```

The same node that took 14-18 s on host staging at 21:47 finished a frame in **1.18 s** — the
blocking-sync flag arming at import did it. The engine's own gate
("active CUDA primary context does not use FFmpeg blocking-sync flags") is now satisfied, so this
was never a missing capability: it was one flag, set before torch built the context. Treat this as
**closed** — do not re-debug the CUDA path, and do not re-run the `--cuda-device`/pinned-memory
experiments.

Two details from that log:
* the flag line read `0x0C (unknown scheduling)` — the scheduling mask is the low **three** bits
  (`CU_CTX_SCHED_MASK = 0x07`), so 0x0C is blocking-sync (0x04) **plus** map-host (0x08): the arming
  had worked, the *name* was wrong. Fixed; the line now reads `0x0C (blocking-sync, map-host)`.
* the log now also names **which route** armed it (`1. cudaSetDeviceFlags(0x04) -> CUDA_SUCCESS`,
  `2/3` for the driver routes), so a future failure says exactly which call refused.

### Run 23:08 (owner) — the native host's NEXT wall: our new input recipe was invalid

```
[ERROR] [ANTs] ID3D12Device.CreateCommittedResource(nr motion) failed: HRESULT 0x80070057
        at nr.py: create_texture2d(..., state=input_state)
```

The 21:52 fix (inputs in the shader-resource state) went in with the UAV flag still attached, and
D3D12 answers `E_INVALIDARG` for **(ALLOW_UNORDERED_ACCESS, NON_PIXEL_SHADER_RESOURCE)** — the flag
may not ride along with that initial state. The pack now creates inputs as **plain shader resources**
(`D3D12_RESOURCE_FLAG_NONE` + NON_PIXEL_SHADER_RESOURCE, the recipe the proven host uses), tries a
small ladder of alternatives if the driver refuses the first, and **logs which recipe the driver
took** instead of silently swapping it. `ANTS_NR_INPUT_STATE=uav` still restores the old behaviour
for an A/B.

### Run 23:32 (owner) — the 23:08 fix exposed the NEXT layer, and it was ours

```
[ANTs] D3D12: the driver REFUSED the intended texture recipe for 'nr output'
       (flags 0x8, initial state UNORDERED_ACCESS; it answered ... E_INVALIDARG)
       and accepted (flags 0x0, initial state COMMON) instead
[ANTs] D3D12 command list not closable (Close 0x80070057) - our copy command list
       (4 recorded command(s): Barrier(nr color -> 1024), CopyTextureRegion(...),
       Barrier(nr color -> 64), Barrier(nr output -> 8))
```

Read the two lines together and the whole cascade is OURS, not the driver's:

1. the driver refused the one recipe the proven host uses
   **(ALLOW_UNORDERED_ACCESS, UNORDERED_ACCESS)** for the OUTPUT texture - the
   same recipe that succeeded at 21:52, so something about this process was
   already off (the device is created on the right adapter with the right LUID,
   and the run right before it in that session was the legacy CUDA zero-copy
   one);
2. our ladder then **silently degraded** to (FLAG_NONE, COMMON) - a texture
   WITHOUT the UAV flag;
3. the NR path dutifully recorded `Barrier(nr output -> 8)` (UNORDERED_ACCESS)
   on that non-UAV resource: an **invalid command**, which D3D12 punishes at the
   next `Close()` with E_INVALIDARG;
4. and that E_INVALIDARG is what the 21:52/20:39 "command list not closable"
   lines have been showing us all along.

Fixes, in the same commit:
* **the ladder can no longer drop the UAV flag**: a texture the runtime writes
  through a UAV either gets the flag or the creation FAILS, loudly, naming the
  device status at that exact moment (that status is what tells "the driver
  refuses" from "the device is already gone" - the only question left here);
* the refused-recipe warning carries the device status too, and a degraded
  recipe is **never cached** on the device (caching it would have poisoned every
  later texture in the process);
* `GpuContext.health` records `GetDeviceRemovedReason` at creation time and a
  device that is born unusable is announced in the console - so the next report
  answers "was it already broken before we asked for anything?".

**Open question for the owner (decides the next move):** was the 23:32 native run
in the SAME ComfyUI process as the 22:35 legacy CUDA run? If yes, the clean
experiment is: restart ComfyUI and run the native node FIRST, alone. If it still
refuses the UAV recipe with a healthy device status, then the driver itself is
refusing UAV textures for us and we go after the device-creation parameters
(feature level / debug layer / adapter) with the reference host as the yardstick.

### The node split (owner request) — one engine per node

The native path can now fail without touching the working one:

| node class | engine | dropped widgets |
|---|---|---|
| `ANTsDLSS5Enhancer` (unchanged) | both, via the `engine` selector | — |
| `ANTsDLSS5Processor` — *ANTs⚡DLSS5 Processor (ReShade based)* | legacy DLL engine, forced | `engine` (that is the ONLY difference) |
| `ANTsDLSS5ProcessorNative` — *ANTs⚡DLSS5 Processor (Native NGX, experimental)* | our native NGX host, forced | `engine` (that is the ONLY difference) |

Both are subclasses of the full node (one implementation, two focused UIs), both consume the same
**ANTs⚡DLSS NR Scheduler** output, and the frontend gives each its own header line, refresh button
and scheduler-link greying. **Settings parity is the owner's rule and is enforced by a test**:
`set(PROCESSOR inputs) == set(ENHANCER inputs) - {"engine"}` for both, so the widgets that only one
engine can act on stay visible and honest - e.g. "SR pre-denoise" on the legacy node warns that it
needs the native host, and "CPU (host staging)" on the native node says the native path is always a
GPU path, instead of either node quietly dropping controls the other has. Naming: the legacy engine's DLL lineage is the RenoDX DLSS-5 addon — a
**ReShade** addon — so "ReShade based" is the accurate short name; OptiScaler is a different project
(a DLSS/XeSS/FSR redirector) and none of its code is involved.

### Scheduler: the default plan is now the showcase, and the node shrinks again

* `passes` defaults to **3**, and the cycle is **Cinematic → Natural → Default** (Python
  `STYLE_CYCLE_DEFAULT` and the JS default are the same list) — a freshly dropped scheduler shows the
  owner-validated plan "in its full glory".
* The JS re-fits the node after every rebuild (`fitNode` → `computeSize` + `setSize`): it used to
  grow when the per-pass toggles went ON and stay big when they went OFF, because removing widgets
  never recomputed the size.

### Run 21:52 / 21:53 (owner) — the first native evaluate, decoded

The 21:52 run got FURTHER than any run before it: NGX initialized, the feature
was created and the first evaluate RETURNED SUCCESS.

```
21:52:10  Init_ProjectID hr=0x1   GetCapabilityParameters hr=0x1
          snippet Init_Ext (via shim) hr=0x1   CreateFeature(18) hr=0x1
21:52:11  [ANTs] D3D12 command list not closable (Close 0x80070057) ...
          Device status: 0x00000000 (device present and healthy)   <-- OURS
21:52:11  NGX EvaluateFeature -> ... [ANTs] C++ exception 0xE06D7363
          [in-flight call: EvaluateFeature] ... <- hr=0x00000001  (SUCCESS)
21:52:11  [ANTs] NATIVE CRASH: exception 0xC0000005 at
          nvwgf2umx.dll+0x6D3471 [in-flight call: ID3D12GraphicsCommandList]
          -> Python stack: com.py release <- d3d12.py close <- _close_native
[ERROR]   The D3D12 device was REMOVED while closing the command list:
          0x887A0001   (then the node's own recovery message)
21:53:17  next frame: D3D12CreateDevice failed: HRESULT 0x887A0001
```

Four facts come out of it, and each one is now code:

**1. The colour input was in the wrong D3D12 state.** The engine's contract -
and what the shipped open-source ComfyUI host does - is that the INPUT
textures (Color/MVec/Depth) live in a **shader-resource** state and only the
OUTPUT is in UNORDERED_ACCESS. We handed it a colour texture in UAV state.
That is exactly the kind of thing D3D12 answers with `E_INVALIDARG` at
`Close()` (the 18:25, 20:39 and 21:52 lines) and what a driver can turn into a
GPU fault. Fixed: inputs are created in / uploaded to
`NON_PIXEL_SHADER_RESOURCE`, output stays a UAV, and the contract line prints
both states. `ANTS_NR_INPUT_STATE=uav` restores the old behaviour for an A/B.

**2. 0x887A0001 is not a "removal reason".** It is
`DXGI_ERROR_INVALID_CALL`, and it is also what the *next* frame's
`D3D12CreateDevice` answered - i.e. the process was already finished with
D3D12. Every HRESULT in the pack now prints its name
(`describe_hresult`), a failed `D3D12CreateDevice` in a process that has
already lost a device says **RESTART ComfyUI**, and the removal message no
longer says "REMOVED ... unknown removal reason".

**3. Releasing a dead device's objects crashes in the NVIDIA driver.** The
access violation at `nvwgf2umx.dll+0x6D3471` happened while
`ID3D12GraphicsCommandList::Release` was in flight, one breath after the
removal. `GpuContext.close()` now detects that the device is gone and
**releases nothing** (the objects are left to process teardown), and it says so
in the console. This is the "crash inside the cleanup" that made the 21:52
report look like two bugs.

**4. We still do not know which recording was invalid at 21:52:11** - the
"not closable" line came from **our own copy list** (device healthy!) before
the feature call, and the two lists looked identical in the log. The message
now names the list and its last recorded commands, so the next occurrence is
readable; `ANTS_D3D12_CHECKPOINT=1` names the offending command and
`ANTS_D3D12_STRICT_CLOSE=1` stops the run there.

### The CUDA gate, decoded from the engine's own words (21:47 run)

```
DLSS5 processing via host staging (CPU) - engine has no CUDA interop:
engine reports CUDA interoperability unavailable: active CUDA primary context
does not use FFmpeg blocking-sync flags
```

Same machine, same DLL: OreX's node runs the same NR runtime with
`CUDA zero-copy` in 2.65 s while our CPU staging needs 14-18 s. The engine is
telling us the requirement verbatim: it joins the process's CUDA **primary
context** (torch's) and it wants `CU_CTX_SCHED_BLOCKING_SYNC` (0x04) - the flag
FFmpeg sets when it creates a context for D3D<->CUDA interop. PyTorch never
sets it.

`ants/dlsssr/cuda_flags.py` (new) now:
* reads `cuCtxGetFlags()` and names what the context carries;
* tries all three routes to the flag, in order, and reports the return code of
  each: `cudaSetDeviceFlags(0x04)` (runtime; only legal before the context
  exists), `cuDevicePrimaryCtxSetFlags(device, 0x04)`, `cuCtxSetFlags(0x04)`
  (may accept an active context - that is the open question);
* runs once at IMPORT (`ants/__init__.py` -> `log_early`), i.e. the earliest
  moment our code runs, because that is the only window in which the runtime
  route can work;
* prints a one-line summary next to the "GPU acceleration is ON but this run
  uses CPU staging" warning, so the reason is never a bare bool again;
* `ANTS_NR_CUDA_FORCE=1` (A/B, opt-in) tries the CUDA entry point even when the
  engine's own gate refuses - the crash box is armed on that path, so a refusal
  is captured rather than lost.

**Next runs, in order** (each in a FRESH ComfyUI process):

0. Use the **ANTs⚡DLSS5 Processor (Native NGX, experimental)** node for the native runs and the
   **ANTs⚡DLSS5 Processor (ReShade based)** node for the legacy ones, so one engine's failure can
   never be confused with the other's (and so the working node stays clean in the workflow).
1. **Small frame first, native engine** (e.g. 768x768, 1 pass): proves the
   whole D3D12 path end to end, and a small evaluate cannot hit the 2 s TDR
   timeout. If this passes, the device removal is a size/timeout issue. The
   colour input is now in the shader-resource state the runtime expects, so
   this run also tests the 21:52 fix - the contract line now prints
   `inputs NON_PIXEL_SHADER_RESOURCE, output UNORDERED_ACCESS`. If a texture recipe is refused the
   log now says which one the driver took instead.
2. **Then the 4096x3072 frame, native**: if the device is removed again the
   error now names the reason; if it says DEVICE_HUNG, the fix is a TDR delay
   (`HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers\TdrDelay`,
   seconds, reboot) or smaller frames.
3. **Then legacy (GPU)**: it should initialize normally in a fresh process.
   If it *still* says "by LUID", send the collector report - the HELPER /
   ENGINE INVENTORY section will show whether the pair on disk is the one
   that worked earlier.
4. **If the process dies again** (console closes / ComfyUI restarts itself):
   the kill line now carries `[in-flight call: ...]` - send that line. Then
   re-run once with `set "ANTS_NR_BLOCK_TERMINATION=1"` in the launch bat:
   the trap will not let the runtime kill the process, the node fails loudly
   instead, and ComfyUI survives to print the reason.
5. If a `Close 0x80070057` appears, the line now says WHICH list failed and
   what it had recorded. While the device reports healthy, re-run once with
   `set ANTS_D3D12_CHECKPOINT=1` - the log names the invalid command; to stop
   the run right there use `set ANTS_D3D12_STRICT_CLOSE=1`.
6. **CUDA zero-copy (the 20-25x)**: after the sync, look for
   `[ANTs] CUDA context flags ...` in the ComfyUI startup log. If it says the
   flag could not be armed (normally `cudaErrorSetOnActiveProcess`, because
   torch created the context first), send that whole block - it is the answer
   to why this node is 6x slower than OreX's on the same DLL. Then, once, with
   the GPU path: `set "ANTS_NR_CUDA_FORCE=1"` in the launch bat (A/B; the
   crash box is armed, so a refusal is captured).
7. Re-run `tools\collect_rig_evidence.bat` (the 21:17 report's helper
   verdicts came from a tool bug - the reader had not loaded) and send the
   HELPER / ENGINE INVENTORY section.

## Rig facts (owner environment)

Windows portable ComfyUI `C:\ComfyUI_PORTABLE\ComfyUI`, RTX 24 GB; node at
`I:\AI SHITE\CODING\GITHUB\ReAactor_node_ReFactor` symlinked into
custom_nodes; sync via **GitHub Desktop** after agent push (~1 min wait);
launch bat has a CONFIGURATION block (env must precede the `start` line);
NGX home `C:\ProgramData\NVIDIA\NGX`; the snippet may flash a **second
console window** during evaluate (its own framework — its buffer dies with
the process, the crash log does not).

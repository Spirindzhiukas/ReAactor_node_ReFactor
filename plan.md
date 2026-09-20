# plan.md — Active Execution Roadmap

The step-by-step checklist. Tick items off **in the commit that does them**, add new items
as they are agreed with the owner, and move finished context into `memory.md` so this file
stays a lean "what's next". Rules: `CLAUDE.md`. Facts: `memory.md`.

## 🔍 NR silent-kill investigation (runs 14-27c; handoff: CONTINUATION.md)

Terminal verdict (2026-09-20): the RenoDX/Merserk `nvngx_dlssnr.dll` kills the
process **deliberately, kernel-direct** (`__fastfail`/int-29h class) at the
first evaluate on our host — proven against every user-mode instrument (VEH
black box, IAT traps verified armed, ntdll detour armed). Callbacks, symlink,
co-residence, shim-routing all exonerated; Merserk's v10 sources inspected
(license-local under `RESEARCH/`) — his host never loads the snippet from
python and never registers callbacks.

- [x] **Run 28+ layout implemented (code side, owner-independent):** core-owned
      session (driver core = owner, canonically-staged snippet = feature
      provider), caller shim with the snippet `Init_Ext` swap (`fwd_init_ext`),
      capability parameter map, RGBA16F surfaces + guides, full 1x create
      contract re-applied per frame, `PerfQualityValue` 5 for 1x (6 = carrier
      post-pass), `NvAPI_Initialize` pre-step, shim module name off the real
      `nvngx.dll` name - contract parity with the two working community hosts
      whose sources were decoded this session (see `memory.md` entry and
      `CONTINUATION.md` "Run 28+ host layout"). Suite: 329 checks green.
- [x] **Run 28 attempt #1 (2026-09-20) = VOID**: our own int29 scanner faulted on
      a section header and killed ComfyUI before `NGX init ->` (nothing about
      the runtime was tested). Fixed + pinned: VirtualQuery-guarded reads
      (`crashlog._Mem`), PE header validation, per-module skip-with-reason, and
      the staging rule that had silently loaded a same-named sibling instead of
      the selected build. The launch bat needs no change.
- [x] **Run 29 (2026-09-20, first guarded build) — node failed on OUR D3D12 bug,
      and the NGX log finally spoke**: staging-heap barriers are invalid commands
      (Close -> 0x80070057); fixed + the guide zero-fill uploads are gone.
      The core log shows it REJECTS the community snippet as a core-hosted
      provider (`nvLoadSignedLibraryW` + FileVersionInfo -> 0xBAD00000), so the
      core-owned FEATURE path is closed for community builds - the snippet is
      hosted by us, as the working hosts do. Offline pefile: their working
      caller helper is unsigned and versionless, so the "VERSION resource in
      the shim" cure is dead. Re-run next; always send
      `models/DLSS/staged/ANTs/appdata/logs/nvngx.log` with the console.
- [x] **Run 30 (2026-09-20) — EvaluateFeature REACHED; silence broken**: the
      full host contract ran (init -> capability params -> snippet Init_Ext ->
      CreateFeature hr=1 -> EvaluateFeature) and the snippet threw a CATCHABLE
      MSVC C++ exception (0xE06D7363) instead of killing the process. The
      first-chance handler now decodes those throws to RTTI type + message +
      stack; `nr.py` reports it loudly and the node drops the dead session;
      console de-flooded (ANTS_NR_NGX_ECHO). Collector fixed for a real rig
      (auto-detection, no path arguments, no cmd quote mangling).
- [x] **Run-30 follow-up (offline)**: public feature-18 corpus swept (6 new
      sources + 3 design notes; `RESEARCH/NOTES.md`) - the caller check is
      published (the snippet asks who its caller is and expects `nvngx.dll`),
      the namespace is confirmed twice, and the proven hosts drain the
      command list before the feature call and submit right after it. Our
      `nr.py` now matches that; pins fail if the hygiene goes away.
- [x] **Collector relocated + self-answering (owner instruction)**: dumps go to
      `C:\ComfyUI_PORTABLE\NODE_CODING\RIG_EVIDENCE\`, never inside the
      checkout; crash offsets are resolved to export names in the report; the
      black box is inlined. The 07:30 crash file (AV inside KERNEL32 at
      +0x27799, then a deliberate `NtTerminateProcess(status 2)`) is the
      recurring silent-kill signature - hardware fault lines now carry a
      caller chain to name the caller.
- [x] **Owner Q&A: exact paths + keep/delete (2026-09-20)**: the helper pair
      now has ONE home. `docs/MODELS_DLSS_LAYOUT.md` documents every path the
      code reads/writes and what is safe to delete; `stage_nr_runtime` copies
      only the chosen runtime + the pair from `Merserk_DLLS` (stash > HELPERS >
      package dll; loud sibling-copy fallback only when no stash exists); auto
      selection refuses to treat a helper DLL as an NR runtime; the collector
      prints a keep/delete audit of the owner's own tree.
- [x] **Owner 14:02 evidence** → **owner correction**: there is no safe/risky
      split. The community RenoDX-derived builds are the ONLY ones that work on
      RTX 30/40 series (the official DLSS 5 NR runtime targets RTX 50), i.e. the
      normal path for most users. All name-based risk gating was REMOVED
      (`skip_known_bad`, `is_known_force_terminator`, `risk_reason`): selection
      is the naming rule alone, an explicit pick always wins, and the engine
      prints a provenance status line instead of a scare-warning. The runs
      14-19 history lives in the module header + docs; the traps/black box stay
      armed. Duplicate names of one build are detected by content for the report
      (the owner keeps two on purpose to test the picker).
- [x] **THE BUILD NAMING RULE (owner directive 2026-09-20)** — `auto` loads the newest
      build, version read from the FILE NAME (`nvngx_dlssnr_<date>.dll`,
      `nvngx_dlss_<version>.dll`); date > dotted > bare number > unversioned; selector lists
      newest first; explicit pick always wins. `ants/dlsssr/versions.py`, wired into the NR
      and SR pickers, the widget tooltips, the status lines, the collector audit and the docs
      (README + CLAUDE.md 2b + MODELS_DLSS_LAYOUT.md). The NR picker no longer refuses an
      all-risky folder (the owner's own rig): it prefers the newest safe build, else warns and
      uses the newest.
- [x] **RUN 18:16/18:25 (owner) — two bugs fixed**: (a) GPU acceleration fell
      back to CPU ("engine lacks CUDA interop") → the pack now reads export
      tables (read-only `peexports.py`), prefers a CUDA-capable engine when
      staging and when loading, prints the engine build + what it exports, and
      the collector gained a HELPER / ENGINE INVENTORY section; (b) the native
      host died on `CommandList.Close` 0x80070057 after the first evaluate →
      the runtime records into a DEDICATED command list now, and an
      unconclosable recording is dropped + replaced (never reused, never
      fatal; `ANTS_D3D12_STRICT_CLOSE=1` for A/B).
- [x] **RUN 20:39/20:40 (owner) — the D3D12 device was REMOVED**: both Close
      failures and the final `CreateCommandAllocator 0x887A0005` are one event
      (DXGI_ERROR_DEVICE_REMOVED); the legacy "matching CUDA ordinal by LUID"
      right after is the wedged process. Code: reason codes reported in plain
      English, dead contexts refuse work and build nothing, node drops session
      + GPU context for a clean rebuild, legacy init explains the restart
      rule, adapter name/LUID logged, `ANTS_D3D12_CHECKPOINT=1` names an
      invalid command if a Close fails on a healthy device.
- [x] **RUN 20:39+ (owner lead) — CUDA LUID adapter identity + the #15255
      multi-GPU CUDA family**: the owner's PR #15451 / issue #15255 link made
      the failure family explicit (Windows CUDA poisons the process once more
      than one GPU is touched; core enumerates every GPU at startup).
      Code: `ants/dlsssr/cuda_luid.py` (driver-API LUID/name/count, reason
      strings, never raises), `AdapterInfo` + `pick_adapter()` +
      **`make_gpu_context(ordinal)`** so the D3D12 device is created ON the
      adapter whose LUID is the CUDA ordinal's (this is also what makes
      ComfyUI's `--cuda-device N` workaround safe: it renumbers CUDA, not
      DXGI), the LUID read fixed to **0x128** (0x12C was the HighPart: the
      old log printed `{HighPart, Flags}`), a one-shot multi-GPU advisory and
      a removal-error clause naming the flags, `tools\check_cuda_multigpu.bat`
      (fresh-process A/B: single-GPU child vs all-GPU copy), and the
      collector's new **CUDA / MULTI-GPU VIEW** section (devices + LUIDs +
      launch flags).
- [x] **OWNER EVIDENCE 21:17 — the rig is SINGLE-GPU**: collector + probe
      both report one RTX 4090 (LUID 00000000:000497ea), so the #15255
      multi-GPU CUDA bug is OUT on this machine — the `--cuda-device 0` A/B is
      pointless and dropped. NGX's own log line (`LUID: { 0x0, 0x497ea }`)
      now matches our reader exactly: the 0x128 LUID fix is verified against
      the vendor. The black box caught the kill: two
      `TERMINATION via ntdll!NtTerminateProcess` lines (handle 0x0 then -1,
      `status=0x2`) inside a ctypes call — the runtime terminates the host,
      and the chain is FFI-only. New: every ctypes call into engine/NGX/D3D12
      carries an in-flight label printed on every kill/exception line, plus
      `ANTS_NR_BLOCK_TERMINATION=1` (opt-in: the trap refuses the kill so the
      node fails loudly). Collector bug fixed: with no args (the bat) the
      export reader was not found → every helper "not a neuroframe engine" +
      a false CUDA alarm; the resolved repo is now passed and the test runs
      the collector the way the bat does.
- [x] **OWNER EVIDENCE 21:52 (native, first real evaluate)** — NGX init /
      Init_Ext / CreateFeature(18) all hr=1, EvaluateFeature returned hr=1, and
      then the frame died: our copy list Close 0x80070057 (device HEALTHY),
      runtime list Close with the device gone, an access violation in
      nvwgf2umx during `CommandList::Release`, and the next frame's
      `D3D12CreateDevice 0x887A0001`. Four fixes: (1) inputs (Color/MVec/Depth)
      now live in NON_PIXEL_SHADER_RESOURCE and only the output is a UAV — the
      contract the shipped host uses (`ANTS_NR_INPUT_STATE=uav` A/B);
      (2) `0x887A0001` is named DXGI_ERROR_INVALID_CALL, a
      `D3D12CreateDevice` failure in a process that lost a device says RESTART
      ComfyUI, and the removal text no longer calls INVALID_CALL a removal
      reason; (3) `GpuContext.close()` releases NOTHING once the device is gone
      (that release is what crashed in the driver) and the process-wide wedge
      is remembered; (4) the "not closable" line names WHICH list and what it
      recorded. Plus the CUDA gate: the engine's own text
      ("active CUDA primary context does not use FFmpeg blocking-sync flags")
      → `cuda_flags.py` + the three arming routes + import-time attempt +
      `ANTS_NR_CUDA_FORCE=1`.
- [x] **OWNER EVIDENCE 22:35 — the CUDA zero-copy path is SOLVED (legacy engine)**: same frame
      1.18 s vs 14-18 s on host staging. The blocking-sync flag armed at import (~ ants/__init__.py
      -> cuda_flags) opened the engine's own gate; the log's `0x0C (unknown scheduling)` was a WRONG
      MASK (scheduling is the low 3 bits: 0x0C = blocking-sync + map-host) — fixed, and the line now
      names the route that armed it. CLOSED: no more CUDA-path debugging, no --cuda-device/pinned
      experiments for the native node either.
- [x] **OWNER EVIDENCE 23:08 — native host, new wall**: `CreateCommittedResource(nr motion)
      E_INVALIDARG` because the shader-resource recipe carried the UAV flag. Inputs are now plain
      shader resources (FLAG_NONE + NON_PIXEL_SHADER_RESOURCE, ladder + loud log of the accepted
      recipe).
- [x] **NODE SPLIT + SCHEDULER (owner request)**: `ANTsDLSS5Processor` (ReShade-based legacy engine)
      and `ANTsDLSS5ProcessorNative` (native NGX, experimental) — subclasses, no engine selector,
      engine-specific widgets only; own JS header/refresh/greying; both take the same schedule.
      Scheduler defaults to 3 passes Cinematic -> Natural -> Default; the JS re-fits the node size
      when per-pass rows are removed (it never shrank before).
- [x] **OWNER EVIDENCE 23:32 — the cascade decoded**: the driver refused
      `(ALLOW_UNORDERED_ACCESS, UNORDERED_ACCESS)` for the OUTPUT texture, our
      ladder silently degraded to a UAV-less texture, and the NR path's
      `Barrier(nr output -> UNORDERED_ACCESS)` on it was an INVALID COMMAND -
      that E_INVALIDARG is the "command list not closable" line from 18:25 /
      20:39 / 21:52. Fixed: the ladder can no longer drop the UAV flag (loud
      failure + device status), a refused recipe is never cached, and
      `GpuContext.health` reports the device state at creation. OPEN QUESTION:
      was 23:32 the same process as the 22:35 legacy CUDA run?
- [x] **NODE PARITY (owner rule)**: both focused processors now drop ONLY the
      `engine` selector — `set(proc) == set(enh) - {"engine"}` is pinned by a
      test; the engine-only widgets stay visible and say what they mean.
- [ ] **Run 34 (owner, fresh process each time)**: 1) native + a SMALL frame
      (768x768) — the state fix's real test; 2) if that works, 4096x3072;
      3) legacy (GPU) in a fresh process; 4) if anything dies: send the
      `[in-flight call: ...]` line, then once with
      `set "ANTS_NR_BLOCK_TERMINATION=1"`; 5) always send the new
      `[ANTs] CUDA context flags ...` block — that is the 20-25x answer.
- [ ] **Run 33 (owner, fresh process each time)**:
      1. native engine + a SMALL frame (768x768, 1 pass) → proves the path
         end-to-end and cannot hit the 2 s TDR timeout;
      2. native engine + 4096x3072 → if DEVICE_HUNG comes back, it is a TDR
         (raise `TdrDelay` in the registry / use smaller frames);
      3. legacy engine (GPU) in a fresh process → must initialize;
      4. if `Close 0x80070057` appears with a healthy device, re-run with
         `set ANTS_D3D12_CHECKPOINT=1` and send the console.
- [ ] **Run 32 (owner)**: legacy engine with the CUDA-capable pair in
      `Merserk_DLLS` → the log must print `exports dlss5nr_init +
      dlss5nr_process_cuda_v6, dlss5nr_cuda_supported` and
      `DLSS5 processing via CUDA`. If it prints `NO CUDA ENTRY POINTS`, the
      pair on disk is the old build → replace it from Gourieff's
      `neuroframe_dlls.zip` (the inventory section names what you have).
- [ ] **Run 32 (owner, native)**: same run with the ANTs native engine → the
      Close warning should either be gone (dedicated list) or appear ONCE per
      frame with `recovery #n`; the node now survives it. Send console +
      `rig_evidence.txt` (the engine inventory is in it).
- [ ] **Run 31 (owner): plain re-run** (`set "NVSDK_NGX_LOG_LEVEL=1"`,
      no ANTS_ lines needed) — the first run that should reach CreateFeature +
      evaluate with the full host-parity contract. Console + nvngx.log + crash
      file if the int29 trap names a site. The stage folder now also carries
      the helper pair copied from `Merserk_DLLS`, so the per-category copies
      are no longer part of the experiment.
- [ ] **Run 31 (owner, E1):** `set "ANTS_NR_USE_SHIM=0"`
      in the launch bat (keep `NVSDK_NGX_LOG_LEVEL=1`), scoop, run once.
      - Works → the shim's PRESENCE is implicated (its signature/version
        cannot be: the proven-working community helper has neither) → keep
        the shim off the `nvngx.dll` name / dig into module-table geometry.
      - Dies with `NATIVE CRASH: exception 0x80000003 (breakpoint (patched
        fast-fail site)) at MODULE+0xOFF` → resolve the offset
        (`resolve_offsets.bat`) → the gate's address is named → decide
        (binary-diff vs stock nvngx_dlssnr / hard anti-tamper conclusion).
- [ ] **Run 29 candidates (after run 28, in evidence order):**
      `ANTS_NR_SHIM_NAME=nvngx.dll` (historical shim name vs the new
      ecosystem-matching default), `ANTS_NR_PERF_QUALITY=none` (the
      still-image reference host never writes the parameter),
      `ANTS_NR_USE_OWN_PARAMS=1` (legacy snippet-direct route).
- [ ] Still-open cheap discriminator: list imports of the **stock**
      `nvngx_dlssnr.dll` (DLSS Swapper copy) with `list_imports.bat` — never run.
- [ ] After NR settles: drop-Merserk cleanup (legacy neuroframe path +
      discovery fallbacks), credit Merserk in the nodepack docs.

## 🧪 Awaiting owner test (gate for v1.1.0 stable)

- [ ] Owner tests DLSS5 hybrid + GPU acceleration: **GPU Acceleration Auto** (expect ~OreX-class
      speed at 4K with nr_passes 4 — the old host path measured ~100× slower), HDR bridge
      Classic/Anchored/Off with owner-tuned neutral defaults (220 nits / 1.0 scale), temporal
      history Auto/Continuous/Per-frame reset, DLLs in `models/DLSS/dlssnr_<version>/` under any
      filenames. If the engine zip is old (no CUDA export) the node says so loudly — update
      neuroframe_dlls.zip from Gourieff's HF dataset.
- [ ] Owner tests the OPTIONS-socket part of `88305cb` (ANTsOptions → ANTsFaceDancer merge).
- [ ] If both pass → tag **v1.1.0** and open/merge the PR to `main`.

## 🔜 Next up (agreed direction, in order)

- [ ] Fix any owner-reported issues from the test round above (priority over new features).
- [ ] After first real-world Anchored-mode use: evaluate whether `black_lever`/`highlight
      anchor` defaults feel right; consider an `Anchored+Classic blend` only if the owner asks.
- [ ] Optional cleanup when convenient: `model_paths._migrate_legacy_dirs` off-by-one.
- [ ] Consider replacing the numpy thumbnail scene heuristic with the engine's native
      `dlss5nr_scene_score_v1` export for temporal Auto mode.
- [x] Pre-SR denoise — owner-confirmed helping a lot (SCUNet favorites).
- [ ] Owner tests NR Schedules: scheduler JS (dynamic rows, model-slot hiding), enhancer
      widget greying while wired, schedule off/on parity, per-pass denoise fallback.
- [x] DLSS preset mechanism SOLVED: presets are host-settable NGX params on nvngx_dlss.dll
      (DLSS.Hint.Render.Preset.<Mode>) — the flip trigger is OUR OWN NGX host, not a
      neuroframe ABI change (RESEARCH_dlss5_hybrid §8 + RESEARCH_dlss_sr_upscaler §3).
- [x] Probe v2 run on the rig: NGX core PRESENT (DriverStore nvmdsi.inf,
      core 32.0.16.1692), nvngx_dlss.dll 310.9.1.0 (J/K/L/M ready), engine
      1.4.0.0 with full v1-v6 family incl. process_frame_v6 (NR-side
      upscaling) + dlss5nr_shutdown (wired into teardown). All green lights
      for ants/dlsssr/.
- [ ] Pure-Python neuroframe replacement (phased): SR host first (ants/dlsssr),
      then port feature 18 onto it -> pair becomes optional legacy (verdict +
      facts in RESEARCH_dlss_sr_upscaler §7).
- [ ] SR node (`ants/dlsssr/`): pure-FFI NGX host; selector over
      models/DLSS/SR/<version>/ sets (owner notes several SR dll variants
      exist); modes DLAA/Q/B/P/UP + output always rescaled back to input;
      artist preset names.
- [ ] FG (Frame Generation) — future version per owner (Merserk/OreX do video
      FG); models/DLSS/FG/<version>/ reserved in discovery v2.
- [x] RR (Ray Reconstruction) — OUT OF SCOPE (owner confirmed: no way to drive it).
- [ ] Build ants/dlsssr/ — pure-Python ctypes NGX host (reference technique:
      DLSS-Video-Transcoder; NVIDIA/DLSS SDK headers = the API source; never copy DVT code,
      no LICENSE). Widgets after the host works: sr_upscale_mode (DLAA/Q/B/P/UP),
      sr_preset (artist names), sr_return_interpolation (lanczos/... — output always
      resized back to input). Zero-MV still quality = A/B on the rig first.
- [ ] Future DLSS styles/modes: add to schedule.STYLES (single registry) — UI/parser/enhancer
      derive automatically.
- [ ] Idea shortlist (owner asked "what else"): native dlss5nr_scene_score_v1 for temporal
      Auto; per-pass optional preview outputs (pass 1 vs final); per-pass timing log lines
      (helps tune schedules); VRAM headroom via dlss5nr_cuda_status; shareable schedule-preset
      JSON strings.
- [ ] README screenshots/usage examples for the DLSS5 section (owner-provided, when he has them).

## 🌱 Long-term ideas (discussed, not committed)

- [ ] Native bridge project: build our own in-process D3D12/NGX bridge from OreX's MIT C++
      sources (`bridge/`) to gain zero-PCIe interop, upscale modes (1x–3x), VRAM chunking,
      VIDEO support — while keeping our control surface. Only if the owner wants the "more
      oomph" endgame; big effort, separate project.
- [ ] External pre-denoiser stage (e.g. OIDN) before DLSS-NR — new dependency; needs owner
      buy-in (see research conclusion: NVIDIA ships no separate pre-SR denoise DLL).
- [ ] Bundling neuroframe DLLs with the pack (license permits, `LICENSE-Merserk.txt`) —
      decided against for now; revisit if owner wants one-click install.

## 💤 Blocked / tabled (do NOT start without owner instruction)

- [ ] Eyes restoration (detection-only findings in `docs/RESEARCH_ultralytics_eye_fix.md` §8).
- [ ] inswapper_128_fp16 work (topic closed).

## ✅ Recently completed (detail lives in memory.md / git log)

- [x] `588f790` — DLSS5 hybrid upgrade (HDR bridge, temporal history, models/DLSS discovery).
- [x] `88305cb` — pre-pass + consolidation (20→18) + ANTs rebrand v1.1.0-alpha1.
- [x] Rebrand residuals sweep (`[ANTs]` startup banner).

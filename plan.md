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

- [ ] **Run 28 (owner, one run carries both gates):** `set "ANTS_NR_USE_SHIM=0"`
      in the launch bat (keep `NVSDK_NGX_LOG_LEVEL=1`), scoop, run once.
      - Works → VERSION-gate-vs-shim confirmed → cure = ship a real
        VS_VERSIONINFO resource inside our shim builder (keep the shim).
      - Dies with `NATIVE CRASH: exception 0x80000003 (breakpoint (patched
        fast-fail site)) at MODULE+0xOFF` → resolve the offset
        (`resolve_offsets.bat`) → the gate's address is named → decide
        (binary-diff vs stock nvngx_dlssnr / hard anti-tamper conclusion).
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

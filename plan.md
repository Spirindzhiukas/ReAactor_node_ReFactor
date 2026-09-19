# plan.md — Active Execution Roadmap

The step-by-step checklist. Tick items off **in the commit that does them**, add new items
as they are agreed with the owner, and move finished context into `memory.md` so this file
stays a lean "what's next". Rules: `CLAUDE.md`. Facts: `memory.md`.

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
- [ ] Pre-SR denoise shipped (denoise_model socket): owner to pick favorite 1x models
      (SCUNet / PureScale2 1x_PureVision / others) and report behavior at 4K + nr_passes 4
      in GPU mode (denoiser adds VRAM-resident inference before the engine).
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

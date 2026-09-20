# CLAUDE.md — Permanent Rules (read at the start of EVERY session)

Read order every session: this file → `memory.md` (project state ledger) → `plan.md`
(active roadmap) → `git log --oneline -15` (what happened since plan.md was last touched).
`memory.md` and `plan.md` are living documents: **update them in the same commit as the
work they describe**, so a fresh clone of `main` is fully self-documenting.

## Project identity

- **ANTs Face Nodes for ComfyUI** (ex "ReActor ReFactor"); `app_title`/`version_flag` in
  `ants/version.py`, package `comfyui-ants-face-nodes` in `pyproject.toml`.
- Lineage: fork/rework of Gourieff's ComfyUI-ReActor face-swap nodepack, rebranded to the
  **ANTs⚡** node family. **No backward workflow compatibility is required** — the owner
  explicitly authorized breaking old node names/workflows.
- Node surface: **19 nodes**, all prefixed `ANTs` (census in `memory.md`).

## Owner standing rules (do not violate; ask before changing any)

1. **3rd-party DLLs/models are never auto-downloaded at runtime.** The DLSS5 node never
   downloads anything. `nvngx_dlssnr.dll` stays 100% user-procured (NVIDIA's license
   prohibits redistribution). The neuroframe helper DLLs (author Merserk) may be linked,
   never silently bundled — license: `LICENSE-Merserk.txt` in Gourieff's HF dataset tree.
2. **DLSS DLL location is enforced:** `ComfyUI/models/DLSS/<NR|SR|FG>/` (flat builds and
   `<version>/` subfolders), and **ANY .dll filenames are accepted** — the engine is
   identified by probing its exports, never by filename. The neuroframe helper pair lives
   **only** in `models/DLSS/Merserk_DLLS/`; `staged/` is a disposable working area the pack
   creates. Paths + keep/delete: `docs/MODELS_DLSS_LAYOUT.md`. Legacy `models/dlssnr/` +
   package `dll/` remain graceful fallbacks.
2b. **THE BUILD NAMING RULE (owner directive 2026-09-20):** a build's **file name carries its
   version** — `nvngx_dlssnr_<date>.dll` (community NR builds) / `nvngx_dlss_<version>.dll`,
   `nvngx_dlssg_<version>.dll` (NVIDIA numbering). `auto` in `dll_version` loads the **newest**
   build by that rule (date > dotted version > bare number > no version, then file date, then
   name); the selector lists builds newest-first; an **explicit pick always wins**. Free text
   after the version is ignored, hardware tags (`4000`, `3090`, `series`, `friendly`) are never
   versions. Implementation + rationale: `ants/dlsssr/versions.py` (shared by NR and SR).
   Consequences to keep: **no build is ever ranked, skipped or refused by NAME.** The official
   DLSS 5 NR runtime targets RTX 50-series, so on RTX 30/40 the community RenoDX-derived builds
   (RenoDX/clshortfuse; packaged in Merserk's bundle) are the ordinary path, and the owner keeps
   two names of one build on purpose to test the picker. Selection = the naming rule and nothing
   else; an explicit pick always wins; an unversioned file that is newer on disk than the picked
   build is reported, never silently ignored; duplicates are detected by content (byte-identity)
   for reporting only. The provenance line the engine prints at startup is informational —
   `ANTS` history (runs 14-19 killed the process on the pre-run-30 host; run 30 throws a catchable
   exception) lives in `ants/dlssnr/discovery.py`'s header, never in the selection path.
   The collector prints what `auto` would load per category. Do not "fix" any of this without
   the owner's explicit instruction.
3. **Loud-failure pattern** for missing dedicated models: raise a `RuntimeError` starting
   with `[ANTs]`, naming the exact expected path and the fix. The owner explicitly likes
   this style — never silently degrade to a fallback when a dedicated model is missing.
4. **Dedicated loader-node sockets, no monolithic dropdowns** — models arrive via typed
   sockets from dedicated loader nodes (`ANTsFaceSwapModelLoader`, etc.).
5. **Keep our unique controls and the `dll_version` selector**; when adopting ideas from
   other implementations (e.g. OreX), build a **hybrid** — never nuke the existing
   implementation without the owner's explicit instruction.
6. **Naming:** widget is `codeformer_fidelity` (never `codeformer_weight`). Artistic,
   human-friendly widget naming stays.
7. **Credits are mandatory** for borrowed ideas/code: OreX (orex2121, ComfyUI-DLSS5-orex),
   RenoDX / clshortfuse (HDR Colour Bridge concept, MIT), Merserk (neuroframe DLLs),
   Gourieff (upstream ReActor + DLL distribution).
8. **Eyes restoration is TABLED** — no work on it without a new explicit owner
   instruction (findings preserved in `docs/RESEARCH_ultralytics_eye_fix.md` §8).
9. **inswapper_128_fp16:** the fp16 topic is closed; no further fp16 work.
10. **All code, comments, logs, errors, docs: English only.** (A full RU sweep already
    happened; keep it clean. Verify with a `[\u0400-\u04FF]` regex scan, not grep ranges —
    grep byte-ranges false-match the ⚡ in node names.)

## Rig session rules (added 2026-09-20, owner-imperative)

1. **Diagnostics always ship as ready-to-run `.bat` (or `.ps1`) files** — never
   raw commands. One marked owner-editable block with baked paths, system-python
   fallback, zero parens on executable lines, CRLF (`.gitattributes`), output
   auto-copied to the clipboard. Pinned by a test (test_dlsssr).
2. **The owner syncs via GitHub Desktop** after the agent pushes (waits ~1 min).
   NEVER hand the owner git commands; pull-check helpers (findstr) may print
   nothing before his scoop — the run itself is the proof. Working clone:
   `I:\AI SHITE\CODING\GITHUB\ReAactor_node_ReFactor`, symlinked into
   `C:\ComfyUI_PORTABLE\ComfyUI\custom_nodes\ReAactor_node_ReFactor`.
3. **Merserk sources are license-local**: `RESEARCH/merserk_ve10/` is
   gitignored (source-available proprietary license — inspection OK,
   redistribution NOT; this repo is a public channel). Only our own analysis
   (`RESEARCH/README.md`, `RESEARCH/NOTES.md`, memory entries) is committed.
   Never copy his code; credit him; technique reference only.
4. NGX on the owner rig lives at `C:\ProgramData\NVIDIA\NGX` (models, no logs
   yet). NR debug env contract (defaults define the experiment): traps +
   ntdll detour + int29 trap arm ALWAYS on the NR path; opt-outs only
   (`ANTS_NR_TERMINATION_TRAP=0`, `ANTS_NR_INT29_TRAP=0`,
   `ANTS_NR_USE_SHIM` toggles the E1 direct-bind experiment). Never nest
   diagnostic arming inside env-gated experiment blocks again (run 27b lesson).
5. **The native NR path reports its own output** (2026-09-21, after two silent bugs cost nights):
   the first frame of every prompt runs a smoke check - NaN/Inf and out-of-range counts in the
   RGBA16F readback (i.e. before the host clamp hides them) and whether the output is
   byte-identical to its input - and logs ONE line only when something is wrong; it never raises
   (intensity 0 is a legal no-op). Instrument knobs, all opt-in, defaults OFF:
   `ANTS_D3D12_DEBUG_LAYER=1` (+ `ANTS_D3D12_DEBUG_MESSAGES`; needs the Windows "Graphics Tools"
   feature) arms the D3D12 debug layer before device creation and drains the runtime's own
   sentences; `ANTS_NR_SOAK=1` prints one handles+VRAM line per prompt (the ~50-prompt soak);
   `ANTS_NR_SESSION_CACHE=1` keeps the size-keyed NGX session across prompts - the DEFAULT is the
   per-prompt init the working run used, so do not flip it without rig evidence.
6. **The pre-denoise SR stage is engine-independent** (owner request, 2026-09-21): it is OUR
   `ants/dlsssr` 1:1 DLAA host, run before whichever NR engine is selected. One selection rule,
   `pre_denoise_action(mode, strength, has_model)` -> "sr" / "model" / None, drives both the pass
   list and all three engine paths (native, legacy CUDA, legacy host staging). SR mode needs NO
   model - the strength is only the gate - so never collapse its strength to 0 for a missing
   `denoise_model`, and never re-gate the stage on the engine. It needs `nvngx_dlss*.dll` in
   `models/DLSS/SR/` (loud error names the path if missing).

## Engineering conventions

- **Tests:** `tests/test_*.py` are standalone scripts (exit 0 = pass). Run:
  `python tests/<file>.py`. They import through `tests/smoke_import.py::install_stubs`
  (torch/insightface/etc. stubs) — **the sandbox has no torch and cannot install it**;
  never add torch-dependent imports outside the stub boundary
  (`upscale_image_with_model` is the sanctioned stub seam).
- **Gates before every commit:** `pyflakes` over `ants/`, `nodes.py`, `tests/`
  (benign exceptions: star-import notes in `codeformer_arch.py`); `tests/test_pyflakes.py`,
  `tests/test_scope_check.py`, `tests/smoke_import.py` (asserts exactly 22 nodes), plus
  the full suite. Current tally: **461 checks** (see `memory.md`).
- **Rebrand hygiene:** no `ReFactor` outside the historical H1 note and the git clone URL
  in `README.md`; `CATEGORY` attrs are `ANTs` / `ANTs/loaders`; `[ANTs]` log prefix.
- **Commits:** conventional prefixes (`feat:`, `fix:`, `docs:`), body bullets explaining
  why. Push only to the session's working branch; `main` moves via PR merge.
- **Sandbox reality (Arena):** restarts wipe `.venv_test` and sometimes git refs
  (`.git/config` is not persisted). Recreate the venv:
  `python3 -m venv /home/user/.venv_test && pip install numpy opencv-python-headless pillow pyflakes`.
  If `git push` says "fetch first" after a restart and local history looks orphaned:
  `git fetch origin <branch>`, then `git reset --soft <remote-head>` and re-commit —
  **never** force-push. The remote is the source of truth.

## Owner environment

- Windows, portable ComfyUI at `C:\ComfyUI_PORTABLE\...`; updates the nodepack via `git pull`.
- NVIDIA RTX (24 GB VRAM); DLSS5 node requires RTX 40/50-series + driver ≥ 616.x.

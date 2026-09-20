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
   Consequences to keep: the NR picker **never refuses** a folder whose builds are all on the
   rig-proven risk list — it warns loudly and uses the newest (the owner's own rig is exactly
   that case and he keeps duplicate names on purpose to test the picker); a rename is not a
   different build (byte-identical twins are treated as the same risky build); an unversioned
   file that is newer on disk than the picked build is reported, never silently ignored.
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

## Engineering conventions

- **Tests:** `tests/test_*.py` are standalone scripts (exit 0 = pass). Run:
  `python tests/<file>.py`. They import through `tests/smoke_import.py::install_stubs`
  (torch/insightface/etc. stubs) — **the sandbox has no torch and cannot install it**;
  never add torch-dependent imports outside the stub boundary
  (`upscale_image_with_model` is the sanctioned stub seam).
- **Gates before every commit:** `pyflakes` over `ants/`, `nodes.py`, `tests/`
  (benign exceptions: star-import notes in `codeformer_arch.py`); `tests/test_pyflakes.py`,
  `tests/test_scope_check.py`, `tests/smoke_import.py` (asserts exactly 18 nodes), plus
  the full suite. Current tally: **171 checks** (see `memory.md`).
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

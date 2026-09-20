# models/DLSS — what this pack reads, and what you can delete

This page is the authority for the on-disk layout. It answers three
questions: **which paths the code reads**, **which paths the code writes**,
and **what is safe to delete**. The rig audit in
`tools/collect_rig_evidence.bat` prints the same verdict for the tree you
actually have (the "MODELS/DLSS LAYOUT AUDIT" section).

Nothing here is downloaded by the pack. Every runtime DLL is 3rd-party and
manually installed (NVIDIA's licence forbids redistributing the
`nvngx_dlss*` runtimes; the neuroframe pair belongs to its authors).

## The layout

```
ComfyUI/models/DLSS/
├── NR/                      ← the neural-rendering builds you SELECT
│   ├── nvngx_dlssnr.dll                          (any filename is fine)
│   └── nvngx_dlssnr_RenoDX_4000_series_friendly.dll
├── SR/                      ← super-resolution builds (the SR node)
│   └── nvngx_dlss_310.9.1.dll
├── FG/                      ← frame-generation builds (reserved, no effect yet)
│   └── nvngx_dlssg_310.9.1.dll
├── Merserk_DLLS/            ← the neuroframe helper PAIR lives HERE ONLY
│   ├── neuroframe_caller.dll
│   └── neuroframe_engine.dll
└── staged/                  ← WORKING COPIES, created by this pack
    ├── <build-name>-<bytes>/nvngx_dlssnr.dll   one folder per selected build
    └── ANTs/
        ├── sr_staged/<build-name>/nvngx_dlss.dll
        ├── shim/                               the nvngx.dll forwarder PE
        └── appdata/logs/                        nvngx.log, native-crash.log
```

## Why `staged/` exists at all

Three hard requirements, all about **names** and **folder contents**:

1. **The NGX core looks for the literal file name `nvngx_dlssnr.dll`** inside
   its search paths, and for `nvngx_dlss.dll` in the SR case. A build under
   any other name therefore has to appear under the canonical name somewhere.
2. **The parts find each other by their neighbours.** The snippet and the
   neuroframe caller resolve their companion files from the folder they were
   loaded out of, and NGX loads the snippet from the folder it is given - so
   that folder has to be self-contained (the pair next to the runtime copy).
3. **The legacy "neuroframe" engine host is handed a bare directory**
   (`dlss5nr_init(ordinal, dll_dir, ...)`): it probes the DLLs inside for its
   own entry points, so the same folder must carry everything.

The staging folder is content-addressed by `<name>-<size>`, so a loaded DLL
is never rewritten and never locked, and it lives in your models tree (not in
a temp dir) because everything DLSS belongs under `models/DLSS`.

**It is disposable.** With ComfyUI closed you can delete `staged/` entirely;
it is rebuilt from the selection plus the helper pair on the next run.

## Rules the code enforces

- **Any filenames are accepted** in `NR/`, `SR/`, `FG/`. The engine is
  identified by probing exports at load time, never by file name.
- **`Merserk_DLLS/` (or `HELPERS/`, `HLP*`, or the package `dll/` folder) is
  the single home of the helper pair.** Staging copies the pair from there
  into the stage folder, so the pair never has to be duplicated into `NR/`,
  `SR/` or `FG/`. A folder only counts as the stash when it actually contains
  `.dll` files (an empty `HELPERS/` never wins over a populated
  `Merserk_DLLS/`).
- **If no stash exists at all**, staging falls back to the old behaviour
  (copy every `.dll` next to the selected runtime) and says so loudly. That
  fallback is compatibility only — moving the pair into `Merserk_DLLS/` once
  ends it.
- **Only the file you selected is ever canonicalised.** A sibling that
  happens to be called `nvngx_dlssnr.dll` is never loaded instead of your
  selection; it is named in a warning.
- **`auto` selection only considers files named `nvngx_dlssnr*`.** Helper
  DLLs in `NR/` can no longer be picked as a "runtime".

## Delete / keep

| Path | Verdict |
|---|---|
| `NR/nvngx_dlssnr*.dll` (the builds you select) | **keep** |
| `SR/nvngx_dlss*.dll`, `FG/nvngx_dlssg*.dll` | **keep** (SR used, FG reserved) |
| `Merserk_DLLS/neuroframe_caller.dll`, `neuroframe_engine.dll` | **keep — the only copy needed** |
| `NR/`, `SR/`, `FG/`, `DLSS/` copies of `neuroframe_*` | **delete** (duplicates) |
| `staged/**` | **delete freely while ComfyUI is closed** (rebuilds itself) |
| `HELPERS/` (empty) | delete or leave; an empty stash is ignored |
| `DLSS_Map.txt` | not read by this pack — keep as a reference or delete |
| two same-size builds in one category (e.g. `nvngx_dlss.dll` and `nvngx_dlss_310.9.1.dll`) | compare: if identical, keep one |

### A rename is not a different build

The pack's "this build killed the process" list matches **file names**
(`*renodx*`). A copy of such a build under a plain name is still the same
build, and the rig audit proved exactly that case on the owner's disk
(`nvngx_dlssnr.dll` and `nvngx_dlssnr_RenoDX_4000_series_friendly.dll`, both
165,830,144 bytes, identical SHA-256). Therefore:

- `auto` in `dll_version` compares **bytes**, not names: a candidate that is
  byte-identical to a listed build is refused too, with a warning naming the
  twin — otherwise "auto" would silently run the risky build while the widget
  looked safe;
- an **explicit** selection in the widget is always honoured (that is how you
  run such a build deliberately, which is the current line of work);
- the audit prints `[!!] ... IS ...` for exactly this combination, so the
  report explains it without anyone having to hash files by hand.

Deleting a DLL that ComfyUI currently has loaded fails with a sharing
violation — that is Windows protecting the file, not a problem. Close
ComfyUI and delete again.

## Which build `auto` loads — THE BUILD NAMING RULE

`auto` loads the **newest** build of a category, and the version is read from the **file name**:

| scheme | example | priority |
|---|---|---|
| date | `nvngx_dlssnr_2026-09-14.dll`, `nvngx_dlssnr_20260914.dll` | highest |
| dotted version (NVIDIA numbering) | `nvngx_dlss_310.9.1.dll`, `nvngx_dlssnr_v10.0.dll` | middle |
| bare number | `nvngx_dlssnr_2.dll` | low |
| no version | `nvngx_dlssnr.dll`, `..._RenoDX_4000_series_friendly.dll` | lowest (ordered by file date) |

Within one scheme the highest number wins; ties are broken by file date, then by name. Free text
after the version never matters (`_renodx4000`, `_beta2`, `_merserk`), and hardware/vendor tags
(`4000`, `3090`, `series`, `friendly`, `rtx`) are never mistaken for a version. The `dll_version`
selector lists builds **newest first**, and an **explicit pick always wins** — that is how you run
one specific build deliberately.

The rule orders candidates; it never identifies them: a file is a runtime only if it exports the
runtime entry points, and anything else is refused loudly with its path.

### Which NR builds exist, and why the community ones are the normal path

The official NVIDIA DLSS 5 NR runtime targets **RTX 50-series** hardware. On RTX 30/40 series the
community **RenoDX-derived** builds are the ones that work — RenoDX by clshortfuse (MIT), reworked
for 30/40-series support and shipped inside Merserk's `Visual.Enhancer` bundle together with the
neuroframe helper pair. Most users are on those GPUs, so this pack treats them as the ordinary
path: **no build is ranked, skipped or refused because of its name**, and `auto` simply loads the
newest one by the naming rule above. An explicit pick always wins.

Credit note: the NR builds belong to their authors (RenoDX/clshortfuse, community repack), the
helper pair to Merserk — this pack never bundles them and never downloads them.

Engineering history, kept so it is not re-derived: on the pre-2026-09-20 host contract this family
killed the process at the first `EvaluateFeature` (runs 14-19: no exception, no log) while
Merserk's own C++ host ran the same bytes fine — the gap was in our provider, not the build. Run 30
reached `EvaluateFeature` and threw a **catchable** C++ exception instead, which is where the
investigation continues. The crash black box and the traps stay armed on every native NR run, so a
regression is recorded rather than guessed at.

### The helper pair must carry the CUDA entry points (GPU acceleration)

The zero-copy path (the one that makes DLSS-NR ~20-25x faster) needs a
neuroframe engine that exports `dlss5nr_process_cuda_v6` (plus
`dlss5nr_cuda_supported`). Without it the node still runs, but on CPU staging,
and the console says so:

```
[ANTs] engine: neuroframe_engine.dll (570368 bytes) exports dlss5nr_init + NO CUDA ENTRY POINTS
DLSS5 processing via host staging (CPU) - engine has no CUDA interop: ...
```

How the pack decides (all read-only, nothing is executed to find out):

* `ants/dlssnr/peexports.py` reads each candidate's **export table** straight
  from the file (bounded reads, no loading - a truncated or hostile image
  yields "no names" instead of a fault);
* staging gives the stage folder the helper pair from the stash **that has a
  CUDA-capable engine**, even when that stash is the lower-priority folder;
  the log line names it: `runtime + helper pair from <path> (2 copied)
  [CUDA-capable engine 'neuroframe_engine.dll']`;
* the engine loader prefers a CUDA-capable build inside a folder too, so a
  folder holding several helper builds cannot silently pick the slow one.

If no build on disk has the entry points, the evidence collector's
**HELPER / ENGINE INVENTORY** section says so and names every helper DLL it
found with its size and hash - that is the list to compare against Gourieff's
`neuroframe_dlls.zip` (the current neuroframe release).

### Duplicate names are detected by content

`nvngx_dlssnr.dll` and `nvngx_dlssnr_RenoDX_4000_series_friendly.dll` on the owner's disk are the
same build under two names (identical SHA-256) — kept that way on purpose, to test the picker. The
rig audit prints `[note] the renaming does not change the build ...` for such a pair, and selection
is unaffected: two names of one build load identically.

## Legacy locations still scanned (graceful fallbacks)

`models/DLSS/dlssnr_<version>/`, flat `models/DLSS/*.dll`,
`models/dlssnr/<version>/`, and the package folder
`custom_nodes/<pack>/ants/dlssnr/dll/`. They exist so an older tree keeps
working; nothing new should be placed there.

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
    ├── sr_staged/<build-name>/nvngx_dlss.dll
    └── ANTs/{shim,appdata}/                    shim PE, NGX log, crash box
```

## Why `staged/` exists at all

Three hard requirements, all about **names** and **folder contents**:

1. **The NGX core looks for the literal file name `nvngx_dlssnr.dll`** inside
   its search paths, and for `nvngx_dlss.dll` in the SR case. A build under
   any other name therefore has to appear under the canonical name somewhere.
2. **The snippet loads its dependencies from its own directory** (NGX loads
   it with `LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR`), so the helper pair has to sit
   next to the runtime copy that gets loaded.
3. **The legacy "neuroframe DLLs" engine is handed a folder** and looks for
   `nvngx_dlssnr.dll` inside it (`dlss5nr_init(ordinal, dll_dir, ...)`), so
   that folder must be self-contained too.

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

Deleting a DLL that ComfyUI currently has loaded fails with a sharing
violation — that is Windows protecting the file, not a problem. Close
ComfyUI and delete again.

## Legacy locations still scanned (graceful fallbacks)

`models/DLSS/dlssnr_<version>/`, flat `models/DLSS/*.dll`,
`models/dlssnr/<version>/`, and the package folder
`custom_nodes/<pack>/ants/dlssnr/dll/`. They exist so an older tree keeps
working; nothing new should be placed there.

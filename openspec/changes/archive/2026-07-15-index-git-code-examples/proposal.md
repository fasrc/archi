## Why

The FASRC `User_Codes` git repository is configured as an ingest source, and its
HPC example files (Slurm job scripts, Fortran/C++/CUDA/R/MATLAB sources, notebooks)
are explicitly allow-listed for collection via `data_manager.sources.git.code_suffixes`.
But **327 of those files — every code example, ~44% of the git source — silently fail
to ingest** and never reach retrieval. The most-requested type on an HPC help desk,
the Slurm job script (`.sbatch`/`.slurm`, 157 files), is entirely absent from the
knowledge base.

The cause is a drift between two independent gates. `code_suffixes` (in
`git_scraper.py`) decides what gets *pulled from git*; `select_loader()` (in
`loader_utils.py`) decides what can be *parsed into text for embedding*. The second
list was never extended to match the first, so these files are collected, stored,
then rejected at embed time as `"Unsupported file format"` and marked `failed`. The
failure is logged per-file but invisible in aggregate, so the gap has gone unnoticed.

## What Changes

- Extend `select_loader()` so every plain-text code/script suffix that git
  collection accepts is loadable as text (via `TextLoader`), eliminating the
  collector-vs-loader drift. Covers the observed failing types: `.sbatch`, `.slurm`,
  `.f90`/`.f`/`.f95`, `.cpp`/`.hpp`, `.cu`, `.r`/`.rmd`, `.m`, `.jl`, `.def`, plus
  `.toml` (already treated as text elsewhere but missing from the loader).
- Treat the collector allow-list as the source of truth for intent: a file type the
  operator configured for collection MUST be loadable, so the two gates cannot
  silently disagree again.
- **Non-goal (this change):** `.ipynb` notebooks. They remain unsupported (still
  fail) because loading them as raw JSON would inject cell-output blobs and metadata
  into the embeddings; a proper notebook-aware loader is a separate change (~12 files,
  see design.md).

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `ingest-processing`: add a requirement that the embed-stage file loader covers the
  code/script file types configured for git collection, so collected code examples
  are embedded rather than failed as an unsupported format.

## Impact

- **Code:** `src/data_manager/vectorstore/loader_utils.py` (`select_loader` suffix
  set). No change to `git_scraper.py`, `code_suffixes`, or any config — the config
  already lists these types.
- **Corpus:** the next re-ingest embeds ~327 previously-failed git files (Slurm/
  Fortran/C++/CUDA/R/MATLAB/etc.). This **changes the corpus**, so it must land
  before a benchmark baseline is locked, not after (see the corpus-change caution in
  `docs/docs/interpreting_benchmark_results.md`).
- **Retrieval:** HPC code examples become answerable; expect movement in the
  `context_*` and source metrics on any question whose gold answer lives in a code
  file. This is the intended effect, not noise.
- **No breaking changes**, no new dependencies (`TextLoader` is already imported).

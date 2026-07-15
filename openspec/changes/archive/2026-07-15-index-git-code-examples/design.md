## Context

Ingest applies two independent gates to a git-sourced file:

1. **Collection gate** — `GitScraper._is_allowed_suffix()` checks the file suffix
   against `data_manager.sources.git.code_suffixes` (config). Disallowed files are
   skipped at scrape time and never enter the `documents` table.
2. **Embed gate** — `select_loader()` (`loader_utils.py`) maps a suffix to a
   LangChain document loader. If it returns `None`, `_add_to_postgres` marks the
   document `failed` with `"Unsupported file format"` (`manager.py:465`).

The config's `code_suffixes` already lists the HPC example types
(`.sbatch .slurm .f90 .f .f95 .cu .R .Rmd .m .jl .cpp .def .ipynb .toml` …), so those
files pass gate 1 and are stored. But `select_loader`'s supported set
(`.txt .c .sh .h .php .yaml .yml .json .csv .tsv .log .rst .md .py .html .htm .pdf`)
was never extended to match, so they all fail gate 2. Measured on the current dev
corpus: **327 failed git documents, 100% `"Unsupported file format"`**, top types
`.sbatch` (139), `.f90` (53), `.cpp` (27), `.R` (21), `.m` (19), `.slurm` (18),
`.cu` (18), `.def` (9), `.jl` (3). The web/KB corpus (549 docs) has zero failures —
this is purely the git-code path.

## Goals / Non-Goals

**Goals:**
- Every plain-text code/script suffix accepted by collection is loadable at embed
  time, so collected code examples embed instead of failing.
- The collector allow-list and the loader can no longer silently drift apart.

**Non-Goals:**
- Notebook-aware `.ipynb` parsing (deferred — see Decisions D3).
- Language-aware chunking/splitting. Loading returns raw source text; chunking is a
  separate downstream concern unchanged by this design.
- Any change to `code_suffixes`, `git_scraper.py`, or deployment config.

## Decisions

### D1: Extend `select_loader`'s text-suffix set with an explicit literal list

Add the missing plain-text suffixes to the existing `TextLoader` branch in
`select_loader`. `TextLoader` reads the file as UTF-8 text — correct for source code
and job scripts, whose value for retrieval is their literal contents.

The list spans two groups (see the spec): the suffixes in `GitScraper`'s **default**
`code_suffixes` that the loader is currently missing (`.js .ts .tsx .jsx .java .go
.rs .sql .cpp .hpp .toml`), plus the **FASRC HPC additions** (`.sbatch .slurm .f90
.f .f95 .cu .r .rmd .m .jl .def`). Covering the shipped default — not only the types
failing in today's corpus — is deliberate: `.js/.go/.sql/.cpp/.toml` are collected by
default but currently unloadable too; they simply happen to be absent from
`User_Codes`. Fixing them now makes the D4 parity invariant genuinely hold.

- **Alternative — dynamic:** have `select_loader` read `code_suffixes` from config and
  return `TextLoader` for anything in it. Rejected: `select_loader` is a pure,
  config-free lookup reused by several modules (`load_text_from_path`,
  `load_doc_from_path`); threading config in would widen its contract and couple the
  loader to the git-source schema. The parity we want is enforced by a test (D4), not
  by runtime coupling.
- **Alternative — catch-all fallback:** return `TextLoader` for any unknown suffix.
  Rejected: it would text-load genuine binaries (`.png`, `.o`, `.mod`, `.h5`),
  producing garbage embeddings. The allow-list is deliberately explicit; the guard
  that returns `None` for unsupported types is a feature, not a bug.

### D2: `TextLoader` for all added suffixes, not language-specific loaders

`.py` currently uses `PythonLoader`, but that loader also just returns the file text
(it adds no parsing we use at embed time). For consistency and zero new dependencies,
all added code suffixes use `TextLoader`. Language-aware handling, if ever wanted,
belongs in the splitter, not the loader.

### D3: Exclude `.ipynb` from this change

Notebooks are JSON documents whose cells carry base64 image outputs, execution
metadata, and stream text. `TextLoader` would embed all of that raw, polluting
retrieval. A correct fix extracts source + markdown cells (e.g. via `nbconvert` or
`jq`), which is a separate change (~12 files affected). Until then `.ipynb` stays in
the failed set — no worse than today.

### D4: A parity test prevents future drift

Add a unit test asserting that every code/script suffix git collection accepts is
loadable by `select_loader`. **`git_scraper.py` cannot be imported into a unit test**
— it runs `get_global_config()` at module load, which raises `ConfigNotReadyError`
without an initialized `PostgresServiceFactory` — so the test cannot read
`GitScraper`'s default list directly. The guard is therefore a maintained copy
(`REQUIRED_LOADABLE_SUFFIXES` in the test) of the shipped default plus the HPC
additions, with a comment binding it to `git_scraper.py`. It still fails the gate
(`scripts/gate.sh`) if a future edit removes a loader for a required type.

Making `git_scraper` unit-importable (lazy module-level config) would allow a true
import-based parity check and is worth a separate cleanup; it is out of scope here
because touching `git_scraper.py` at all would add import lines that no unit test can
cover, failing the patch-coverage gate for an unrelated refactor.

## Risks / Trade-offs

- **A collected file is not valid UTF-8 text** → `TextLoader.load()` raises; the
  existing `try/except` around `loader.load()` (`manager.py:471-478`) already catches
  it and records the real decode error. Worst case is the same `failed` state as
  today, with a clearer message. The already-supported `.c`/`.sh` rely on this same
  path.
- **Corpus change vs. benchmark baseline** → re-ingesting adds ~327 documents, which
  changes retrieval results. Mitigation: land and re-ingest **before** locking any
  benchmark baseline, per the corpus-change caution in
  `docs/docs/interpreting_benchmark_results.md`. Do not compare a post-change run to a
  pre-change one.
- **`.ipynb` still fails** → accepted and documented; a known, bounded gap.
- **Over-broadening** → mitigated by the explicit list (D1) plus the binary-still-
  returns-`None` scenario in the spec.

## Migration Plan

1. Land the `select_loader` change + parity test through the gate (TDD: failing test
   first — a `.sbatch` path currently returns `None`).
2. Redeploy dev via `deploy/fasrc-dev/scripts/redeploy.sh`. `reset_collection: true`
   truncates and re-embeds the whole corpus; the previously-failed code files now
   embed. Verify: `SELECT ingestion_status, count(*) FROM documents WHERE
   source_type='git' GROUP BY 1` — `failed` should drop by ~315 (327 minus the ~12
   `.ipynb`).
3. **Rollback:** revert the one-function change and redeploy. The code files return to
   `failed`; no schema or data migration is involved, so rollback is clean.

## Open Questions

- Should `.ipynb` support be filed as a fast-follow now, or wait until retrieval
  demand for notebook content is shown? (Leaning: file a tracking issue, don't build
  yet.)

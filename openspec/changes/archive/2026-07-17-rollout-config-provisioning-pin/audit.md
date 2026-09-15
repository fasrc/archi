# Post-rollout ingestion audit (independent, 2026-07-17)

Independent `ingestion-verifier` pass (read-only) on the post-rollout corpus. It confirms
the rollout's primary goal and surfaces pre-existing corpus-quality artifacts now made
visible by the fresh re-ingest. **None of the issues are caused by the pin change** — they
are crawler/embed behaviors any redeploy would reproduce — but they are now measured.

## Rollout confirmed clean (primary goal met)
- Orphan chunks 0, orphan parent_nodes 0, non-terminal (pending/embedding) 0, embedded
  docs with 0 chunks 0, is_deleted 0. `docs_with_chunks = 1286 = 738 git + 548 web`.
- No stale-from-older-crawl documents; no foreign hosts.
- **Both git sources present**: User_Codes 718 embedded (+19 failed), ood-documentation
  20 embedded. Config at pin `98f9bd22`.

## Corrections to my earlier numbers
- **NLTK race is 8 files, not 7** — the 1 web failure (`docs.rc.fas/kb/optimization`) is
  the same `_LazyCorpusLoader__*` race (log has 6 `__args` + 2 `__reader_cls` = 8; 7 git +
  1 web). I counted only the git `__reader_cls` lines.
- **Manifest is 219 docs.rc.fas + 148 slurm.schedmd + 1 wikipedia + 2 git** (370 lines) —
  I described it as "219 KB pages," undercounting the slurm/wiki web seeds.

## Issues found (all pre-existing; ranked by retrieval impact)
1. **182 slash-redirect duplicates (highest impact).** docs.rc.fas pages exist in both
   `/path` and `/path/` form (301 no-slash→slash), distinct `resource_hash` each →
   **~1,154 redundant chunks** polluting retrieval. Fresh this run (not a migration
   leftover). Hash/URL dedup structurally can't catch slash variants. **A plain re-ingest
   does NOT fix it** (`reset_collection=true` truncates `document_chunks`, not
   `documents`). Durable fix = crawler trailing-slash canonicalization for docs.rc.fas.
2. **NLTK LazyCorpusLoader parallelism race (8 files: 7 git + 1 web).** Flaky under
   `parallel_workers=32`. Caused a **2-file KB regression**: `mkltest.f90` and
   `blas_test.f90` were embedded in the 2026-07-15 crawl, truncated by this run, then
   raced → now `failed`, 0 chunks. Recoverable by a re-run; permanent fix = serialize
   NLTK corpus init or drop embed-stage workers.
3. **2 silently-dropped seeds** (no document AND no `failed` row → no retry signal):
   `slurm.schedmd.com/slurmrestd.html`, `en.wikipedia.org/wiki/Annie_Jump_Cannon`.
4. **12 `.ipynb`** — systemic loader gap, already Issue #109 (one is a
   `.ipynb_checkpoints` junk artifact).

## Bearing on the RAGAS re-baseline
The slash-dups (~1,154 redundant chunks) and the 2 dropped files are now a KNOWN corpus
state. A baseline taken now bakes them in. Decision for the operator: baseline as-is
(documents current reality) vs. fix slash-dup canonicalization first (cleaner instrument).

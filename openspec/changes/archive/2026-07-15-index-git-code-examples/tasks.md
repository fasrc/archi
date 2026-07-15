## 1. Failing tests (red first)

- [x] 1.1 In `tests/unit/` (new `test_loader_utils.py` or existing loader test),
  add a test asserting `select_loader("submit.sbatch")` and `select_loader("job.slurm")`
  each return a non-`None` `TextLoader`. Watch it fail (currently returns `None`).
- [x] 1.2 Add tests for the remaining HPC-addition suffixes — `.f90 .f .f95 .cu
  .r .rmd .m .jl .def` — each returns a non-`None` text loader.
- [x] 1.3 Add a case-insensitivity test: `select_loader("SOLVER.F90")` returns a
  loader equivalent to the lowercase form.
- [x] 1.4 Add a negative guard test: `select_loader("figure.png")` still returns
  `None` (the change must not become a catch-all).
- [x] 1.5 Add the drift-guard/parity test: assert every suffix in a maintained list
  (`REQUIRED_LOADABLE_SUFFIXES` = shipped default + HPC additions) is loadable by
  `select_loader`. (`git_scraper.py` can't be imported in a unit test — it calls
  `get_global_config()` at import — so the list is a hand-synced copy, not an import.
  See design D4.)

## 2. Implementation

- [x] 2.1 In `src/data_manager/vectorstore/loader_utils.py`, extend the `TextLoader`
  branch of `select_loader` to include the shipped-default gaps
  (`.js .ts .tsx .jsx .java .go .rs .sql .cpp .hpp .toml`) and the HPC additions
  (`.sbatch .slurm .f90 .f .f95 .cu .r .rmd .m .jl .def`) — lowercase; suffix is
  already lowercased at line 27.
- [x] 2.2 Leave `.ipynb` out (documented non-goal, D3). Do not add a catch-all
  fallback.
- [x] 2.3 Run the new tests green.

## 3. Gate

- [x] 3.1 `bash scripts/gate.sh` — black 24.10.0 + isort 6.0.1 clean, pytest passing,
  diff-cover `--fail-under=80` vs `origin/dev`. Do not bypass with `--no-verify`.
- [x] 3.2 Commit on a branch off `origin/dev` (never commit to `dev` directly),
  lowercase message, no attribution trailers.

## 4. Deploy and verify the corpus effect

- [x] 4.1 Redeploy dev: `deploy/fasrc-dev/scripts/redeploy.sh` (`reset_collection: true`
  re-embeds the whole corpus with the new loader).
- [x] 4.2 Verify the failure drop: `SELECT ingestion_status, count(*) FROM documents
  WHERE source_type='git' GROUP BY 1` — `failed` drops by ~315 (the 327 minus ~12
  `.ipynb`), and the added code files show `embedded`.
- [x] 4.3 Spot-check retrieval: confirm at least one Slurm-script chunk is now
  returned for a relevant query (e.g. a `.sbatch` example), proving the files reached
  the vector store.

## 5. Follow-up (not blocking)

- [x] 5.1 File a tracking issue for notebook-aware `.ipynb` loading (nbconvert/jq
  source+markdown extraction), referencing D3 in this change's design.

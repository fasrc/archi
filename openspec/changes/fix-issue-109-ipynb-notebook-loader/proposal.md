## Why

`.ipynb` is in the dev deployment's `data_manager.sources.git.code_suffixes`, so git
collection harvests notebooks — but `select_loader()` has no `.ipynb` branch, so every
one of them falls through to `return None` and is marked `failed` with
`"Unsupported file format:"` at `src/data_manager/vectorstore/manager.py:465`. As of
2026-07-15 that is **12 documents in the dev corpus that are collected but never
embedded**.

This was deliberately deferred from PR #108 (decision **D3** of
`index-git-code-examples`): notebooks are JSON whose cells carry base64 image outputs,
execution metadata, and stream text, so loading them with `TextLoader` would pollute
retrieval. D3 said the correct fix — extract source + markdown cells only — is its own
change. This is that change.

## What Changes

- Add an `.ipynb` branch to `select_loader()` in
  `src/data_manager/vectorstore/loader_utils.py`, returning
  `NotebookLoader(str(path), include_outputs=False)` from
  `langchain_community.document_loaders`.
- Notebook loading extracts **cell source + markdown only**; execution-output blobs are
  excluded, satisfying D3's "clean extraction" bar.
- Add `.ipynb` to `REQUIRED_LOADABLE_SUFFIXES` in `tests/unit/test_loader_utils.py`, so
  the existing parity drift-guard now covers notebooks.
- No dependency change: `NotebookLoader`'s runtime dep `pandas==2.3.2` is already pinned
  in `requirements/requirements-base.txt:72` and `pyproject.toml:33`.
- No breaking changes. No change to `code_suffixes`, `git_scraper.py`, or deployment
  config.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `ingest-processing`: adds a requirement that notebook files collected by git are
  loadable at embed time, extracting cell source and markdown while excluding execution
  outputs. Complements the existing "Embed-stage loader covers configured code file
  types" requirement, which is scoped to *plain-text* suffixes and deliberately excluded
  `.ipynb`.

## Impact

- **Code**: `src/data_manager/vectorstore/loader_utils.py` (one import, one branch).
- **Tests**: `tests/unit/test_loader_utils.py` (new notebook tests + parity list entry).
- **Dependencies**: none added — `pandas` already pinned.
- **Corpus**: re-ingest embeds 12 new documents, which changes retrieval results. Per
  `docs/docs/interpreting_benchmark_results.md` this is a **corpus change**: it must land
  before any benchmark baseline is locked, and a post-change run must not be compared to
  a pre-change one.
- **Out of scope (post-merge, requires the dev host)**: the
  `docker exec data-manager-dev` import proof, the dev redeploy, and the post-redeploy
  Postgres check that `.ipynb` `failed` goes 12 → 0 and `embedded` → 12.

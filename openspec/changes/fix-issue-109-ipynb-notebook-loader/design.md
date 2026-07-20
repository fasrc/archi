## Context

`select_loader()` in `src/data_manager/vectorstore/loader_utils.py:19` is a
dispatch-by-extension function: a set-membership branch returning `TextLoader`, then
`.py` → `PythonLoader`, `.html`/`.htm` → `BSHTMLLoader`, `.pdf` → `PyPDFLoader`, then a
`logger.error("No loader available for %s", path)` + `return None` fallthrough.
`VectorStoreManager` treats that `None` as fatal for the file and records
`"Unsupported file format:"` with `ingestion_status='failed'`
(`src/data_manager/vectorstore/manager.py:465`).

`.ipynb` is in the dev deployment's `code_suffixes`, so 12 notebooks are collected and
then dropped at embed time (measured on the dev corpus 2026-07-15).

PR #108 closed this drift for plain-text code suffixes and added a parity drift-guard,
`REQUIRED_LOADABLE_SUFFIXES` in `tests/unit/test_loader_utils.py:36`, exercised by the
parametrized `test_every_collected_code_suffix_is_loadable` at line 96. It deliberately
excluded `.ipynb` — decision **D3** of `index-git-code-examples`: notebooks are JSON
carrying base64 image outputs, execution metadata, and stream text, and `TextLoader`
would embed all of it verbatim.

## Goals / Non-Goals

**Goals:**

- `select_loader()` returns a working loader for `.ipynb`.
- Loaded notebook content carries cell source + markdown and **excludes** execution
  outputs, meeting D3's clean-extraction bar.
- `.ipynb` joins the parity guard so the collection/loader drift cannot silently reopen.

**Non-Goals:**

- Notebook-aware chunking or splitting. Loading returns extracted text; chunking is a
  separate downstream concern.
- Any change to `code_suffixes`, `git_scraper.py`, or deployment config.
- Rendering or preserving notebook outputs anywhere in the pipeline.
- The dev redeploy and its Postgres verification (post-merge, needs the dev host).

## Decisions

### D1: Use `langchain_community`'s `NotebookLoader` rather than hand-rolling extraction

`NotebookLoader` is already available from the pinned `langchain_community` dependency,
with signature `NotebookLoader(path, include_outputs=False, max_output_length=10,
remove_newline=False, traceback=False)`. Its **default `include_outputs=False` is exactly
the D3 requirement** — it emits markdown and code source and omits output text.

We pass `include_outputs=False` explicitly anyway, so the behavior the spec depends on is
visible at the call site and cannot silently flip if the library changes its default.

*Alternatives considered.* (a) Parse the notebook JSON ourselves and concatenate
`cell.source` for `markdown`/`code` cells — no new surface, but re-implements format
handling (nbformat versions, `source` as str vs list-of-str) that the library already
handles. (b) Shell out to `nbconvert --to markdown` — heavier, adds a subprocess and a
temp-file dance to a pure function. Neither earns its cost against a dependency we
already ship.

### D2: Add the branch as a distinct `if`, immediately after the `.py` branch

`.ipynb` cannot join the `TextLoader` set literal — that is the whole point of D3. It
gets its own branch, placed next to `.py` because notebooks are Python source in
practice, keeping the code-oriented branches adjacent:

```python
if file_extension == ".ipynb":
    return NotebookLoader(str(path), include_outputs=False)
```

Extension matching reuses the existing `file_extension = path.suffix.lower()` normalization
at the top of the function, so case-insensitivity is inherited rather than re-implemented.

### D3: No dependency change — `pandas` is already pinned

`NotebookLoader` imports `pandas` at load time. It is **already** pinned at `2.3.2` in
both `requirements/requirements-base.txt:72` and `pyproject.toml:33`, and
`requirements-base.txt` carries a comment explaining pandas must live there for
collection to succeed. **Do not add a redundant pin** — it would be mechanical churn on a
single-behavior PR.

### D4: The test must exercise the branch, not just assert the type

Unlike PR #108's additions (set-literal entries, which coverage.py did not score), a new
`if` + `return` is **new executable statements** and *will* be measured by
`diff-cover --fail-under=80` against `origin/dev`. A test that only asserts
`type(select_loader("x.ipynb")).__name__ == "NotebookLoader"` does cover both lines, but
it would not prove the D3 behavior. The spec's substantive scenario — write a real
notebook fixture with a markdown cell, a code cell, and a recorded stdout output blob,
then assert `.load()[0].page_content` contains the source and **does not contain** the
output string — covers the branch *and* the requirement. Both tests are written red
first.

## Risks / Trade-offs

- **`NotebookLoader`'s extraction format could change across `langchain_community`
  versions** (e.g. how cells are joined) → the behavioral test asserts *containment* of
  source and *absence* of output text, not an exact rendering, so it stays green across
  cosmetic format changes while still failing if outputs leak back in.
- **A future `langchain_community` bump could flip the `include_outputs` default** → we
  pass it explicitly, and the output-exclusion test fails loudly if the semantics change.
  If bumping langchain versions, re-verify the API via the Context7 MCP tools rather than
  from memory.
- **Corpus change**: 12 new documents enter the index at the next re-ingest, shifting
  retrieval results. Per `docs/docs/interpreting_benchmark_results.md`, this must land
  before a benchmark baseline is locked, and post-change runs must not be compared to
  pre-change ones → call it out in the PR body so a benchmark run is not misread.
- **Notebooks with only outputs and no source** (rare) load to near-empty content →
  acceptable; `load_doc_from_path` already tolerates empty results, and this is strictly
  better than today's hard `failed`.

## Migration Plan

No data migration. On merge and the next dev redeploy + re-ingest, the 12 `.ipynb`
documents move from `ingestion_status='failed'` to `embedded`. Rollback is reverting the
branch; notebooks return to the failed set, which is the status quo ante.

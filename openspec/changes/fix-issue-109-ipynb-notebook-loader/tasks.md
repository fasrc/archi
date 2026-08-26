## 1. Red tests (write first, watch them fail)

- [x] 1.1 In `tests/unit/test_loader_utils.py`, add a test asserting
      `select_loader("analysis.ipynb")` returns a `NotebookLoader` instance (not `None`).
      Run `python -m pytest tests/unit/test_loader_utils.py -q` and confirm it FAILS
      (currently returns `None`).
- [x] 1.2 In the same file, add a behavioral test: write a real `.ipynb` fixture under
      `tmp_path` containing (a) a markdown cell, (b) a code cell with distinctive source,
      and (c) a recorded stdout output blob with a distinctive string. Load it via
      `select_loader(...).load()` and assert `page_content` CONTAINS the markdown text and
      the code source, and does NOT contain the output blob string. Confirm it FAILS.
- [x] 1.3 Add `.ipynb` to `REQUIRED_LOADABLE_SUFFIXES` (starts line 36) so the
      parametrized drift guard `test_every_collected_code_suffix_is_loadable` (line 96)
      covers notebooks. Confirm the new parametrization FAILS.

## 2. Implementation (minimum code to go green)

- [x] 2.1 In `src/data_manager/vectorstore/loader_utils.py`, add `NotebookLoader` to the
      existing `from langchain_community.document_loaders import (...)` block (keep isort
      ordering).
- [x] 2.2 Add the branch immediately after the `.py` branch:
      `if file_extension == ".ipynb": return NotebookLoader(str(path), include_outputs=False)`.
      Pass `include_outputs=False` explicitly (design D1) — do not rely on the library
      default.
- [x] 2.3 Run `python -m pytest tests/unit/test_loader_utils.py -q` and confirm all three
      tests from group 1 now PASS.

## 3. Gate and PR

- [x] 3.1 Run `bash scripts/gate.sh` and confirm it exits 0 — black 24.10.0 + isort 6.0.1
      clean, `pytest tests/unit/` green, and diff-cover patch coverage ≥ 80% vs
      `origin/dev`. The new `if`/`return` are measured statements (design D4); if patch
      coverage is short, the tests in group 1 are not exercising the branch.
- [x] 3.2 Commit (lowercase message, e.g. `add ipynb notebook loader to select_loader`).
      No `Co-Authored-By` or session trailers. Never `--no-verify`.
- [x] 3.3 Push the branch and open the PR: `gh pr create --repo fasrc/archi --base dev`
      with `closes #109` in the body. Note in the body that this is a **corpus change**
      (12 new documents at next re-ingest) that must land before any benchmark baseline is
      locked, and list the post-merge dev-host verification steps from the issue as
      outstanding. STOP at the open PR — do not merge.
      PR: https://github.com/fasrc/archi/pull/125 (open, not merged).

## Verification deferred to the dev host (post-merge, NOT part of this change)

These are recorded for the human reviewer; do not attempt them in the loop.

- `docker exec data-manager-dev python -c "from langchain_community.document_loaders import NotebookLoader; import pandas; print('ok')"` prints `ok`
- `deploy/scripts/redeploy.sh`, then
  `SELECT ingestion_status, count(*) FROM documents WHERE source_type='git' AND suffix='ipynb' GROUP BY 1;`
  shows `embedded: 12`, `failed: 0`

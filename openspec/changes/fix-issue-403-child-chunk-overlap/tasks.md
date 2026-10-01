# Tasks — configurable child-chunk overlap (#403)

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
test, watch it fail, write the smallest fix, run the gate, commit. Never end a task with the
suite red, and never use `--no-verify`.

Standing notes for every task:

- **Scope.** Source files this change may edit:
  `src/data_manager/vectorstore/node_parsing.py`, `src/data_manager/vectorstore/manager.py`,
  `src/cli/templates/base-config.yaml`, `scripts/benchmarking/measure_chunk_overlap.py`,
  `docs/docs/configuration.md`. Nothing under `deploy/**`, `.github/**`, or the
  control-plane files is in scope. Do not touch the top-level `chunk_overlap` of the
  `character` path.
- **Read `design.md` first.** It fixes the clamp rule (D2), the parameter name (D3), the
  validation (D4), and the render guard (D5).
- **Commands.** The gate is the project gate command from `CLAUDE.md`. The fast loop is
  `python -m pytest tests/unit/test_node_parsing.py tests/unit/test_vectorstore_manager_hierarchical.py tests/unit/test_base_config_chunking_render.py tests/unit/test_measure_chunk_overlap.py -q`.
- **Format before you stage.** Run black and isort, then `git add`, then commit, and
  confirm `git status` is empty.
- **Append tests at the end of a file** after the last complete test function; never split
  an existing test.

## 1. Parser: configurable overlap with the new clamp

- [x] 1.1 In `tests/unit/test_node_parsing.py`, write failing tests: (a) `_clamped_overlap`
      with an explicit overlap — `_clamped_overlap(512, 500) == 256`,
      `_clamped_overlap(512, 0) == 0`, `_clamped_overlap(16, 20) == 8`,
      `_clamped_overlap(512) == 20`; (b) `build_hierarchical_nodes(...,
      strategy="markdown", child_chunk_size=512, child_chunk_overlap=500)` builds its child
      `SentenceSplitter` with `chunk_overlap=256` and its parent splitter with
      `chunk_overlap=0` (monkeypatch `node_parsing.SentenceSplitter` with a recorder that
      wraps the real class); (c) the sentence path passes
      `min(child_chunk_overlap, min(parent, child) // 2)` to
      `HierarchicalNodeParser.from_defaults` (monkeypatch it the same way), for overlap `0`
      and for overlap 500. Watch them fail. Then implement D2 and D3 in
      `src/data_manager/vectorstore/node_parsing.py`, and update
      `test_clamped_overlap_boundary_values` to the new rule (`_clamped_overlap(20) == 10`,
      `_clamped_overlap(21) == 10`, `_clamped_overlap(200) == 20`, `_clamped_overlap(0) ==
      0`, `_clamped_overlap(1) == 0`) — that change is intended (design D2). Update the
      `CHILD_CHUNK_OVERLAP` comment and the `_clamped_overlap` docstring to say the
      overlap is at most half the size. Gate green; commit.

## 2. Manager: resolve, validate, and pass the value

- [x] 2.1 In `tests/unit/test_vectorstore_manager_hierarchical.py`, write failing tests for
      a new `_resolve_chunk_overlap(chunking_cfg)`: absent key → `CHILD_CHUNK_OVERLAP`;
      `None` → `CHILD_CHUNK_OVERLAP`; `0` → `0`; `64` → `64`; and `-1`, `True`, `"20"`,
      `2.5` each raise `ValueError` whose message contains
      `data_manager.chunking.chunk_overlap`. Add one test that the hierarchical insert path
      passes the resolved value to `build_hierarchical_nodes` as `child_chunk_overlap`
      (reuse the monkeypatch style of the existing `_build_hierarchical_payload` tests in
      that file). Watch them fail. Implement D4 in
      `src/data_manager/vectorstore/manager.py`: the resolver, `self.child_chunk_overlap`
      in `__init__` beside the chunk sizes, and the keyword at the
      `build_hierarchical_nodes` call. Several test helpers build the manager with
      `VectorStoreManager.__new__` and skip `__init__` (`_make_manager` at
      `tests/unit/test_vectorstore_manager_hierarchical.py:169`, and helpers in
      `test_vectorstore_reingest_chunk_refresh.py`, `test_vectorstore_reingest_content_signal.py`,
      `test_vectorstore_manager_batch_commit.py`, `test_ingest_run.py`). Run
      `grep -rn "VectorStoreManager.__new__" tests/unit` and set
      `child_chunk_overlap = CHILD_CHUNK_OVERLAP` in each helper that reaches the
      hierarchical payload path, so no existing test fails with `AttributeError`. Run all of
      `tests/unit/` before the gate. Gate green; commit.

## 3. Template: render the key only when set

- [x] 3.1 In `tests/unit/test_base_config_chunking_render.py`, write failing tests with the
      file's `_render` helper: `chunking.chunk_overlap: 0` renders as `0` (not absent, not
      20); `64` renders as `64`; an unset key is absent from the rendered `chunking` block;
      `None` is absent. Watch them fail. Add the D5 block to
      `src/cli/templates/base-config.yaml` after the `child_chunk_size` block, and extend
      the comment above the size keys to name `chunk_overlap` (default 20, clamped to half
      the child size, hierarchical strategies only). Gate green; commit.

## 4. Measurement script parity

- [ ] 4.1 In `tests/unit/test_measure_chunk_overlap.py`, write a failing parity test:
      for every `chunk_size` in `(8, 16, 39, 40, 41, 48, 512)`, `parent_chunk_size` in
      `(16, 128, 2048)`, and `overlap` in `(0, 1, 20, 64, 500)`, assert
      `clamp_overlap(overlap, chunk_size, parent_chunk_size) ==
      node_parsing._clamped_overlap(min(chunk_size, parent_chunk_size), overlap)`. Watch it
      fail. Change `clamp_overlap` in `scripts/benchmarking/measure_chunk_overlap.py` to
      design D6 and update the module docstring (line 23) and the `clamp_overlap`
      docstring. Run the whole `tests/unit/test_measure_chunk_overlap.py` file; fix any
      test whose expected budget assumed the old rule, and say which ones in the commit
      message. Gate green; commit.

## 5. Docs

- [ ] 5.1 In `docs/docs/configuration.md`, add a row for `chunking.chunk_overlap` (int,
      default `20`) to the chunking table near line 577: the child-splitter overlap in
      tokens for the hierarchical strategies, clamped to half the child size (to half the
      smaller of the two sizes on the `sentence` path), `0` disables it, and a change takes
      effect only on re-ingest. Add `chunk_overlap: 20` to the YAML example below the
      table. Add one sentence that the top-level `chunk_overlap` (line 516) applies only to
      the `character` strategy. Add a sentence to the backward-compatibility note that
      omitting `chunk_overlap` keeps overlap 20, and that child sizes below 40 now get half
      the size. If `mkdocs` is available, run `mkdocs build --strict -f docs/mkdocs.yml` and
      confirm no new warning. Gate green; commit.

## 6. Verify, push, and open the PR

- [ ] 6.1 Run the gate once more on the finished change and confirm it exits 0 with patch
      coverage at or above 80 %. Confirm `git status` is empty. Push with
      `git push -u origin fix/issue-403-child-chunk-overlap` — the branch tracks
      `origin/dev`, so `-u` is required. Open the PR with
      `gh pr create --repo fasrc/archi --base dev`, put `closes #403` in the **body** (a
      closing keyword in the title does not link the issue), and stop. Do not merge.

## 1. Count system-error files in `_add_to_postgres` (red tests and fix in one task each)

- [x] 1.1 Process stage and return contract (design D1, D2). In
      `tests/unit/test_vectorstore_manager_batch_commit.py`, add these tests right AFTER
      `test_embedding_progress_callback_exception_does_not_abort` and BEFORE
      `def _tagging_config` — NOT at the end of the file. Grep each test name first; a
      duplicate `def` silently deletes the older test. Use the existing helpers
      `_setup_flat_manager(monkeypatch, embed_fn=..., loader_fn=...)` and
      `_setup_hierarchical_manager(monkeypatch)`. The inline executor runs files in dict
      order, so `hash-0` is processed first.
      - `test_add_to_postgres_returns_zero_for_an_empty_batch`:
        `manager._add_to_postgres({}) == 0`.
      - `test_add_to_postgres_returns_zero_when_every_file_is_stored`: two files → `0`.
      - `test_add_to_postgres_counts_a_loader_error`: `loader_fn` raises for
        `/tmp/file-0.txt` → `1`, and `catalog.update_ingestion_status` was called with
        `("hash-0", "failed", <the exception text>)`.
      - `test_add_to_postgres_counts_a_load_error`: `loader_fn` returns an object whose
        `load()` raises for `file-0` → `1`.
      - `test_add_to_postgres_counts_a_processing_error`: set
        `manager.text_splitter` so that `split_documents` raises on its first call → `1`
        (this reaches the `future.result()` handler).
      - `test_add_to_postgres_does_not_count_an_unsupported_format`: `loader_fn` returns
        `None` for `file-0` → `0`.
      - `test_add_to_postgres_does_not_count_a_file_with_no_text`: flat path; make the
        first file give only blank chunks (for example, set `manager.text_splitter` to
        return `[SimpleNamespace(page_content="   ", metadata={})]` on its first call) →
        `0`, and `hash-0` was marked `failed` with "No text chunks could be extracted".
      - `test_add_to_postgres_hierarchical_does_not_count_a_file_with_no_text`:
        `_setup_hierarchical_manager`, then monkeypatch
        `manager_module.build_hierarchical_nodes` to return `[]` → `0`.
      Run `python -m pytest -q tests/unit/test_vectorstore_manager_batch_commit.py` and
      confirm the new tests that expect a number FAIL (today the method returns `None`).
      Then, in the same task, make design D2 in
      `src/data_manager/vectorstore/manager.py`, EXCEPT the three store-stage `except`
      additions (task 1.2): add `_FILE_SYSTEM_ERROR` above `SUPPORTED_DISTANCE_METRICS`;
      `-> int` and the docstring sentence; `return 0` on the empty batch; return
      `_FILE_SYSTEM_ERROR` from the two load handlers in `process_file`; add
      `failed_hashes`; count the `future.result()` exception and the marker in the batch
      loop; `return len(failed_hashes)` at the end. Re-run the test file and confirm ALL
      tests pass. Run `black` and `isort` on the two changed files BEFORE `git add`. Run
      `bash scripts/gate.sh` in the foreground and commit only when it is green
      (message: `count system-error files in _add_to_postgres (#603)`).

- [x] 1.2 Store stage (design D1, D2). In the same test file, add these tests right AFTER
      the tests of task 1.1 and BEFORE `def _tagging_config`, with unique names (grep
      first):
      - `test_add_to_postgres_counts_an_embed_error`: `embed_fn` raises on its first call;
        two files → `1`.
      - `test_add_to_postgres_counts_a_store_error`: re-patch
        `manager_module.psycopg2.extras.execute_values` after `_setup_flat_manager` so it
        raises on its first call; two files → `1`. Also assert that `hash-1` is still
        marked `embedded`: the cursor
        (`fake_conn.cursor.return_value.__enter__.return_value`) got an `execute` call
        whose SQL contains `ingestion_status = 'embedded'` with params `("hash-1",)`.
      - `test_add_to_postgres_hierarchical_counts_a_store_error`:
        `_setup_hierarchical_manager`, then set `manager._insert_hierarchical_file` to a
        function `(cursor, document_id, parents)` that raises on its first call and
        returns `1` after; two files → `1`.
      - `test_add_to_postgres_counts_every_file_when_all_fail_to_store`: `execute_values`
        always raises; two files → `2`.
      Run the test file and confirm the new tests FAIL (they return `0`). Then, in the
      same task, add `failed_hashes.add(filehash)` to each of the three store-stage
      `except` blocks of `_add_to_postgres` (design D2). Re-run the test file and confirm
      ALL tests pass. Run `black` and `isort` on the changed files BEFORE `git add`. Run
      `bash scripts/gate.sh` in the foreground and commit only when it is green
      (message: `count store-stage failures in _add_to_postgres (#603)`).

## 2. Record the run as failed (red tests and fix in one task)

- [x] 2.1 Run status (design D3, D4). In the same test file, add a helper and tests right
      AFTER the tests of task 1.2 and BEFORE `def _tagging_config`, with unique names
      (grep first). The helper `_run_update_vectorstore(monkeypatch, manager, files)`
      sets `manager.data_path = "/tmp"` and monkeypatches: `manager.fetch_collection` to
      return an object whose `count()` returns `0`;
      `manager_module.PostgresCatalogService.load_sources_catalog` to a `staticmethod`
      that returns `{}`; `manager._collect_postgres_hashes` → `set()`;
      `manager._collect_indexed_documents` → `files`; `manager._collect_stale_hashes` →
      `set()`; `manager._record_ingest_run` → a function that appends `status` to a list.
      It returns `(manager.update_vectorstore(), recorded_statuses)`. Tests, two files
      each (`{"hash-0": "/tmp/file-0.txt", "hash-1": "/tmp/file-1.txt"}`):
      - `test_update_vectorstore_records_failed_when_one_of_two_files_fails_to_store`:
        `execute_values` raises on its first call → returns `"failed"`, recorded
        `["failed"]`, and `hash-1` is still marked `embedded` (as in task 1.2).
      - `test_update_vectorstore_records_updated_when_every_file_is_stored` →
        `"updated"`, recorded `["updated"]`.
      - `test_update_vectorstore_records_failed_on_a_load_error` → `"failed"`.
      - `test_update_vectorstore_records_failed_on_an_embed_error` → `"failed"`.
      - `test_update_vectorstore_records_failed_when_every_file_fails` → `"failed"`.
      - `test_update_vectorstore_hierarchical_records_failed_on_a_store_error` →
        `"failed"`.
      - `test_update_vectorstore_records_updated_for_an_unsupported_format` →
        `"updated"`.
      - `test_update_vectorstore_records_updated_for_a_file_with_no_text` →
        `"updated"`.
      Run the test file and confirm the five tests that expect `"failed"` FAIL (they get
      `"updated"`), and the three that expect `"updated"` pass. Then, in the same task,
      make design D3 in `_sync_vectorstore`, and design D4 in
      `tests/unit/test_vectorstore_reingest_chunk_refresh.py`: change `added =
      MagicMock()` to `added = MagicMock(return_value=0)` in
      `test_stale_hash_is_removed_and_re_added` and
      `test_sync_vectorstore_threads_embedding_progress_to_add_to_postgres` only. Run
      `python -m pytest -q tests/unit/test_vectorstore_manager_batch_commit.py
      tests/unit/test_vectorstore_reingest_chunk_refresh.py tests/unit/test_ingest_run.py`
      and confirm ALL tests pass. Run `black` and `isort` on the changed files BEFORE
      `git add`. Run `bash scripts/gate.sh` in the foreground and commit only when it is
      green (message: `record an ingest run as failed when a file hits a system error
      (#603)`).

## 3. Docs

- [x] 3.1 In `docs/docs/services.md`, add the two sentences of design D5 at the end of the
      bullet that starts with `**⚠ the most recent ingest attempt failed at …**`, and
      change nothing else. Run `bash scripts/gate.sh` in the foreground and commit when it
      is green (message: `document when an ingest run is failed (#603)`).

## 4. Publish

- [ ] 4.1 Tick this box, commit the tick, then push the branch:
      `git push -u origin fix/issue-603-ingest-run-fails-on-file-failure`. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head
      fix/issue-603-ingest-run-fails-on-file-failure` with the title `fix(ingest): record
      an ingest run as failed when a document hits a system error` and `Closes #603` in
      the BODY (not the title). Pass the body with `--body` or a file under `/tmp`; do NOT
      commit a PR-body file to the repository. In the body, copy the D1 table from
      `design.md`, and state the behavior change: a run with a system-error file is now
      `failed`, so the status board keeps the previous successful run and the tracked
      upload and source-refresh tasks report an error. STOP at the open PR — do not
      merge.

# Tasks — delete unreferenced parent nodes on re-ingest (#411)

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
test, watch it fail, write the smallest fix, run the gate, commit. Never end a task with the
suite red, and never use `--no-verify`.

Standing notes for every task:

- **Scope.** Files this change may edit: `src/data_manager/vectorstore/parent_nodes.py`
  (new), `src/data_manager/vectorstore/manager.py`,
  `src/data_manager/vectorstore/schema.py` (design D5c only),
  `tests/unit/test_parent_nodes.py` (new),
  `tests/unit/test_vectorstore_manager_hierarchical.py`,
  `tests/unit/test_vectorstore_schema.py`, other `tests/unit/` fakes only if an existing
  test breaks, and `docs/docs/troubleshooting.md`. Do NOT edit
  `src/utils/benchmark_provenance.py`, `src/bin/service_benchmark.py`,
  `src/interfaces/chat_app/app.py`, the retriever, `init.sql`, `deploy/**`, `.github/**`,
  or any control-plane file.
- **Read `design.md` first.** It fixes the predicate (D1), the module API (D3), the three
  call sites (D4, D5, D5b), the index (D5c), the test fake (D6), and the docs text (D7).
- **Fast loop:** `python -m pytest tests/unit/test_parent_nodes.py tests/unit/test_vectorstore_manager_hierarchical.py tests/unit/test_vectorstore_schema.py -q`.
  Run all of `tests/unit/` before the gate.
- **Format before you stage.** Run black and isort, then `git add`, then commit, and
  confirm `git status` is empty.
- **Append tests at the end of a file** after the last complete test function; never split
  an existing test. Never reuse an existing test name.

## 1. Helper module

- [x] 1.1 Create `tests/unit/test_parent_nodes.py` with the stateful fake cursor of design
      D6 (parents with `id`, `document_id`, `metadata.resource_hash`; chunks with
      `collection` and `parent_id`; documents with `id` and `resource_hash`; a
      `table_exists` flag; `rowcount`). Write failing tests: (a)
      `delete_unreferenced_parents(cursor, 7)` deletes the document-7 parents that no chunk
      references, keeps a document-7 parent that a chunk in ANOTHER collection references,
      keeps every parent of document 8, and returns the deleted count; (b)
      `delete_unreferenced_parents(cursor, None)` returns `0` and runs no SQL; (c)
      `delete_unreferenced_parents_for_resource(cursor, "hash-7")` has the same keep and
      delete results for the document whose `resource_hash` is `"hash-7"`, ALSO deletes an
      unreferenced parent with a `NULL` `document_id` whose metadata `resource_hash` is
      `"hash-7"`, keeps such a parent for another hash, passes the params
      `("hash-7", "hash-7")`, and returns `0` for an unknown hash; (d) a `rowcount` of `-1`
      returns `0`; (e) both delete constants contain `NOT EXISTS`,
      `metadata->>'parent_id' = p.id::text`, and no `collection` text; (f)
      `parent_table_exists` returns the fake's flag; (g) `truncate_parent_nodes` runs
      `TRUNCATE_PARENT_NODES`; (h) no helper calls `commit`. Watch them fail. Create
      `src/data_manager/vectorstore/parent_nodes.py` per design D3 with a module docstring
      that cites #411. Gate green; commit.

## 2. Call site 1 — after the new chunks of a document

- [x] 2.1 In `tests/unit/test_vectorstore_manager_hierarchical.py`, write failing tests
      (reuse the style of `test_add_to_postgres_hierarchical_persists_parents_and_children`,
      but make the `execute_values` capture ALSO append a marker such as
      `("EXECUTE_VALUES document_chunks", None)` to `fake_cursor.executed`, so one ordered
      list holds every statement): (a) a hierarchical `_add_to_postgres` run executes
      `DELETE_UNREFERENCED_PARENTS_FOR_DOCUMENT` with the file's `document_id` AFTER the
      chunk-insert marker and BEFORE `RELEASE SAVEPOINT`; (b) with a `None` `document_id`,
      it runs `DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE` with the file hash instead; (c) if
      the parent delete raises, the file rolls back to its savepoint and is marked
      `failed`; (d) one INFO record reports the summed deleted count (use `caplog`); (e) a
      non-hierarchical run executes no parent delete. Add a `rowcount` attribute (default
      `0`) to the file's `_FakeCursor`. Watch (a), (b), and (d) fail. Implement design D4 in
      `manager.py` as a thin call site that imports the helper. Run all of `tests/unit/`;
      fix any fake that breaks and name it in the commit message. Gate green; commit.

## 3. Call site 2 — after the chunk delete for a resource

- [x] 3.1 In `tests/unit/test_vectorstore_manager_hierarchical.py`, write failing tests for
      `_remove_from_postgres` with a fake `psycopg2.connect` (monkeypatch the `psycopg2`
      that `manager.py` imports): (a) with the table present, `PARENT_TABLE_EXISTS` runs
      once, then for each hash the chunk delete runs first, then
      `DELETE_UNREFERENCED_PARENTS_FOR_RESOURCE` with that hash, then one `commit`; (b) the
      call runs when `hierarchical_chunking` is `False` too; (c) with the table absent, no
      statement that names `document_parent_nodes` runs, the chunk deletes run, and the
      commit happens; (d) one INFO record reports the summed count and the number of
      removed resources; (e) the connection is closed if the parent delete raises. Watch
      them fail. Implement design D5. Gate green; commit.

## 4. Call site 3 and the index

- [x] 4.1 In `tests/unit/test_vectorstore_manager_hierarchical.py`, write failing tests for
      `delete_existing_collection_if_reset` with `reset_collection: True` and a fake
      connection (read the method first; it sets `conn.autocommit` after the commit, so the
      fake must allow that): (a) with the table present, `TRUNCATE_PARENT_NODES` runs right
      after `TRUNCATE TABLE document_chunks CASCADE` and before the commit; (b) with the
      table absent, it does not run. In `tests/unit/test_vectorstore_schema.py`, write a
      failing test that `ensure_hierarchical_schema` also executes
      `CREATE INDEX IF NOT EXISTS idx_chunks_parent_id ON document_chunks ((metadata->>'parent_id'))`
      (compare with whitespace collapsed); update any existing test there that pins the
      exact statement count or list, and say so in the commit message. Watch them fail.
      Implement design D5b and D5c. Gate green; commit.

## 5. End-to-end behaviour on the stateful fake

- [x] 5.1 In `tests/unit/test_parent_nodes.py`, teach the stateful fake the parent
      `INSERT ... RETURNING id` (new serial id per row), `fetchone`, the chunk-insert path
      (monkeypatch `execute_values` in the `manager` module to append chunks with their
      `metadata.parent_id`), the chunk delete by `resource_hash` and collection, and
      `SAVEPOINT` and `RELEASE SAVEPOINT` as no-ops. With a `VectorStoreManager` built like
      `_make_manager` in `test_vectorstore_manager_hierarchical.py` (copy the helper; do
      not import a test module), write tests that: (a) run the hierarchical
      `_add_to_postgres` twice for one document, and assert one parent row per
      `parent_index` for that document and that every remaining chunk's `parent_id` names a
      surviving parent; (b) run the `_sync_vectorstore` order, `_remove_from_postgres` then
      `_add_to_postgres`, and assert the same; (c) keep a parent of the document that a
      chunk in another collection references, through both orders. These tests can pass on
      the first run, because tasks 1-3 already did the work; if one fails, fix the code, not
      the test. Gate green; commit.

## 6. Docs

- [x] 6.1 Add the section of design D7 to `docs/docs/troubleshooting.md` before
      `## Getting Help`: the count query, the CAUTION (text from D7), the cleanup
      statement, and the paragraph about the git and Jira removal gap. If `mkdocs` is
      available, run `mkdocs build --strict -f docs/mkdocs.yml` and confirm no new warning
      or INFO line about a link. Gate green; commit.

## 7. Verify, push, and open the PR

- [ ] 7.1 Confirm `grep -rn "DELETE FROM document_parent_nodes" src/` lists only
      `src/data_manager/vectorstore/parent_nodes.py`. Confirm `git diff origin/dev --stat`
      does not touch `benchmark_provenance.py`, `service_benchmark.py`, or `app.py`. Run the
      gate once more and confirm it exits 0 with patch coverage at or above 80 %. Confirm
      `git status` is empty. Push with
      `git push -u origin fix/issue-411-prune-orphan-parent-nodes` — the branch tracks
      `origin/dev`, so `-u` is required. Open the PR with
      `gh pr create --repo fasrc/archi --base dev`, put `closes #411` in the **body** (a
      closing keyword in the title does not link the issue), and say in the body that the
      unit tests use a fake cursor and do not run the SQL against Postgres, that the git and
      Jira removal paths are a known gap tracked in #600, and that the dev
      cleanup is a post-merge operator step. Then stop. Do not merge.

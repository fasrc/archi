## 1. Failing test (RED)

- [x] 1.1 Add a unit test in `tests/unit/test_vectorstore_reingest_chunk_refresh.py` (or a sibling `test_vectorstore_reingest_content_signal.py` mirroring its style) asserting that a document re-ingested with CHANGED CONTENT but the SAME filename and SAME identity hash is returned by `_collect_stale_hashes()` (old chunks removed, content re-embedded). Confirm it FAILS on the current code (filename-only detection).
- [ ] 1.2 Add a test case asserting the no-change fast path is preserved: identical content + filename + hash yields an empty stale set and no removal/re-embed. Confirm it passes today and must keep passing.
- [ ] 1.3 Add a test case asserting graceful fallback: a candidate hash whose embedded chunks carry NO content signal does not raise and falls back to filename detection.

## 2. Persist the content signal at ingest (GREEN, part 1)

- [ ] 2.1 In `src/data_manager/vectorstore/manager.py` embed path (`_add_to_postgres` / `process_file` / hierarchical payload), compute a content signal from the persisted file bytes (content hash) once per file and attach it to each chunk's metadata (e.g. `metadata['content_hash']`), mirroring how `filename` is attached. Keep it cheap: compute only for files being embedded.
- [ ] 2.2 Confirm no new third-party dependency is introduced (use stdlib `hashlib`). Confirm `git diff origin/dev -- pyproject.toml requirements/requirements-base.txt` is empty.

## 3. Detect content staleness (GREEN, part 2)

- [ ] 3.1 Add `_collect_embedded_content_signals()` symmetric to `_collect_embedded_filenames()` — map `resource_hash -> set of embedded content signals` from `document_chunks.metadata`.
- [ ] 3.2 Extend `_collect_stale_hashes()` to ALSO mark a candidate hash stale when its current on-disk file's content signal differs from the embedded one. Only evaluate for hashes already present in the vectorstore; skip (fall back to filename detection) when no embedded signal exists; never crash.
- [ ] 3.3 Verify content-stale hashes flow through the existing remove-then-re-embed path in `update_vectorstore()` (no change needed there if `_collect_stale_hashes` returns them) and the no-change fast path (`hashes_in_data == hashes_in_vstore and not stale_hashes`) is untouched.

## 4. Make tests pass + verify

- [ ] 4.1 Run `python -m pytest tests/unit/ -k "reingest or stale or content_signal" -v` — new tests pass, existing filename tests still pass.
- [ ] 4.2 Run `bash scripts/gate.sh` — full unit suite green, diff-cover ≥ 80% on changed lines.

## 5. Docs

- [ ] 5.1 Update the LIMITATION note in `_collect_stale_hashes`'s docstring to state content-change detection is now closed.
- [ ] 5.2 Update `docs/docs/configuration.md` where re-ingest staleness is described to reflect the content-based signal.

## Why

The data-manager re-ingest path only detects stale vectorstore chunks when a
document's persisted *filename* changes (the HTML→Markdown extension flip added in
PR #38). A document whose **content changes while its filename and identity hash stay
the same** — a re-scraped web page whose URL is unchanged, or an in-place local/ticket
overwrite — keeps serving its old chunks indefinitely, because resource hashes are
identity-based (`md5(url)` / `md5(path)`) and the basename never moves. This is a
pre-existing correctness gap (issue #39) that lets retrieval return outdated content.

## What Changes

- Persist a per-document **content signal** at ingest time so a same-filename,
  same-hash content rewrite is detectable on the next re-ingest. Reuse an existing
  `documents` column or chunk metadata where possible (the `documents` table already
  carries `size_bytes` and `file_modified_at`); add a content hash only if no existing
  signal is sufficient.
- Extend `VectorStoreManager._collect_stale_hashes()` (in
  `src/data_manager/vectorstore/manager.py`) to ALSO mark a hash stale when its stored
  content signal differs from the current on-disk file's — not only when the basename
  differs. Content-stale hashes flow through the existing remove-then-re-embed path
  introduced in #38, so old chunks are replaced rather than duplicated or left stale.
- Keep the no-change fast path untouched: only candidate hashes already present in the
  vectorstore are compared, and an unchanged corpus still short-circuits to "up to
  date" with no extra per-document work on the common path.
- Update the limitation note in the `_collect_stale_hashes` docstring and
  `docs/docs/configuration.md` to reflect that content-change detection is now closed.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `ingest-processing`: the "Re-ingest refreshes chunks for changed content"
  requirement is broadened from filename-based detection to also cover a content-only
  change under an unchanged filename and unchanged hash.

## Impact

- Code: `src/data_manager/vectorstore/manager.py` (`update_vectorstore`,
  `_collect_stale_hashes`, content-signal collection/storage); the ingest write path
  that persists the content signal (collector/persistence layer). Possibly
  `src/cli/templates/init.sql` only if a new column is unavoidable (prefer reusing
  `size_bytes`/`file_modified_at`).
- Tests: new unit test mirroring `tests/unit/test_vectorstore_reingest_chunk_refresh.py`.
- Docs: `docs/docs/configuration.md`, `_collect_stale_hashes` docstring.
- No new third-party dependency. No schema migration unless a content-hash column is
  strictly required.

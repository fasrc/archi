## Why

`PostgresCatalogService.upsert_resource` writes `last_modified = EXCLUDED.last_modified`
unconditionally on the conflict path, and `EXCLUDED.last_modified` is `NULL` whenever the
resource's metadata did not carry the key. So **any** re-ingest that cannot supply a
timestamp overwrites a good stored one with `NULL`, and no path preserves the existing
value.

This is the amplifier behind all three review findings on PR #230 (issue #233). Each of
those was a distinct way for the sitemap lastmod map to end up empty or truncated, and in
every case the damage was the same: the catalog crawl re-upserts the affected pages and
their stored `last_modified` goes to `NULL`. Those three proximate causes are fixed at the
source; the amplifier is in a different module and remains. Shapes that still reach it:

- a source type that never carries `last_modified` re-ingesting a page a sitemap had stamped
- a sitemap that drops the `<lastmod>` element for a page it still lists
- any new caller of `upsert_resource` that does not thread the metadata through

The stored `last_modified` is the change signal that selective (incremental) re-ingest is
built on, so silently clearing it degrades the feature it exists to serve, with nothing
reporting the loss.

## What Changes

- The `ON CONFLICT DO UPDATE SET` clause in `upsert_resource` becomes
  `last_modified = COALESCE(EXCLUDED.last_modified, documents.last_modified)`, so an
  absent incoming timestamp means "no new information" and leaves the stored value intact,
  while a supplied one still overwrites unconditionally.
- The insert path is unchanged: a brand-new row with no timestamp still stores `NULL`,
  because there is no prior value to preserve.
- `upsert_resource`'s docstring states the preserve-on-absent semantics, so the next caller
  reads the contract instead of inferring it from SQL.
- This is issue #233's recommended option 1. Option 2 (a sentinel for explicit withdrawal)
  is deliberately NOT taken — see design D3.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `incremental-reingest`: the requirement "The documents catalog persists a last_modified
  value" is amended so that on the conflict path an absent incoming value preserves the
  stored one rather than clearing it. The insert-path and migration clauses are unchanged.

## Impact

- **Code**: `src/data_manager/collectors/utils/catalog_postgres.py` — the
  `ON CONFLICT DO UPDATE SET` clause of `upsert_resource` and its docstring. No column, no
  parameter, and no call signature changes.
- **Schema/DB**: none. No migration, no DDL change — `documents.last_modified` is already
  nullable. This change alters only how an existing row's value is updated.
- **Callers**: none change. Every caller of `upsert_resource` keeps its current signature
  and behaviour; the only difference is that omitting `last_modified` no longer destroys a
  stored value.
- **Behaviour**: strictly value-preserving. The only observable difference is that a row
  whose stored `last_modified` was previously cleared to `NULL` by a timestamp-less
  re-ingest now retains it. Existing rows already cleared are NOT backfilled — the data is
  gone and this change cannot reconstruct it; it stops further loss.
- **Trade-off accepted**: a page whose timestamp is *legitimately* withdrawn keeps the
  stale value (design D3).
- **Tests**: `tests/unit/test_catalog_postgres_upsert_last_modified.py` — one existing
  assertion flips, plus new cases for preservation and for overwrite-on-supplied. Mock
  cursor; no live DB or deployment needed.

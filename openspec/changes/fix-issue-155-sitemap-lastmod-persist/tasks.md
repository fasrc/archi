## 1. Capture lastmod in the parser (TDD)

- [x] 1.1 Write failing unit tests in `tests/unit/` for the lastmod-aware parse over
      fixture XML: a `<url>` with `<lastmod>2026-04-21T19:19:35+00:00</lastmod>` yields
      that value; one with no `<lastmod>` yields `None`; an empty `<lastmod></lastmod>`
      yields `None`; a malformed document raises no new exception (fail-open) and yields
      `None`/no entries.
- [x] 1.2 Add a lastmod-aware helper (e.g. `_loc_entries(root, wrapper) ->
      List[Tuple[str, Optional[str]]]`) that reads the first `<loc>` and first `<lastmod>`
      per wrapper, and expose captured entries to the expander (per design D1) WITHOUT
      changing `_locs(root, wrapper) -> List[str]`.
- [x] 1.3 Regression-guard existing parse callers: keep `sources_builder.py` (`_locs` ×3)
      and `goldenset_maintenance.py` (`parse_sitemap_document`) emitting the identical URL
      set; add/keep a test asserting one such caller's output is unchanged.

## 2. Carry lastmod through expansion (TDD)

- [x] 2.1 Write failing tests: `expand_sitemap_source`/`expand_sitemaps` emit each
      normalized page URL paired with its `lastmod` (or `None`); trust-filter, floor/cap,
      and order-preserving dedupe behavior is unchanged; on a normalization collision the
      first occurrence's URL+lastmod wins and the duplicate is dropped.
- [x] 2.2 Thread `lastmod` through `expand_sitemap_source` and `expand_sitemaps` (design
      D2), returning `(url, lastmod|None)` per emitted page.
- [x] 2.3 Update `goldenset_maintenance.py:582`'s `expand_sitemaps` consumer to the new
      shape, preserving its URL-set behavior; keep its tests green.

## 3. Persist last_modified in the documents catalog (TDD)

- [x] 3.1 Add `last_modified TIMESTAMPTZ` (nullable) to the `documents` DDL in
      `src/cli/templates/init.sql` (Timestamps block, near `file_modified_at`).
- [x] 3.2 Add a forward-only, idempotent migration
      `src/cli/templates/migrations/add_documents_last_modified.sql`
      (`ALTER TABLE documents ADD COLUMN IF NOT EXISTS last_modified TIMESTAMPTZ;`).
- [x] 3.3 Write failing test(s) for `catalog_postgres.upsert_resource`: a resource whose
      metadata carries `last_modified` writes the value to the column on insert and on
      conflict-update; absent → `NULL`; no error either way (fake/mock connection, no live
      DB).
- [x] 3.4 Add `"last_modified": "last_modified"` to
      `catalog_postgres._METADATA_COLUMN_MAP` and wire the column into the INSERT column
      list, the `VALUES` tuple, and the `ON CONFLICT DO UPDATE SET` clause
      (`= EXCLUDED.last_modified`). Leave the SQLite `index_utils` backend untouched.

## 4. Bridge sitemap lastmod → resource metadata (TDD)

- [x] 4.1 Write failing test(s): given expansion output carrying a `lastmod`, the scrape
      path injects `last_modified` into a sitemap-derived page's resource metadata so it
      reaches `upsert_resource`; a hand-listed URL and a lastmod-less page inject nothing
      (column stays `NULL`). Test the map-build + injection in isolation (fake persistence),
      not a live scrape.
- [x] 4.2 In `ScraperManager`, build a `{normalized_url: lastmod}` map from
      `_expand_sitemaps` (now returning pairs) and inject `last_modified` into the matching
      resource's metadata at persist time (design D4). No change to which pages are fetched
      or their order.

## 5. Verify no behavior regression + gate

- [x] 5.1 Add/confirm a test asserting fetch behavior is unchanged: a fresh ingest fetches
      the same set of pages as before; `last_modified` is recorded as an added attribute
      only (no skip path exists).
- [x] 5.2 Run `bash scripts/gate.sh` (format → lint → test, ≥80% diff coverage vs
      `origin/dev`) and ensure it exits 0. Fix any format/lint/coverage gaps.
- [x] 5.3 Confirm out-of-scope items are absent: no fetch-skip logic, no git `source_ref`
      skip, no `min_pages` change, no edit to the SQLite `resources` backend.

## Review round — value-less filter on a non-text column

- [x] R.1 **The `!= ''` predicate errors on the new column.** `get_metadata_by_filter("last_modified")`
  with no value took the "has a value" branch, which built
  `col IS NOT NULL AND col != ''`. `last_modified` is `TIMESTAMPTZ`, so PostgreSQL casts `''`
  to a timestamp, raises `InvalidDatetimeFormat`, and fails the whole query — the caller gets
  an error rather than a wrong row set.
- [x] R.2 **Fixed by column type, not by field name.** The reported symptom is on the field this
  change adds, but the same predicate was already reachable for `created_at`, `ingested_at`,
  `modified_at`/`file_modified_at` (all `TIMESTAMPTZ`) and `size_bytes` (`BIGINT`) — four
  pre-existing instances of one bug. `_NON_TEXT_COLUMNS` lists the non-text **columns** of
  `documents`, so a metadata key mapped to one later is covered without anyone remembering,
  and a test asserts that property over the map itself.
- [x] R.3 Mutation-checked in both directions: restoring the original predicate fails all six
  non-text cases, and dropping `!= ''` for *every* column fails the three text cases — so the
  fix can be neither reverted nor over-broadened silently. Over-broadening is the more
  dangerous direction, since it would start matching blank text values with nothing to report
  it.
- [x] R.4 Asserted on the emitted SQL rather than on returned rows: the query is malformed
  before PostgreSQL sees it, so the defect is fully visible in the string, and asserting on
  rows would require a live database to reproduce something a unit test can catch.

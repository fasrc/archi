## Why

The scraper already parses each page's `<lastmod>` out of a sitemap and then throws it
away, so archi has no per-page change signal to decide what to re-fetch on a re-ingest —
every ingest re-fetches all 200+ KB pages. Capturing and persisting that signal is the
enabler for selective (incremental) re-ingest (issue #155, PR 1 of 3 from #135). This PR
only **captures and persists** the signal; it changes no fetch behavior, so it can land
safely on its own and unblock PR-2 (sitemap lastmod-gated skip) and PR-3 (git release-ref
skip), which cannot start until a stored `last_modified` exists.

## What Changes

- The sitemap parser captures each `<url>`/`<sitemap>` entry's optional `<lastmod>`
  alongside its `<loc>`, instead of dropping it. A missing or malformed `<lastmod>`
  yields `None` and never raises (parsing stays fail-open).
- Sitemap expansion (`expand_sitemap_source` / `expand_sitemaps`) carries the optional
  `lastmod` per emitted, normalized page URL through the same dedupe it already does.
- The Postgres `documents` catalog gains a nullable `last_modified` column, added to the
  base schema (`init.sql`) for fresh installs and via a new idempotent migration under
  `src/cli/templates/migrations/` for existing databases. `upsert_resource` stores a
  `last_modified` value when the resource's metadata carries one.
- The ingest-time scrape path threads a sitemap-derived page's captured `lastmod` into
  the resource metadata so it reaches the new column — the only new stored attribute, with
  no change to which pages are fetched.
- **No** fetch-skip logic (PR-2), **no** git `source_ref` skip (PR-3), **no** `min_pages`
  interaction — a fresh ingest still fetches every page exactly as today.

## Capabilities

### New Capabilities
- `incremental-reingest`: capturing and persisting per-page change signals (starting with
  the sitemap `<lastmod>`) so a future re-ingest can conservatively skip unchanged pages.
  This PR delivers only the capture-and-persist half; the skip half is added by PR-2/PR-3.

### Modified Capabilities
<!-- None — no existing capability's REQUIREMENTS change. The scrape/persist pipeline in
     `ingest-processing` is reused as-is; this change adds a new, orthogonal capability. -->

## Impact

- **Code**: `src/data_manager/collectors/scrapers/sitemap_source.py` (parse + expand),
  `src/data_manager/collectors/scrapers/scraper_manager.py` (`_expand_sitemaps` +
  the sitemap→resource bridge), `src/data_manager/collectors/utils/catalog_postgres.py`
  (`_METADATA_COLUMN_MAP` + `documents` INSERT/UPDATE).
- **External callers** of the changed parse/expand functions that MUST keep working
  unchanged: `src/cli/tools/sources_builder.py` (`_locs` ×3),
  `src/utils/goldenset_maintenance.py` (`parse_sitemap_document`, `_locs`,
  `expand_sitemaps`).
- **Schema/DB**: new nullable `documents.last_modified TIMESTAMPTZ` in
  `src/cli/templates/init.sql` plus a forward-only, idempotent migration
  (`ADD COLUMN IF NOT EXISTS`). No backfill; existing rows read `NULL`.
- **Runtime**: none — no page is fetched, skipped, or ordered differently; the SQLite
  `resources` backend (`index_utils.py`) is untouched.
- **Tests**: `tests/unit/` fixture-based coverage for parse, expand, and the catalog
  upsert column (no live DB or deployment needed).

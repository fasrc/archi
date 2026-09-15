## Why

`ScraperManager.schedule_collect_links` refuses to crawl at all when sitemap expansion
raises `SitemapExpansionError` and no refresh has ever succeeded
(`src/data_manager/collectors/scrapers/scraper_manager.py:311-332`). One failed sitemap
fetch therefore suppresses the **whole** scheduled catalog crawl — every hand-listed and
catalog page, including pages that have nothing to do with that sitemap — and
`run_locked` in `src/bin/service_data_manager.py:70-91` records the early return as a
normal success, so the scheduler is never told the pass did nothing.

The guard was written for a hazard that no longer exists. It protects against a mapless
crawl writing NULL over every stored `last_modified`, which was real while
`upsert_resource` did an unconditional `last_modified = EXCLUDED.last_modified`. Issue
#233 (commit `c3757609`, PR #242) changed that clause to
`COALESCE(EXCLUDED.last_modified, documents.last_modified)`
(`src/data_manager/collectors/utils/catalog_postgres.py:334`), so an absent incoming
timestamp now means "no new information" and the database keeps the stored value. The
comment block that justifies the guard still asserts the old, unconditional clause; that
claim is false on `dev` today.

Two facts close the hazard completely. Every re-scrape of a known page reaches the
conflict path — `resource_hash` is derived from the URL
(`src/data_manager/collectors/persistence.py:51`) — so it hits the COALESCE branch. And
the crawl path deletes nothing: `reset_directory` and `delete_by_metadata_filter` have no
caller in the collector or scraper path. A mapless pass can therefore only fail to add new
stamps; it cannot destroy stored ones.

## What Changes

- The early `return` in the `SitemapExpansionError` handler of `schedule_collect_links` is
  deleted, so the scheduled pass always reaches
  `self.collect_links(persistence, link_urls=catalog_urls)` with the full catalog URL list.
- The `logger.error(...)`-then-skip pair becomes a single `logger.warning` that records a
  degraded pass: expansion failed, so pages new in this pass carry no `last_modified`.
- The comment block is rewritten to state the real invariant — the upsert preserves stored
  `last_modified` through `catalog_postgres.py`'s COALESCE, so a mapless crawl is safe, and
  the only cost is missing stamps for pages first seen during that pass.
- `TestNoDegradeWithoutAPriorMap` in `tests/unit/test_scraper_sitemap_refresh.py:812`
  pins the skip. It is replaced by strictly stronger tests: the crawl proceeds **and**
  stored timestamps survive a mapless pass through the production upsert path.
- This is issue #277's design option (c), chosen by the operator on 2026-08-23. The
  earlier two-PR plan (a scheduler-visible skip signal, then ownership partitioning) is
  superseded — it designs around a hazard the persistence layer no longer has.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `incremental-reingest`: two requirements are added. A scheduled collection now proceeds
  when expansion fails with no prior map, instead of skipping, and a mapless pass is
  required to be non-destructive to stored `last_modified` values. The capability's spec
  is not yet archived under `openspec/specs/`, so the delta is expressed as ADDED
  requirements that supersede the no-map clause of #181's fallback requirement.

## Impact

- **Code**: `src/data_manager/collectors/scrapers/scraper_manager.py` only — the
  `SitemapExpansionError` handler inside `schedule_collect_links` (the early return, its
  log call, and the comment block above it). No signature, no caller, and no other branch
  changes.
- **Explicitly unchanged**: the map-clearing and map-retention branches at lines 334-351,
  the `catalog_postgres.py` upsert, and `src/bin/service_data_manager.py` (the operator
  declined a scheduler-visible degraded-run signal; a warning log is the accepted
  observability).
- **Behaviour**: a scheduled pass whose sitemap expansion fails with no prior map now
  crawls the catalog instead of doing nothing. Stored timestamps are preserved by the
  database. Pages first seen during such a pass get no `last_modified` until a later pass
  with a working map supplies one.
- **Initial ingest**: unaffected — `SitemapExpansionError` still propagates out of the
  initial path and fails the ingest.
- **Schema/DB**: none. No migration, no DDL, no deploy ordering.
- **Tests**: `tests/unit/test_scraper_sitemap_refresh.py` (the pinned class is replaced)
  and `tests/unit/test_catalog_postgres_upsert_last_modified.py` (one case added, reusing
  the #233 mock-cursor harness). Mock cursor throughout; no live database and no
  deployment needed.

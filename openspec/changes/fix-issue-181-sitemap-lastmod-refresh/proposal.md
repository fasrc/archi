## Why

`ScraperManager._sitemap_lastmod_map` is built exactly once, during
`collect_all_from_config` (`scraper_manager.py:188`), and never again. Scheduled
collections (`schedule_collect_links`, `:298`) re-scrape the catalog's web URLs and
re-persist every page — stamping each one with the `<lastmod>` that was true when the
data-manager process started. A sitemap `<lastmod>` that advances while the service runs
therefore never reaches `documents.last_modified`: the scheduled run overwrites the row
with the startup-era timestamp, so the column is *always* stale, and staler with every
day of process uptime. That column is the change signal the selective-re-ingest work
(#155 PR-1 of 3) exists to feed, so a permanently-stale value makes the eventual
skip-unchanged-pages logic wrong rather than merely conservative (issue #181).

## What Changes

- The sitemap expansion + dedup + map-update sequence currently inlined in
  `collect_all_from_config:207–220` is extracted into one helper
  (`_refresh_sitemap_lastmod_map`) with a single behavior, used by both entry points.
- `schedule_collect_links` calls that helper before `collect_links`, so each scheduled
  collection re-reads the sitemaps and refreshes the map from current `<lastmod>` values.
- **Degrade on error (scheduled path only).** A `SitemapExpansionError` (below-floor /
  over-cap) during a scheduled refresh is caught, logged as a warning, and the *previous*
  map is retained; the scheduled collection proceeds. A stale-but-present map is strictly
  better than today's always-stale map, and a transient DNS/server blip must not stop a
  scrape of URLs already in the catalog.
- **Initial ingest keeps failing fast.** `collect_all_from_config` still lets
  `SitemapExpansionError` propagate and fail the ingest, per the existing comment at
  `:186–187`. No behavioral change to that path.
- The refreshed map preserves the hand-list exclusion rule: a page that is hand-listed in
  `input_lists` is absent from the map even when a sitemap also lists it, so its
  `last_modified` stays `NULL` (the rule `collect_all_from_config:208–214` documents and
  the `incremental-reingest` spec requires).
- **No** change to which URLs a scheduled collection scrapes — the crawl set remains the
  catalog query's result. **No** TTL/ETag refresh cadence, **no** change to
  `_expand_sitemaps` itself, **no** `deploy/` or `config/` change.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `incremental-reingest`: the per-page `<lastmod>` change signal this capability captures
  and persists must track the sitemap over process lifetime, not be pinned to startup.
  Adds the refresh-on-schedule requirement, its degraded-fallback contract, and the
  invariant that refreshing changes no fetch behavior.

## Impact

- **Code**: `src/data_manager/collectors/scrapers/scraper_manager.py` only —
  `collect_all_from_config` (extract), `schedule_collect_links` (call + degrade), and one
  new private helper. The file is `black`-clean at `origin/dev@0a157cdc`, so an in-place
  edit will not reflow unrelated lines into the diff.
- **Consumers**: `_handle_standard_url:693` reads the map via
  `getattr(self, "_sitemap_lastmod_map", {})` and only stamps `last_modified` when the map
  is non-empty — so a refresh that emptied the map would silently stop stamping every
  page. The helper must therefore publish a fully-built map atomically (see design D3).
- **Callers**: `src/bin/service_data_manager.py:89` is the scheduler entry point into
  `schedule_collect_links`; its signature and contract are unchanged.
- **Schema/DB**: none — the column and the metadata→column plumbing already exist (#155).
- **Runtime**: one extra sitemap fetch+parse per scheduled link collection, bounded by the
  same `sources.links.sitemap` policy (`allowed_hosts`, `min_pages`, `max_pages`) as
  ingest. `weblists/` input lists are read from local files (`:520–534`), so re-deriving
  the configured sitemap URLs adds no network I/O.
- **Tests**: `tests/unit/test_scraper_sitemap_refresh.py` (new) — mock-driven, no live DB,
  no deployment, no real sitemap fetch.

## Why

Web sources are a fixed hand-maintained URL list. `config/lists/sources.list` (370 lines) carries 219 hand-listed `docs.rc.fas.harvard.edu/kb/` article URLs — every one of them a `/kb/` article. The live KB sitemap at `https://docs.rc.fas.harvard.edu/kb/epkb_post_type_1-sitemap.xml` is a flat `<urlset>` (not a `<sitemapindex>`) containing **282** `<loc>` entries, each with a `<lastmod>` timestamp. The hand list is therefore missing ~63 KB articles today, and one sitemap line would replace 219 hand-maintained lines.

The crawler cannot close this gap on its own: the FASRC deployment pins `base_source_depth: 1` (template default at `src/cli/templates/base-config.yaml:251`), so each listed URL is fetched but its links are never followed. New KB articles enter the corpus only when someone edits the list by hand. The repo already shows the workaround this forces: `config/lists/slurm-sitemap.xml` is a hand-snapshotted sitemap (148 `<loc>` entries, generated with xml-sitemaps.com) whose URLs were pasted into `sources.list` — a stale copy the moment it was taken.

`archi sources build` (`src/cli/tools/sources_builder.py`, spec `openspec/specs/sources-build/spec.md`) can already expand a sitemap — but it is an operator-run, build-time list generator: discovery still requires a human to re-run it and re-commit the list. A `sitemap-` source prefix moves discovery into the ingestion run itself, so new KB articles appear automatically on every re-ingest.

## What Changes

- **New `sitemap-` prefix in the ingestion source lists**, analogous to the existing `git-`/`sso-`/`elog-`/`indico-` prefixes. `_collect_urls_from_lists_by_type` (`src/data_manager/collectors/scrapers/scraper_manager.py:411-440`) peels `sitemap-` lines into a new `sitemap_urls` bucket. The routing function stays pure list-parsing — no network I/O is added to it.
- **Sitemap expansion at ingest time**, in the I/O orchestrator `collect_all_from_config` (`scraper_manager.py:104-122`): a new `ScraperManager._expand_sitemaps(sitemap_urls)` helper fetches each sitemap over `requests` (the fetch pattern already used at `scraper.py:259`), parses it with stdlib `xml.etree.ElementTree`, and appends the resulting page URLs to `link_urls` before the `collect_links(...)` call at `scraper_manager.py:116`. Expanded URLs then flow through the normal web-scraping path exactly as if each had been listed by hand.
- **Parse logic in a small dedicated module** (`src/data_manager/collectors/scrapers/sitemap_source.py`) with an injectable fetch callable, so every branch is directly unit-testable without network access.
- **Robustness**: a fetched `<sitemapindex>` is recursed one level (each child sitemap fetched once; a child that is itself an index contributes nothing); any fetch/parse failure logs a warning and yields nothing for that document (fail-open — ingestion continues), mirroring the missing-list handling at `scraper_manager.py:404`.
- **Trailing-slash normalization**: sitemap `<loc>` values end in a trailing slash (`.../scp/`) while the hand list does not (`.../scp`); expanded URLs are normalized to the hand-list form (mirroring `sources_builder.normalize_url` at `src/cli/tools/sources_builder.py:326-343`) so the change does not compound the known trailing-slash duplicate-chunk issue (#118).
- **Config migration**: the 219 hand-listed `/kb/` lines in `config/lists/sources.list` are REPLACED by the single `sitemap-https://docs.rc.fas.harvard.edu/kb/epkb_post_type_1-sitemap.xml` line. The two must not run side by side, or the KB double-ingests under slash variants.

## Capabilities

### New Capabilities

- `sitemap-source-discovery`: A `sitemap-` prefix in ingestion source lists that expands an XML sitemap into its listed page URLs at ingest time, feeding them through the standard web-scraping path with fail-open error handling and hand-list-consistent URL normalization.

### Modified Capabilities

None. The `sources-build` spec (build-time list generation via `archi sources build`) is unaffected; the two are complementary (see design.md D2). The `git-`/`sso-`/`elog-`/`indico-` routing branches are untouched.

## Impact

- **Code**: `src/data_manager/collectors/scrapers/sitemap_source.py` (new module: parse, normalize, expand with injected fetch), `src/data_manager/collectors/scrapers/scraper_manager.py` (prefix branch in `_collect_urls_from_lists_by_type`, new `_expand_sitemaps` helper, wiring in `collect_all_from_config`). `_extract_urls_from_file` (`scraper_manager.py:528-540`) already survives `sitemap-` lines (skips `#`/blank, splits on comma) — no change needed there.
- **Config**: `config/lists/sources.list` — remove the 219 `/kb/` lines, add the one sitemap line. No config-schema change; no new keys in `config.yaml` / the base-config template.
- **Tests**: new `tests/unit/test_sitemap_source.py` (there are currently no scraper_manager unit tests). Tests mock the fetch and feed fixture XML strings: a `<urlset>` sample, a `<sitemapindex>` sample, and malformed/failed-fetch cases. No existing tests change.
- **Behavior**: sitemap-derived URLs are plain web links — crawled at the configured depth, fetched, persisted, chunked, and embedded identically to hand-listed URLs. Deployments whose lists contain no `sitemap-` lines see zero behavior change.
- **Not in scope**: deletion/pruning of upstream-removed docs (the pipeline has no prune step today; the sitemap is the enabling authoritative "current pages" set for that future work), `<lastmod>`-driven incremental re-embedding (the pipeline is full-rebuild today), crawl-depth changes, any change to `archi sources build`, and any change to the git/sso/elog/indico routing.

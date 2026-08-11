## 1. Red tests for the refresh contract

- [x] 1.1 Create `tests/unit/test_scraper_sitemap_refresh.py` with a fixture that builds a
      `ScraperManager` whose `input_lists` derivation and `_expand_sitemaps` are patched
      (no network, no live catalog): `_expand_sitemaps` returns a controllable list of
      `(page_url, lastmod|None)` pairs, and `persistence.catalog.get_metadata_by_filter`
      returns a controllable catalog URL list. Assert the fixture alone reproduces today's
      behavior: after `collect_all_from_config`, `_sitemap_lastmod_map` holds the initial
      values.
- [x] 1.2 Write the failing test for the headline bug: after `collect_all_from_config`
      builds the map, make `_expand_sitemaps` return *updated* `lastmod` values, call
      `schedule_collect_links`, and assert `_sitemap_lastmod_map` holds the new values.
      Red today (the scheduled path never re-expands).
- [x] 1.3 Write the failing test that the scheduled collection is actually scraped under the
      refreshed map — assert the refresh happens **before** `collect_links` is invoked (e.g.
      record the map's contents from a `collect_links` spy), not merely that the attribute is
      updated by the time the call returns.

## 2. Extract the refresh helper without changing initial ingest

- [x] 2.1 Lift the `_dedup_key` closure (`scraper_manager.py:194–198`) to a shared
      module-level function (or `@staticmethod`) keeping the normalize-or-fall-back-to-raw
      behavior, so both call sites share one definition (design D4).
- [x] 2.2 Extract `_refresh_sitemap_lastmod_map(self, sitemap_urls, existing_keys) ->
      List[str]` from `collect_all_from_config:207–220`: expand, walk pairs in order, skip
      URLs already in `existing_keys` (adding as it goes), and return the not-already-seen
      page URLs in order. It always lets `SitemapExpansionError` propagate — no `strict`
      flag (design D1, D2).
- [x] 2.3 Build the map into a **local** dict and assign `self._sitemap_lastmod_map` once,
      after expansion fully succeeds. Do not clear-then-populate in place (design D3 — the
      degraded path's fallback depends on this, and `_handle_standard_url:693` stamps only
      while the map is non-empty).
- [x] 2.4 Rewrite `collect_all_from_config` to call the helper and
      `link_urls.extend(...)` its return value. Confirm the existing suites that pin this
      path stay green **unmodified**: `test_scraper_sitemap_dedup_unaffected.py`,
      `test_scraper_no_duplication.py`, `test_scraper_determinism.py`.
- [ ] 2.5 Add the test asserting initial ingest still fails fast: `_expand_sitemaps` raising
      `SitemapExpansionError` inside `collect_all_from_config` propagates to the caller and
      no collection proceeds. Keep the intent comment at `:184–187` accurate.

## 3. Wire the scheduled path with the degraded fallback

- [ ] 3.1 In `schedule_collect_links`, re-derive the sitemap sources and hand-list keys from
      `self._collect_urls_from_lists_by_type(self.input_lists)`, then call the helper before
      `collect_links` and **discard** its return value — the crawl set stays the catalog
      query's result (design D2, D4).
- [ ] 3.2 Write the failing test for the degraded path: `_expand_sitemaps` raises
      `SitemapExpansionError` during `schedule_collect_links`; assert the call does **not**
      raise, `collect_links` still runs over the catalog URLs, and the previous map is intact
      entry-for-entry.
- [ ] 3.3 Write the failing test that a failed refresh never blanks the map — assert the map
      is neither empty nor partially rebuilt after the raising refresh, so pages are still
      stamped. Make it fail against a deliberate clear-then-populate implementation, so the
      test pins design D3 rather than restating 3.2.
- [ ] 3.4 Wrap the helper call in `try/except SitemapExpansionError`, logging one `warning`
      with the exception text, and continue to `collect_links`. Assert the warning via
      `caplog` (design D6).

## 4. Hand-list exclusion and map-replacement semantics

- [ ] 4.1 Write the failing test for the exclusion rule on the *scheduled* path: a page that
      is both hand-listed in `input_lists` and present in the sitemap gets **no** entry in the
      refreshed map (so its `last_modified` stays `NULL`), while a sitemap-only page does get
      one. Use a normalization-variant pair (`/x/` hand-listed vs `/x` in the sitemap) so the
      test pins matching on the normalized URL, not the raw string.
- [ ] 4.2 Write the test for wholesale replacement (design D5): a page present in the first
      expansion and absent from the second has no entry after the refresh, so nothing stamps
      it and its stored value returns to `NULL`. Assert the map does not retain the old entry.
- [ ] 4.3 Write the test that the scheduled crawl set is unchanged: a page newly present in
      the sitemap but absent from the catalog gains a map entry and is **not** passed to
      `collect_links`.

## 5. Gate and scope

- [ ] 5.1 Run `bash scripts/gate.sh` from the branch worktree and ensure it exits 0
      (format → lint → test, ≥80% diff coverage vs `origin/dev`). Fix any format, lint, or
      coverage gap before committing; never bypass the gate.
- [ ] 5.2 Confirm the out-of-scope items are absent from the diff: no TTL/ETag cadence, no
      edit to `_expand_sitemaps` or `sitemap_source.py`, no change to the initial-ingest error
      contract, no growth of the scheduled crawl set, and no `deploy/`, `config/`, schema, or
      migration change. The diff should touch `scraper_manager.py` and the new test file only.

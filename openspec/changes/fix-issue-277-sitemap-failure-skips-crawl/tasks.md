## 1. Let a mapless scheduled pass crawl (TDD, red then green in this one task)

- [ ] 1.1 Flip the pinned assertion, then remove the guard — **in this one task**, because
      `bash scripts/gate.sh` runs before every commit and a task that ends with the suite red can never be
      committed. In `tests/unit/test_scraper_sitemap_refresh.py`, rewrite
      `class TestNoDegradeWithoutAPriorMap` (line 812) as
      `class TestMaplessPassStillCrawls`: replace
      `test_expansion_error_without_a_map_skips_the_crawl` (which asserts `crawled == []`)
      with `test_expansion_error_without_a_map_still_crawls`, asserting that
      `collect_links` is called exactly once and that its `link_urls` equals the full
      catalog list the fixture publishes (`https://x.example.edu/a` and
      `https://x.example.edu/b`) — not a subset. Rewrite the class docstring, which
      currently recites the NULL-overwrite hazard as current fact, to state the new
      invariant (design D5). Keep
      `test_expansion_error_with_a_good_map_still_degrades_gracefully` verbatim — it pins
      #181's valid-map path and is still correct. Run
      `python -m pytest tests/unit/test_scraper_sitemap_refresh.py -q` and confirm the new
      test fails **on its own assertion** (`collect_links` never called), not on an import
      or fixture error — that is the red step and it proves the test reaches the guard.
- [ ] 1.2 Green: in `src/data_manager/collectors/scrapers/scraper_manager.py`, inside the
      `except SitemapExpansionError` handler of `schedule_collect_links`, delete the
      `if not getattr(self, "_sitemap_map_valid", False):` branch together with its
      `logger.error(...)` call and its `return`. The handler must fall through to the
      existing `logger.warning(str(exc))` and then to
      `self.collect_links(persistence, link_urls=catalog_urls)`. Emit exactly one
      `logger.warning` for a degraded pass — expansion failed, so pages new in this pass
      carry no `last_modified` — and no error-level log (design D3). Re-run the file:
      green. Change nothing in the `elif` / `else` branches that clear or retain the map
      (they are out of scope, issue #277 "Out of scope").
- [ ] 1.3 Rewrite the comment block above the deleted branch. It currently claims the
      upsert does an unconditional `last_modified = EXCLUDED.last_modified`; that claim is
      false on `dev`. State the real invariant: the catalog upsert preserves a stored
      `last_modified` through
      `COALESCE(EXCLUDED.last_modified, documents.last_modified)` in
      `src/data_manager/collectors/utils/catalog_postgres.py`, so a crawl with no map is
      safe, and the only cost is missing stamps for pages first seen in this pass
      (design D4). Keep the second paragraph's `_sitemap_map_valid` rationale only if the
      flag is still read after 1.2; if nothing reads it in this handler any more, drop that
      paragraph rather than leaving a comment about a dead condition.
- [ ] 1.4 Confirm no other test pins the skip:
      `grep -rn "skips_the_crawl\|skipping this scheduled crawl" tests/ src/` must return
      nothing.

## 2. Pin that a mapless pass destroys no stored timestamp (both layers)

- [ ] 2.1 Scraper layer — in `tests/unit/test_scraper_sitemap_refresh.py`, add a test that
      a page scraped while `_sitemap_lastmod_map` is empty is persisted with **no**
      `last_modified` key in `resource.metadata`. Drive it through
      `_scrape_and_persist_url` with a stub scraper whose `crawl_iter` yields one resource
      and a stub persistence that captures what it receives, so the assertion covers the
      real `if lastmod_map:` gate at `scraper_manager.py:823` rather than a reimplementation
      of it. This is the first half of the chain in design D2.
- [ ] 2.2 Persistence layer — in
      `tests/unit/test_catalog_postgres_upsert_last_modified.py`, add a case named for this
      scenario (a mapless scheduled re-crawl of a page whose row already holds a
      timestamp): call `upsert_resource` with metadata that has no `last_modified`, then
      assert both that the emitted SQL contains
      `last_modified = COALESCE(EXCLUDED.last_modified, documents.last_modified)` and that
      `_param_for_column` finds `None` bound in the `last_modified` slot. Reuse that
      existing helper — its column/VALUES pairing is what keeps the assertion from being
      positionally blind. Do not modify `catalog_postgres.py` (issue #277 "Out of scope").
- [ ] 2.3 Confirm the existing map-branch tests in
      `tests/unit/test_scraper_sitemap_refresh.py` are still green and unedited —
      `TestExpansionCompletenessSignal` and the clear-on-complete / retain-on-incomplete
      cases cover lines this change must not affect.

## 3. Verify against issue #277's acceptance criteria

- [ ] 3.1 Run `bash scripts/gate.sh` **bare — no pipe, no redirect** (it refuses to run when its output is
      piped or redirected). Format, lint, unit tests, and >= 80% diff coverage on changed
      lines must all pass. Never `--no-verify`.
- [ ] 3.2 Run `openspec validate fix-issue-277-sitemap-failure-skips-crawl --strict` and
      confirm it passes.
- [ ] 3.3 Walk issue #277's six acceptance-criteria boxes and confirm each maps to a real
      test or a real diff hunk: crawl proceeds with the full list (1.1), stored timestamps
      survive through the production upsert path (2.2), the pinned class is replaced by
      something strictly stronger (1.1 + 2.x), the false comment is gone and names the
      COALESCE clause (1.3), a `logger.warning` still records the degraded pass (1.2), and
      the gate is green (3.1).

## 4. Ship it (no merge)

- [ ] 4.1 Open one PR against `dev`:
      `gh pr create --repo fasrc/archi --base dev`. The body MUST contain `closes #277` —
      a closing keyword in the *title* does not link the issue. State in the body that the
      change needs **no migration and no deploy ordering**, that the data-loss hazard the
      removed guard defended against was closed by #233 / PR #242 (commit `c3757609`), and
      name the accepted cost: pages first seen during a mapless pass carry no
      `last_modified` until a later pass with a working map supplies one. **Never merge** —
      a human merges in daylight.

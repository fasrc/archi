## 1. Let a mapless scheduled pass crawl (TDD, red then green in this one task)

- [x] 1.1 Flip the pinned assertion, then remove the guard — **in this one task**, because
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
- [x] 1.2 Green: in `src/data_manager/collectors/scrapers/scraper_manager.py`, inside the
      `except SitemapExpansionError` handler of `schedule_collect_links`, delete the
      `if not getattr(self, "_sitemap_map_valid", False):` branch together with its
      `logger.error(...)` call and its `return`. The handler must fall through to the
      existing `logger.warning(str(exc))` and then to
      `self.collect_links(persistence, link_urls=catalog_urls)`. Emit exactly one
      `logger.warning` for a degraded pass — expansion failed, so pages new in this pass
      carry no `last_modified` — and no error-level log (design D3). Re-run the file:
      green. Change nothing in the `elif` / `else` branches that clear or retain the map
      (they are out of scope, issue #277 "Out of scope").
- [x] 1.3 Rewrite the comment block above the deleted branch. It currently claims the
      upsert does an unconditional `last_modified = EXCLUDED.last_modified`; that claim is
      false on `dev`. State the real invariant: the catalog upsert preserves a stored
      `last_modified` through
      `COALESCE(EXCLUDED.last_modified, documents.last_modified)` in
      `src/data_manager/collectors/utils/catalog_postgres.py`, so a crawl with no map is
      safe, and the only cost is missing stamps for pages first seen in this pass
      (design D4). Keep the second paragraph's `_sitemap_map_valid` rationale only if the
      flag is still read after 1.2; if nothing reads it in this handler any more, drop that
      paragraph rather than leaving a comment about a dead condition.
- [x] 1.4 Confirm no other test pins the skip:
      `grep -rn "skips_the_crawl\|skipping this scheduled crawl" tests/ src/` must return
      nothing.

## 2. Pin that a mapless pass destroys no stored timestamp (both layers)

- [x] 2.1 Scraper layer — in `tests/unit/test_scraper_sitemap_refresh.py`, add a test that
      a page scraped while `_sitemap_lastmod_map` is empty is persisted with **no**
      `last_modified` key in `resource.metadata`. Drive it through
      `_scrape_and_persist_url` with a stub scraper whose `crawl_iter` yields one resource
      and a stub persistence that captures what it receives, so the assertion covers the
      real `if lastmod_map:` gate at `scraper_manager.py:823` rather than a reimplementation
      of it. This is the first half of the chain in design D2.
- [x] 2.2 Persistence layer — in
      `tests/unit/test_catalog_postgres_upsert_last_modified.py`, add a case named for this
      scenario (a mapless scheduled re-crawl of a page whose row already holds a
      timestamp): call `upsert_resource` with metadata that has no `last_modified`, then
      assert both that the emitted SQL contains
      `last_modified = COALESCE(EXCLUDED.last_modified, documents.last_modified)` and that
      `_param_for_column` finds `None` bound in the `last_modified` slot. Reuse that
      existing helper — its column/VALUES pairing is what keeps the assertion from being
      positionally blind. Do not modify `catalog_postgres.py` (issue #277 "Out of scope").
- [x] 2.3 Confirm the existing map-branch tests in
      `tests/unit/test_scraper_sitemap_refresh.py` are still green and unedited —
      `TestExpansionCompletenessSignal` and the clear-on-complete / retain-on-incomplete
      cases cover lines this change must not affect.

## 3. Verify against issue #277's acceptance criteria

- [x] 3.1 Run `bash scripts/gate.sh` **bare — no pipe, no redirect** (it refuses to run when its output is
      piped or redirected). Format, lint, unit tests, and >= 80% diff coverage on changed
      lines must all pass. Never `--no-verify`.
- [x] 3.2 Run `openspec validate fix-issue-277-sitemap-failure-skips-crawl --strict` and
      confirm it passes.
- [x] 3.3 Walk issue #277's six acceptance-criteria boxes and confirm each maps to a real
      test or a real diff hunk: crawl proceeds with the full list (1.1), stored timestamps
      survive through the production upsert path (2.2), the pinned class is replaced by
      something strictly stronger (1.1 + 2.x), the false comment is gone and names the
      COALESCE clause (1.3), a `logger.warning` still records the degraded pass (1.2), and
      the gate is green (3.1).

## 4. Ship it (no merge)

- [x] 4.1 Open one PR against `dev` — https://github.com/fasrc/archi/pull/341:
      `gh pr create --repo fasrc/archi --base dev`. The body MUST contain `closes #277` —
      a closing keyword in the *title* does not link the issue. State in the body that the
      change needs **no migration and no deploy ordering**, that the data-loss hazard the
      removed guard defended against was closed by #233 / PR #242 (commit `c3757609`), and
      name the accepted cost: pages first seen during a mapless pass carry no
      `last_modified` until a later pass with a working map supplies one. **Never merge** —
      a human merges in daylight.

## Deviations from this plan, and why

Recorded during implementation on 2026-08-24. The plan was written against
`origin/dev` @ `4fb0050c`; implementation ran against `f9a14523`.

1. **Task 1.4's grep was too narrow and missed a second pin.**
   `grep -rn "skips_the_crawl\|skipping this scheduled crawl"` returns nothing, yet
   `TestIncompleteExpansionIsNotAValidMap::test_truncated_initial_expansion_does_not_authorize_a_later_degrade`
   also asserted `crawled == []`. Its name says "degrade", not "skip", so neither
   pattern reached it. It was found by running the whole file, not by the grep. That
   test is now `test_truncated_initial_expansion_still_crawls_on_a_later_failure`, and
   it additionally asserts `_sitemap_map_valid` stays `False` — the latch still gates
   the retention branch at `scraper_manager.py:718`, so it must not be blessed by the
   crawl proceeding. Its class docstring, which recited the NULL hazard as current
   fact and named the deleted class, was rewritten.

2. **Task 1.3's conditional paragraph is kept, because the flag is not dead.**
   The plan allowed dropping the `_sitemap_map_valid` rationale "if nothing reads it
   in this handler any more". Nothing in the handler does — but
   `_refresh_sitemap_lastmod_map` still reads it at `scraper_manager.py:718` for the
   retention decision. The flag stays; only the handler's reference to it is gone.

3. **Task 2.1 names a method that does not exist.** The plan says
   `_scrape_and_persist_url`; the real method is `ScraperManager._handle_standard_url`,
   and the `if lastmod_map:` gate is at line 827, not 823. The new test drives the
   real method. It asserts **both** directions — no stamp with an empty map, and a
   stamp with a populated one — because a negative-only test stays green if the
   stamping gate is deleted outright. No existing test exercised this method; every
   other test in the suite monkeypatches it away.

4. **Task 2.2 as written would have produced a duplicate.** It asks for a test
   asserting the COALESCE clause plus a `None` binding, but
   `test_upsert_resource_without_last_modified_uses_coalesce_and_passes_none`
   (`tests/unit/test_catalog_postgres_upsert_last_modified.py:148`) already asserts
   exactly both halves. Instead the new test
   `test_upsert_conflict_never_uses_the_unconditional_last_modified_form` asserts what
   no existing test does: the destructive `last_modified = EXCLUDED.last_modified`
   form is absent from the conflict clause. That is the assertion that fails if the
   hazard is ever reintroduced — nothing in the scraper would catch it, because the
   scraper's own behavior would still be correct.

5. **One warning covers both degrade paths.** The plan describes the message for the
   mapless case. With the guard gone, the valid-map case falls through the same
   `logger.warning`, so a message claiming "without a map" would be false there. The
   emitted message states the cached-entry count instead, which is accurate whether
   that count is 0 or N.

## Adversarial review, round 1 (2026-08-24)

Two findings. One adopted, one split, one pushed back on.

- **Adopted — the warning did not name the cache provenance.** The entry count alone
  cannot separate "0 entries, nothing ever expanded" from "12 entries from a truncated
  expansion", and the two justify different amounts of trust in the timestamps the pass
  writes. The warning now labels the map "from the last complete expansion" or "never
  validated by a complete expansion", read from `_sitemap_map_valid`. That flag decides
  no control flow here; it still gates the retention branch at
  `_refresh_sitemap_lastmod_map`. Pinned by
  `test_degraded_warning_names_the_cache_provenance`.

- **Pushed back — a scheduler-visible partial-failure signal.** The review asked that a
  degraded pass stop `run_locked` from advancing `last_run`. Issue #277's "Out of scope"
  records the operator declining exactly that, and `src/bin/service_data_manager.py` is
  named there as unchanged. The behavior is real — `run_locked` records any normal return
  as success — and the issue body already documents it. It is a settled scope decision,
  not a defect in this change.

- **Split out — deleted rows can be resurrected by a scheduled crawl.** The review
  reported this as introduced here. It is not. `schedule_collect_links` snapshots
  non-deleted catalog URLs, `delete_resource` soft-deletes
  (`catalog_postgres.py:372-384`), and the upsert clears `is_deleted`/`deleted_at` on
  conflict, so an operator delete landing mid-crawl is undone. Before this change the
  method had exactly one `return` — the guard removed here — so every other scheduled
  pass, including every successful one, already ran that race. This change adds one rare
  path to a pre-existing defect rather than creating it. Filed as its own issue.

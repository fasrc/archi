# Design — remove the mapless-crawl skip guard (issue #277)

## Context

`schedule_collect_links` has three ways to reach the crawl and one way to abandon it. The
abandon path — `SitemapExpansionError` raised while no refresh has ever succeeded — was
added to protect stored `last_modified` values from an upsert that cleared them. That
upsert no longer clears them. The guard is now a pure availability loss: one unreachable
sitemap silently cancels a scheduled crawl of the entire catalog.

## Decisions

### D1. Delete the guard rather than narrow it

The alternative was to keep the skip but restrict it to catalog rows a sitemap had actually
stamped (ownership partitioning). That requires persisted provenance per row, which is a
schema change and a migration, to defend against data loss that the COALESCE upsert has
already made impossible. The guard is deleted. `catalog_postgres.py:334` is the single
point that decides whether a timestamp-less upsert destroys a value, and it decides "no".

### D2. The invariant lives in the database, so the test must reach the SQL

A test that mocks `upsert_resource` and asserts "it was called without `last_modified`"
proves nothing about preservation — the whole hazard was in the SQL that the mock hides. So
the preservation half of this change is pinned in two layers, each in the file that already
owns its harness:

- **Scraper layer** (`tests/unit/test_scraper_sitemap_refresh.py`): with an empty
  `_sitemap_lastmod_map`, the scrape path adds no `last_modified` key to a resource's
  metadata. `_scrape_and_persist_url` gates stamping on `if lastmod_map:`
  (`scraper_manager.py:823`), so a mapless pass carries no timestamp into persistence.
- **Persistence layer** (`tests/unit/test_catalog_postgres_upsert_last_modified.py`):
  metadata with no `last_modified` still emits
  `COALESCE(EXCLUDED.last_modified, documents.last_modified)` and still binds `None` into
  the `last_modified` slot, so the preservation is decided by Postgres and not by the
  caller omitting a parameter. This reuses the existing `_param_for_column` helper, whose
  column/VALUES pairing keeps the assertion from being positionally blind.

Together they state the real chain: mapless pass → no incoming timestamp → stored value
survives. Neither test is moved into the other's file, because each depends on a fixture
that only exists where it lives.

### D3. Keep exactly one warning, and keep it a warning

The removed branch logged at `error` and returned. A pass that completes with fewer new
stamps is degraded, not failed, so the surviving call logs at `warning`. This is the
operator's accepted observability for issue #277; the scheduler-visible signal (a distinct
state in `run_locked`) was declined, and the data-manager service entrypoint is untouched.
The already-present `logger.warning(str(exc))` on the valid-map path stays as it is, so the
handler ends with one warning on either path and no duplicate.

### D4. The false comment is part of the defect

The comment at `scraper_manager.py:314-320` asserts an unconditional
`last_modified = EXCLUDED.last_modified` upsert. That was true when it was written and is
false on `dev` now. Leaving it while deleting the code it justifies would invite the guard
to be reinstated by the next reader. Rewriting it names `catalog_postgres.py`'s COALESCE
clause and the residual cost (no stamps for pages new in a mapless pass), so the trade-off
is legible at the site of the decision.

### D5. Replace the pinned test class, do not keep it

`TestNoDegradeWithoutAPriorMap` asserts `crawled == []` — the exact behaviour this change
removes. It cannot be kept, and it cannot be deleted without a replacement, or the change
would drop the only coverage of the no-map path. Its second test
(`test_expansion_error_with_a_good_map_still_degrades_gracefully`) pins the valid-map
degrade path from #181 and is still correct; it is preserved verbatim. The class's
docstring, which recites the NULL-overwrite hazard as current fact, is rewritten with the
class.

### D6. Red and green land in one task

The pinned assertion and the guard removal are a single task, because `bash scripts/gate.sh` runs before
every commit and a task that ends with the suite red can never be committed. The red step
is still real and still observed: flip the assertion, run the file, confirm it fails **on
that assertion**, then delete the guard and re-run green.

## Risks

- **A mapless pass adds no stamps for new pages.** Accepted and logged. Selective re-ingest
  treats a missing `last_modified` as "unknown", which re-processes the page — the
  conservative direction. A later pass with a working map fills it in.
- **A sitemap that is permanently broken now crawls every scheduled pass instead of
  skipping.** That is the intended behaviour: the crawl is the product, and the sitemap is
  only a source of timestamps. The warning names the degradation on every pass.
- **Regression risk to the map branches.** Lines 334-351 (clear on a complete read, retain
  on an incomplete read) are out of scope and are covered by existing tests in the same
  file; those tests must stay green untouched.

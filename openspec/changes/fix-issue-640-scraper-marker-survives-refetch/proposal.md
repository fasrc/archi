## Why

ELOG and Indico rows are stored with `source_type="web"` and a `"scraper": "elog"|"indico"`
key in `extra_json` (`elog_scraper.py:126`, `indico_scraper.py:926,1051,1208`). The plain
link scraper writes no such key. `ScrapedResource.get_hash()` hashes only the URL, so a
link re-fetch of an ELOG or Indico URL upserts the same `resource_hash`, and the catalog
upsert sets `extra_json = EXCLUDED.extra_json` (`catalog_postgres.py:335`). The whole blob is
replaced and the marker is erased.

`schedule_collect_links` re-fetches every `source_type="web"` row
(`scraper_manager.py:365-383`), so on `dev` every ELOG and Indico row loses its marker over
time. After that, `schedule_collect_elog` and `schedule_collect_indico` (`:477`, `:500`) no
longer find the row, and the disable guard of #460 cannot tell who owns it. The ELOG
fallback `_unmarked_source` (`:869`) reads only the `/elog/` path, so it misses an `elog-`
entry with any other path, and it never covers Indico (fasrc/archi#640).

The operator decided the mechanism and the repair on 2026-10-20 (issue body, "Decision").

## What Changes

- The catalog upsert keeps the stored `scraper` key when the incoming payload has no
  marker (absent or JSON `null`). An incoming marker still replaces the stored one. Every
  other key of `extra_json` still comes from the incoming payload. One SQL `CASE` in the
  `ON CONFLICT ... DO UPDATE SET` branch of `upsert_resource`; no schema change (the column
  is already `JSONB`, `init.sql:235`).
- New module `src/data_manager/collectors/utils/scraper_marker_repair.py`: reads the
  explicit `elog-` and `indico-` entries of the given input lists, finds unmarked `web`
  rows that those entries cover, and plans a marker repair for each. An ELOG-shaped or
  Indico-shaped row that the entries cannot place (no entry, two sources, or also listed
  as a plain link) is reported, never guessed. A row whose URL looks like a plain link
  gets no output (design D9, Risks).
- New one-off script `scripts/maintenance/repair_scraper_markers.py`: a dry run is the
  default; `--apply` writes the planned markers in one transaction with a guarded `UPDATE`
  that never overwrites a marker.
- `docs/docs/configuration.md`: say that a link re-fetch keeps the marker, and how to
  repair rows that lost it before this change.
- `schedule_collect_links` does not change (operator decision).

## Capabilities

### New Capabilities
- `scraper-source-marker`: the stored `scraper` marker of an ELOG or Indico catalog row
  survives a plain link re-fetch, and a one-off repair restores markers erased before.

### Modified Capabilities
- None.

## Impact

- Code: `src/data_manager/collectors/utils/catalog_postgres.py` (one clause),
  `src/data_manager/collectors/utils/scraper_marker_repair.py` (new),
  `scripts/maintenance/repair_scraper_markers.py` (new), `docs/docs/configuration.md`.
- Tests: `tests/unit/test_catalog_postgres_upsert_scraper_marker.py`,
  `tests/unit/test_scraper_marker_links_schedule.py`,
  `tests/unit/test_scraper_marker_repair.py`,
  `tests/unit/test_repair_scraper_markers_script.py` (all new).
- Behavior change: once a row has a marker, a plain link fetch cannot clear it. A URL
  that an operator moves from an `indico-` entry to a plain link entry stays an Indico row
  until the row is deleted (design, Risks).
- Operator step after deploy, outside this PR: run the repair script on dev, dry run
  first.
- Out of scope: the legacy SQLite `CatalogService` (`index_utils.py:102`; the persistence
  service uses `PostgresCatalogService`, `persistence.py:24`), rows from the `elog:` config
  section URL (not an input-list entry), and `extra_text`.

## ADDED Requirements

### Requirement: The catalog upsert SHALL keep a stored scraper marker when the incoming metadata has none
When `PostgresCatalogService.upsert_resource` updates an existing row and the incoming metadata has no `scraper` key, or has it with the value `None`, the stored non-null `scraper` value SHALL remain in `extra_json`, and every other `extra_json` key SHALL come from the incoming metadata. An incoming non-null `scraper` value SHALL replace the stored one.

#### Scenario: A plain link re-fetch of an Indico row
- **WHEN** a row is stored with `scraper: indico` and the same `resource_hash` is upserted again with metadata that has no `scraper` key
- **THEN** the row's `extra_json` still has `scraper: indico`, and `get_metadata_by_filter("scraper", scraper="indico")` still returns the row

#### Scenario: A re-fetch with no extra keys at all
- **WHEN** a row is stored with `scraper: elog` and the same `resource_hash` is upserted again with metadata whose only keys are catalog columns
- **THEN** the row's `extra_json` is `{"scraper": "elog"}`

#### Scenario: An explicit marker replaces the stored one
- **WHEN** a row is stored with `scraper: elog` and the same `resource_hash` is upserted again with `scraper: indico`
- **THEN** the row's `extra_json` has `scraper: indico`

#### Scenario: A None marker counts as no marker
- **WHEN** a row is stored with `scraper: elog` and the same `resource_hash` is upserted again with `scraper: None`
- **THEN** the row's `extra_json` has `scraper: elog`

#### Scenario: The conflict clause is pinned
- **WHEN** the unit tests read the SQL that `upsert_resource` executes
- **THEN** the `ON CONFLICT` branch has the `CASE` clause of design D1, and it has no unconditional `extra_json = EXCLUDED.extra_json` assignment

### Requirement: The links schedule SHALL NOT re-fetch a marked row of an explicitly disabled source
When `elog.enabled` or `indico.enabled` is explicitly `false`, `schedule_collect_links` SHALL NOT pass to `collect_links` the URL of a row whose stored marker names that source, whatever the URL shape.

#### Scenario: ELOG row with no ELOG path
- **WHEN** `elog.enabled` is `false` and a `web` row with URL `https://x/r.git` has `scraper: elog`
- **THEN** `collect_links` does not receive `https://x/r.git`

#### Scenario: Indico row
- **WHEN** `indico.enabled` is `false` and a `web` row has `scraper: indico`
- **THEN** `collect_links` does not receive that row's URL

### Requirement: The marker repair SHALL derive a missing marker only from explicit elog- and indico- input-list entries
`plan_marker_repairs` SHALL plan a `scraper` marker for an unmarked `web` row only when the explicit `elog-` or `indico-` entries of the given input lists cover the row's URL (design D5), and only when exactly one source covers it and the URL is not also an unprefixed list entry. It SHALL report, and SHALL NOT repair, an unmarked row that is ELOG-shaped or Indico-shaped but that no explicit entry covers, a row that entries of two sources cover, and a row that is also listed as a plain link.

#### Scenario: Row under an elog- entry with any path
- **WHEN** the lists hold `elog-https://x/r.git` and an unmarked row has URL `https://x/r.git`
- **THEN** the plan holds a repair with source `elog` for that row

#### Scenario: Row under an indico- event entry
- **WHEN** the lists hold `indico-https://indico.h/event/7/` and an unmarked row has URL `https://indico.h/event/7/contributions/3/`
- **THEN** the plan holds a repair with source `indico` for that row

#### Scenario: Event scope boundary
- **WHEN** the lists hold `indico-https://indico.h/event/7/` and an unmarked row has URL `https://indico.h/event/71/`
- **THEN** the plan holds no repair for that row, and it reports the row as unresolved

#### Scenario: Entries of two sources cover the row
- **WHEN** an `elog-` entry and an `indico-` entry both cover an unmarked row
- **THEN** the plan reports the row as unresolved and holds no repair for it

#### Scenario: Shaped row with no entry
- **WHEN** no explicit entry covers an unmarked row whose URL has an `/elog/` path
- **THEN** the plan reports the row as unresolved and holds no repair for it

#### Scenario: Plain link row
- **WHEN** no explicit entry covers an unmarked row whose URL is neither ELOG-shaped nor Indico-shaped
- **THEN** the plan neither repairs nor reports the row

### Requirement: The marker repair script SHALL write nothing unless --apply is given
`scripts/maintenance/repair_scraper_markers.py` SHALL default to a dry run that executes no `UPDATE`, SHALL write only with `--apply`, and SHALL write each marker with an `UPDATE` whose `WHERE` clause skips a row that already has a non-null marker. It SHALL refuse with exit code 2, before it connects to the database, when an input list cannot be read.

#### Scenario: Default dry run
- **WHEN** the script runs with `--pg-dsn` and `--input-list` only
- **THEN** it prints the planned repairs and the unresolved rows, executes no `UPDATE`, does not commit, and exits 0

#### Scenario: Apply
- **WHEN** the script runs with `--apply` and the plan holds one repair
- **THEN** it executes the guarded `UPDATE` once, commits once, and prints `wrote 1 of 1 marker(s)`

#### Scenario: Missing input list
- **WHEN** an `--input-list` path does not exist
- **THEN** the script exits 2, prints the error to stderr, and never calls `psycopg2.connect`

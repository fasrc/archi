## Context

Anchors re-verified on `origin/dev` `bbc52059` (2026-10-26).

- Catalog upsert: `src/data_manager/collectors/utils/catalog_postgres.py`. `upsert_resource`
  `:265` (docstring `:271-286`); `extra_json = json.dumps(extra, sort_keys=True) if extra
  else None` `:294`; `ON CONFLICT (resource_hash) DO UPDATE SET` `:321`;
  `extra_json = EXCLUDED.extra_json,` `:335`. The read side `_row_to_metadata` `:1431`
  drops keys whose value is JSON `null` (`:1445-1446`). `get_metadata_by_filter` `:397`
  filters on `extra_json` keys in Python after a `SELECT *`.
- Column type: `extra_json JSONB` (`src/cli/templates/init.sql:235`).
- Markers: `elog_scraper.py:126` (`"scraper": "elog"`, plus `elog_entry`),
  `indico_scraper.py:926,1051,1208` (`"scraper": "indico"`). The link scraper writes no
  `scraper` key. Indico row URLs are built from `indico.base_url` and the event id:
  `{base}/event/{id}/` (`:985`), `{base}/event/{id}/contributions/{c}/` (`:1120`).
- Scheduler: `src/data_manager/collectors/scrapers/scraper_manager.py`.
  `schedule_collect_links` `:358` (reads `url` and `scraper` of every `web` row `:365-367`,
  drops rows of an explicitly disabled source `:369-383`); `schedule_collect_indico` `:468`
  (`scraper="indico"` `:477`); `schedule_collect_elog` `:491` (`scraper="elog"` `:500`);
  `_unmarked_source` `:869` (ELOG by `/elog/` path only).
- List helpers: `src/cli/source_registry.py` — `split_prefixed_entry` `:14`, `is_elog_url`
  `:22`, `read_input_list_entries` `:28` (skips blank and `#` lines, drops `,depth`).
- Test patterns: `tests/unit/test_catalog_postgres_upsert_last_modified.py` (`_make_service`
  `:53-69`; the SQL-text assertions on the conflict clause in
  `test_upsert_resource_conflict_update_includes_last_modified` and
  `test_upsert_conflict_never_uses_the_unconditional_last_modified_form`);
  `tests/unit/test_scraper_input_list_precedence.py` (`_make_manager` `:22-27`,
  `_fake_persistence` `:30-36`, `_links_schedule_urls` `:255-280`);
  `tests/unit/test_goldenset_maintenance_script.py:15,36-37` (load a script by path with
  `importlib.util.spec_from_file_location`); `scripts/benchmarking/goldenset_maintenance.py:83`
  (a script puts the repo root on `sys.path`).

## Goals / Non-Goals

**Goals:** a link re-fetch never erases a stored marker; markers erased before this change
can be restored from an explicit list entry, with a dry run by default; nothing is guessed
from URL shape.

**Non-Goals:** `schedule_collect_links` (operator decision: do not change it); the legacy
SQLite `CatalogService` (`index_utils.py:102`, not used by `PersistenceService`,
`persistence.py:24`); `extra_text` (a search aid, no owner selection reads it); other
ELOG or Indico keys (`elog_entry`, `event_id`, …) — their own scraper rewrites them on its
next run; rows from the `elog:` config-section URL (not an input-list entry; an ELOG-shaped
one is still caught at run time by `_unmarked_source`, and the repair reports it); running
the repair on dev (a human step after deploy).

## Decisions

### D1. Keep the stored marker in the upsert's conflict branch

Replace `catalog_postgres.py:335` with exactly this clause (same indentation as the other
`SET` lines; the SQL string is a plain triple-quoted string, not an f-string):

```sql
                        extra_json = CASE
                            WHEN COALESCE(jsonb_typeof(EXCLUDED.extra_json -> 'scraper'), 'null') = 'null'
                                AND COALESCE(jsonb_typeof(documents.extra_json -> 'scraper'), 'null') <> 'null'
                            THEN COALESCE(EXCLUDED.extra_json, '{}'::jsonb)
                                || jsonb_build_object('scraper', documents.extra_json -> 'scraper')
                            ELSE EXCLUDED.extra_json
                        END,
```

- "No marker" means the key is absent **or** JSON `null`. `json.dumps` writes a `None`
  value as `null`, and the read side already treats `null` as absent (`:1445-1446`), so the
  write side must agree. `jsonb_typeof(NULL)` is `NULL`, hence the `COALESCE(..., 'null')`.
- `EXCLUDED.extra_json` is `NULL` when the payload has no extra keys (`:294`), so the merge
  starts from `'{}'::jsonb`.
- An incoming marker always wins (the `ELSE` branch), so a scraper can still re-own a row.
- Only the `scraper` key is carried over. Every other key still comes from the incoming
  payload, as before.
- Update the `upsert_resource` docstring: one paragraph that says the stored `scraper`
  marker is kept when the incoming metadata has none (absent or `None`), and why (#640).

Proven on the real `upsert_resource` path against a throwaway Postgres 17 (Appendix A).

### D2. Test the clause as text, as the `last_modified` tests do

The unit suite cannot run SQL. The tests assert the conflict clause text, as
`test_catalog_postgres_upsert_last_modified.py` does for `COALESCE(EXCLUDED.last_modified,
documents.last_modified)`. Compare after whitespace normalization
(`" ".join(sql.split())`) so black and indentation cannot break the match. The text that
the test pins is the text that Appendix A proved. Do not change the clause without a new
proof.

### D3. Guard the scheduler half of the chain

`schedule_collect_links` already drops a row whose stored marker names a disabled source.
With D1, a plain re-fetch keeps that marker, so the row stays dropped. Pin this with
scheduler tests that use a marked ELOG row whose URL has **no** `/elog/` path
(`https://x/r.git`; the shape fallback cannot rescue it) and a marked Indico row. They are
green on `dev` (guards). With D1 they are the whole chain: D1 keeps the marker, D3 obeys it.

### D4. Repair logic lives in `src/`, the script is thin

`scripts/` has no coverage gate (`--cov=src`), so the logic goes in a new module
`src/data_manager/collectors/utils/scraper_marker_repair.py`, and
`scripts/maintenance/repair_scraper_markers.py` only parses arguments, reads the lists,
connects, prints, and closes. Module API (exact names):

```python
MARKER_SOURCES = ("elog", "indico")
SELECT_UNMARKED_WEB_ROWS_SQL: str   # D7
UPDATE_MARKER_SQL: str              # D7

@dataclass(frozen=True)
class ListEntries:
    explicit: Tuple[Tuple[str, str], ...]  # (source, url) of each elog-/indico- entry, in list order
    plain: FrozenSet[str]                  # normalize_url() of each unprefixed entry

@dataclass(frozen=True)
class MarkerRepair:
    resource_hash: str
    url: str
    source: str
    entry: str          # the list entry URL that covers the row (without its prefix)

@dataclass(frozen=True)
class UnresolvedRow:
    resource_hash: str
    url: str
    reason: str

@dataclass(frozen=True)
class RepairPlan:
    repairs: Tuple[MarkerRepair, ...]
    unresolved: Tuple[UnresolvedRow, ...]

def normalize_url(url: str) -> str
def entry_scope(source: str, url: str) -> Optional[str]
def read_list_entries(paths: Sequence[Union[str, Path]]) -> ListEntries
def plan_marker_repairs(rows: Iterable[Tuple[str, Optional[str]]], entries: ListEntries) -> RepairPlan
def format_plan(plan: RepairPlan) -> List[str]
def read_unmarked_web_rows(conn) -> List[Tuple[str, Optional[str]]]
def apply_marker_repairs(conn, repairs: Sequence[MarkerRepair]) -> int
def run_repair(conn, entries: ListEntries, apply: bool) -> List[str]
```

### D5. Which rows an explicit entry covers

- `normalize_url(url)`: `urlsplit(url.strip())`; lowercase scheme and host; strip trailing
  `/` from the path; drop query and fragment. Example:
  `normalize_url("HTTPS://Indico.H/event/1/?x=1#a") == "https://indico.h/event/1"`.
- `read_list_entries(paths)`: for each path, `read_input_list_entries(path)` (a missing or
  unreadable file raises `OSError`; do not catch it here). Skip `sitemap-` entries. An entry
  that `split_prefixed_entry` maps to `elog` or `indico` goes to `explicit` as
  `(source, url)`; `git-` and `sso-` entries are ignored; an unprefixed entry goes to
  `plain` as `normalize_url(entry)`.
- `entry_scope(source, url)`:
  - `elog`: `normalize_url(url)` (the logbook URL; entries live under it; the path can be
    anything, `tests/unit/test_input_list_prefixes.py:33`).
  - `indico`: the normalized URL cut just after the first `/event/<digits>` in the path
    (`https://indico.h/event/7/timetable/` → `https://indico.h/event/7`). No event id (a
    category entry) → `None`: its rows sit under other events, so the entry covers no row.
- A scope `s` covers a row whose normalized URL is `n` when `n == s` or
  `n.startswith(s + "/")`. So `/event/7` does not cover `/event/71`.

### D6. Plan rule per row (never guess)

`plan_marker_repairs` walks the rows in order. Skip a row whose `url` is empty or `None`.
Let `n = normalize_url(url)` and `sources` = the set of sources whose explicit entries
cover `n` (keep the first covering entry per source, in list order).

1. `len(sources) > 1` → `UnresolvedRow(h, url, "covered by entries of more than one source: elog, indico")`.
2. `len(sources) == 1` and `n in entries.plain` → `UnresolvedRow(h, url, "also listed as a plain link")`.
3. `len(sources) == 1` → `MarkerRepair(h, url, source, entry_url)`.
4. `len(sources) == 0` and the URL is ELOG-shaped (`is_elog_url(url)`) or Indico-shaped
   (`"indico"` in the lowercased host and `"/event/"` in the lowercased path) →
   `UnresolvedRow(h, url, "no explicit elog- or indico- entry covers this row")`. Shape
   selects what to **report**, never what to write.
5. Otherwise the row is a plain link: no output.

`format_plan(plan)` returns, in this order: one line per repair
`f"repair {r.source} {r.resource_hash} {r.url} (entry {r.entry})"`, one line per unresolved
row `f"unresolved {u.resource_hash} {u.url}: {u.reason}"`, then
`f"{len(plan.repairs)} repair(s), {len(plan.unresolved)} unresolved"`.

### D7. Database access and the write guard

Proven against a throwaway Postgres 17 (Appendix A). Use exactly:

```python
SELECT_UNMARKED_WEB_ROWS_SQL = """
    SELECT resource_hash, url
    FROM documents
    WHERE NOT is_deleted
      AND source_type = 'web'
      AND COALESCE(jsonb_typeof(extra_json -> 'scraper'), 'null') = 'null'
    ORDER BY resource_hash
"""

UPDATE_MARKER_SQL = """
    UPDATE documents
    SET extra_json = COALESCE(extra_json, '{}'::jsonb)
        || jsonb_build_object('scraper', %s::text)
    WHERE resource_hash = %s
      AND NOT is_deleted
      AND COALESCE(jsonb_typeof(extra_json -> 'scraper'), 'null') = 'null'
"""
```

- `read_unmarked_web_rows(conn)`: one `cursor.execute(SELECT_UNMARKED_WEB_ROWS_SQL)`, return
  `[(row[0], row[1]) for row in cursor.fetchall()]`.
- `apply_marker_repairs(conn, repairs)`: one cursor; for each repair
  `execute(UPDATE_MARKER_SQL, (repair.source, repair.resource_hash))` and add
  `cursor.rowcount`; then `conn.commit()` once and return the sum. On any exception:
  `conn.rollback()`, then re-raise. The `WHERE` guard means a row that gained a marker
  since the read is not overwritten (its `rowcount` is 0, so the sum can be less than
  `len(repairs)`).
- `run_repair(conn, entries, apply)`: `plan = plan_marker_repairs(read_unmarked_web_rows(conn),
  entries)`; `lines = format_plan(plan)`; if `apply`: append
  `f"wrote {written} of {len(plan.repairs)} marker(s)"`, else append
  `"dry run: nothing written; rerun with --apply to write"`; return `lines`. The dry run
  executes no `UPDATE` and never calls `commit`.

### D8. Script interface

`python scripts/maintenance/repair_scraper_markers.py --pg-dsn <dsn> --input-list <path>
[--input-list <path> ...] [--dry-run | --apply]`

- `--pg-dsn` required. `--input-list` required, `action="append"`. `--dry-run` and
  `--apply` are a mutually exclusive group; the dry run is the default.
- Read the lists **before** connecting. On `OSError`, print
  `f"error: cannot read input list: {exc}"` to stderr and return 2; `psycopg2.connect` is
  never called.
- `import psycopg2` inside `main`, connect with `psycopg2.connect(args.pg_dsn)`, print each
  line of `run_repair(...)`, close the connection in a `finally`, return 0.
- Module docstring: purpose (#640), the usage line above, "dry run by default", and "run it
  on the host that can reach the stack's Postgres".
- The file starts with `sys.path.insert(0, str(Path(__file__).resolve().parents[2]))`, then
  the `src` import with `# noqa: E402  # isort: skip` (pattern:
  `scripts/benchmarking/goldenset_maintenance.py:83-89`).

### D9. Docs

In `docs/docs/configuration.md`, after the bullet list under "Two rows need care when a
marker is absent" (`:216-222`), add one paragraph: a plain link re-fetch keeps a stored
`scraper` marker (#640); rows that lost it before this change still map to `links`; repair
them with the script (dry run by default, `--apply` to write); it restores a marker only
from an explicit `elog-` or `indico-` entry in the given lists and reports the rows it
cannot place.

## Risks / Trade-offs

- **A marker is now sticky.** If an operator moves a URL from an `indico-` entry to a
  plain link entry, the row keeps `scraper: indico` until it is deleted. This follows from
  the operator's chosen mechanism; say it in the PR body.
- **The unit tests pin SQL text, not SQL behavior.** Appendix A is the behavior proof. A
  later edit of the clause needs a new proof.
- **Indico category entries cannot be repaired** (their events sit under other URLs). The
  script reports Indico-shaped rows that no entry covers.

## Appendix A — throwaway Postgres proof (Loop 1, 2026-10-26)

Setup: `docker run --rm -d -e POSTGRES_PASSWORD=x -p 127.0.0.1:55640:5432 postgres:17.11`;
one database per stage; the `documents` DDL from `init.sql:207-255`. The probe imported
`PostgresCatalogService` from each scratch tree (printed `module:` path) and called the
real `upsert_resource` twice per row (first payload, then second payload), then the real
`get_metadata_by_filter("scraper", ...)`. `red` = `origin/dev` `bbc52059`; `green` = the
same tree with the D1 clause.

| Case | First payload extra | Second payload extra | red `extra_json` | green `extra_json` |
|---|---|---|---|---|
| A | `scraper: indico, event_id: 1` | `title: plain` | `{"title": "plain"}` | `{"scraper": "indico", "title": "plain"}` |
| B (`https://x/r.git`) | `scraper: elog, elog_entry: true` | none (`extra_json` NULL) | `null` | `{"scraper": "elog"}` |
| C | `scraper: elog` | `scraper: indico` | `{"scraper": "indico"}` | `{"scraper": "indico"}` |
| D | `title: a` | `title: b` | `{"title": "b"}` | `{"title": "b"}` |
| E | `scraper: None, title: a` | `title: b` | `{"title": "b"}` | `{"title": "b"}` |
| F | `scraper: elog` | `scraper: None, title: b` | `{"scraper": null, "title": "b"}` | `{"scraper": "elog", "title": "b"}` |
| G | `title: g` (insert only) | — | `{"title": "g"}` | `{"title": "g"}` |

`get_metadata_by_filter("scraper", scraper="indico")`: red `['C']`, green `['A', 'C']`.
`get_metadata_by_filter("scraper", scraper="elog")`: red `[]`, green `['B', 'F']`.

D7 SQL on the red database: `SELECT_UNMARKED_WEB_ROWS_SQL` returned A, B, D, E, F, G (F has
a JSON-`null` marker). `UPDATE_MARKER_SQL` with `("indico", "A")` → rowcount 1,
`("elog", "B")` → rowcount 1, `("elog", "C")` → rowcount 0 (C already has a marker; it
stayed `{"scraper": "indico"}`). After: A `{"scraper": "indico", "title": "plain"}`,
B `{"scraper": "elog"}`; the unmarked set became D, E, F, G.

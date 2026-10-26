## 1. Keep the marker in the catalog upsert (fasrc/archi#640, design D1–D3)

- [x] 1.1 Red tests and the fix in one task. Create two NEW test files with the exact
      bodies in the Appendix of this file (copy them, do not rewrite them):
      `tests/unit/test_catalog_postgres_upsert_scraper_marker.py` (Appendix A, 3 tests)
      and `tests/unit/test_scraper_marker_links_schedule.py` (Appendix B, 3 tests). Run
      `python -m pytest -q tests/unit/test_catalog_postgres_upsert_scraper_marker.py
      tests/unit/test_scraper_marker_links_schedule.py` and confirm **2 failed, 4 passed**
      (the two conflict-clause tests fail; the insert-bind test and the 3 scheduler guards
      pass on `dev` — that is expected, design D3; Loop 1 measured these totals on
      `bbc52059`). Then, in the same task, replace the line
      `extra_json = EXCLUDED.extra_json,` in
      `src/data_manager/collectors/utils/catalog_postgres.py` (`:335`, inside the
      `ON CONFLICT ... DO UPDATE SET` of `upsert_resource`) with the exact 7-line `CASE`
      clause of design D1, at the same indentation as the other `SET` lines. Add the
      docstring paragraph of design D1 to `upsert_resource`. Do not touch
      `scraper_manager.py`. Re-run the two files: **6 passed**. Also run
      `python -m pytest -q tests/unit/test_catalog_postgres_upsert_last_modified.py
      tests/unit/test_catalog_postgres_schema_check.py
      tests/unit/test_scraper_input_list_precedence.py`: all pass. If collection fails
      with `ModuleNotFoundError: No module named 'slack_sdk'` (stale loop image, #776),
      run `pip install -q slack_sdk` once in the foreground and re-run. Run `black` and
      `isort` on the changed and new files BEFORE `git add`. Commit in the foreground
      (the pre-commit hook runs `bash scripts/gate.sh`, about 4 minutes; never put the
      commit or the gate in the background, and never pipe the gate). Message:
      `fix(#640): keep the scraper marker on a link re-fetch`.

## 2. Repair markers erased before the fix (design D4–D8)

- [x] 2.1 Pure planning, red tests and code in one task. Create the NEW file
      `tests/unit/test_scraper_marker_repair.py` with these 14 tests (grep each name
      first; names must be unique). Use `tmp_path` for list files. Expected values come
      from design D5 and D6.
      - `test_normalize_url_lowercases_scheme_and_host_and_drops_slash_query_fragment`:
        `normalize_url("HTTPS://Indico.H/event/1/?x=1#a") == "https://indico.h/event/1"`.
      - `test_entry_scope_elog_is_the_normalized_logbook_url`:
        `entry_scope("elog", "https://h/lb/") == "https://h/lb"`.
      - `test_entry_scope_indico_cuts_after_the_event_id`:
        `entry_scope("indico", "https://indico.h/event/7/timetable/") == "https://indico.h/event/7"`.
      - `test_entry_scope_indico_without_event_id_is_none`:
        `entry_scope("indico", "https://indico.h/category/3/") is None`.
      - `test_read_list_entries_splits_explicit_and_plain_entries`: one file with the lines
        `# comment`, a blank line, `elog-https://x/lb`, `indico-https://indico.h/event/7/`,
        `git-https://g/r.git`, `sso-https://s/`, `sitemap-https://x/s.xml`,
        `https://x/plain/,2` → `explicit == (("elog", "https://x/lb"), ("indico",
        "https://indico.h/event/7/"))` and `plain == frozenset({"https://x/plain"})`.
      - `test_read_list_entries_raises_on_a_missing_file`: `pytest.raises(OSError)` for
        `tmp_path / "absent.list"`.
      - `test_plan_repairs_rows_under_explicit_entries`: write the same list lines as in
        `test_read_list_entries_splits_explicit_and_plain_entries` and read them; rows `("h1", "https://x/lb/12")`,
        `("h2", "https://indico.h/event/7/contributions/3/")`, `("h3", "https://x/page")`
        → `repairs == (MarkerRepair("h1", "https://x/lb/12", "elog", "https://x/lb"),
        MarkerRepair("h2", "https://indico.h/event/7/contributions/3/", "indico",
        "https://indico.h/event/7/"))` and `unresolved == ()`.
      - `test_plan_repairs_an_elog_entry_with_any_path`: entry `elog-https://x/r.git`,
        row `("h1", "https://x/r.git")` → one repair with source `elog`.
      - `test_plan_reports_a_row_past_the_event_scope`: entry
        `indico-https://indico.h/event/7/`, row `("h1", "https://indico.h/event/71/")` →
        no repair; `unresolved == (UnresolvedRow("h1", "https://indico.h/event/71/",
        "no explicit elog- or indico- entry covers this row"),)`.
      - `test_plan_reports_a_row_covered_by_two_sources`: entries
        `elog-https://indico.h/event/7` and `indico-https://indico.h/event/7/`, row
        `("h1", "https://indico.h/event/7/")` → no repair; reason
        `"covered by entries of more than one source: elog, indico"`.
      - `test_plan_reports_a_row_also_listed_as_a_plain_link`: entries
        `indico-https://indico.h/event/7/` and `https://indico.h/event/7/timetable/`, row
        `("h1", "https://indico.h/event/7/timetable")` → no repair; reason
        `"also listed as a plain link"`.
      - `test_plan_reports_shaped_rows_with_no_entry_and_skips_plain_rows`: no entries;
        rows `("h1", "https://h/elog/lb/5")`, `("h2", "https://indico.h/event/9/")`,
        `("h3", "https://x/page")` → no repairs; `unresolved` holds exactly `h1` and `h2`
        (in that order) with reason `"no explicit elog- or indico- entry covers this row"`.
      - `test_plan_skips_rows_with_no_url`: rows `("h1", None)` and `("h2", "")` → empty
        plan.
      - `test_format_plan_lines`: a plan with one repair and one unresolved row →
        exactly these 3 strings, in order: `"repair elog h1 https://x/lb/12 (entry
        https://x/lb)"`, `"unresolved h2 https://h/elog/lb/5: no explicit elog- or
        indico- entry covers this row"`, `"1 repair(s), 1 unresolved"` (each string is one
        line; the wraps here are only for this file).
      Run the file: confirm it fails (the module does not exist). Then, in the same task,
      create `src/data_manager/collectors/utils/scraper_marker_repair.py` with a module
      docstring (purpose, #640, "never guesses from URL shape") and design D4's
      `MARKER_SOURCES`, the four dataclasses, `normalize_url`, `entry_scope`,
      `read_list_entries`, `plan_marker_repairs`, and `format_plan` (design D5, D6). Import
      `is_elog_url`, `read_input_list_entries`, and `split_prefixed_entry` from
      `src.cli.source_registry`. Leave the database functions for task 2.2. Re-run the
      file: **14 passed**. Run `black` and `isort` BEFORE `git add`. Commit in the
      foreground (the hook runs the gate; never background it). Message:
      `feat(#640): plan scraper marker repairs from explicit list entries`.

- [x] 2.2 Database half, red tests and code in one task. Create the NEW file
      `tests/unit/test_scraper_marker_repair_db.py` (a separate file, so that nothing is
      appended after the last test of 2.1) with these 8 tests. Use `MagicMock`
      connections: `conn.cursor.return_value.__enter__.return_value` is the cursor.
      - `test_read_unmarked_web_rows_runs_the_select_once`: `fetchall` returns
        `[("h1", "https://x/a")]` → result `[("h1", "https://x/a")]`, and `execute` was
        called once with `SELECT_UNMARKED_WEB_ROWS_SQL`.
      - `test_select_sql_matches_the_proven_text`: `" ".join(SELECT_UNMARKED_WEB_ROWS_SQL.split())`
        equals the whitespace-normalized SELECT of design D7.
      - `test_update_sql_matches_the_proven_text`: same check for `UPDATE_MARKER_SQL`
        against the UPDATE of design D7 (it holds the guard
        `COALESCE(jsonb_typeof(extra_json -> 'scraper'), 'null') = 'null'`).
      - `test_apply_marker_repairs_executes_one_update_per_repair_and_commits_once`: two
        repairs (`h1`/`elog`, `h2`/`indico`), `rowcount` 1 → returns `2`; `execute` calls
        are exactly `[call(UPDATE_MARKER_SQL, ("elog", "h1")), call(UPDATE_MARKER_SQL,
        ("indico", "h2"))]`; `conn.commit` called once.
      - `test_apply_marker_repairs_counts_only_rows_written`: an `execute` side effect sets
        `cursor.rowcount` to 1 then 0 → returns `1`.
      - `test_apply_marker_repairs_rolls_back_on_error`: `execute` raises
        `RuntimeError("boom")` → `pytest.raises(RuntimeError)`, `conn.rollback` called
        once, `conn.commit` not called.
      - `test_run_repair_dry_run_executes_no_update_and_never_commits`: `fetchall` returns
        one row under an `elog-https://x/lb` entry → the last line is
        `"dry run: nothing written; rerun with --apply to write"`, the first line starts
        with `"repair elog "`, `execute` was called only with the SELECT, and
        `conn.commit` was not called.
      - `test_run_repair_apply_writes_and_reports`: same row, `apply=True`, `rowcount` 1 →
        the last line is `"wrote 1 of 1 marker(s)"`, and `conn.commit` was called once.
      Run the file: confirm it fails (the names do not exist). Then, in the same task, add
      `SELECT_UNMARKED_WEB_ROWS_SQL`, `UPDATE_MARKER_SQL` (copy the exact text of design
      D7), `read_unmarked_web_rows`, `apply_marker_repairs`, and `run_repair` to
      `scraper_marker_repair.py`. Re-run both repair test files: **22 passed**. Run
      `black` and `isort` BEFORE `git add`. Commit in the foreground. Message:
      `feat(#640): read and write scraper markers for the repair`.

- [ ] 2.3 The one-off script, red tests and code in one task. Create the NEW file
      `tests/unit/test_repair_scraper_markers_script.py`. Load the script by path with
      `importlib.util.spec_from_file_location` (pattern:
      `tests/unit/test_goldenset_maintenance_script.py:15,36-37`). Patch
      `psycopg2.connect` with `monkeypatch.setattr(psycopg2, "connect", fake)`, where
      `fake` records its calls and returns a `MagicMock` connection whose cursor
      `fetchall` returns `[("h1", "https://x/lb/12")]`. The list file holds
      `elog-https://x/lb`. 4 tests:
      - `test_dry_run_is_the_default`: `main(["--pg-dsn", "dsn", "--input-list", path])`
        returns `0`; stdout's last line is
        `"dry run: nothing written; rerun with --apply to write"`; no `execute` call holds
        `UPDATE`; `conn.commit` not called; `conn.close` called once.
      - `test_apply_writes_and_reports`: with `--apply` → returns `0`, stdout has
        `"wrote 1 of 1 marker(s)"`, `conn.commit` called once, `conn.close` called once.
      - `test_missing_input_list_exits_2_before_connecting`: `--input-list` names a missing
        file → returns `2`, stderr starts with `"error: cannot read input list:"`, and
        `fake` was never called.
      - `test_dry_run_and_apply_are_mutually_exclusive`: `--dry-run --apply` →
        `pytest.raises(SystemExit)` with code `2`.
      Run the file: confirm it fails (the script does not exist). Then create
      `scripts/maintenance/repair_scraper_markers.py` per design D8 (no `__init__.py`
      needed). Re-run the file: **4 passed**. Run
      `python scripts/maintenance/repair_scraper_markers.py --help` and confirm it exits 0.
      Run `black` and `isort` BEFORE `git add`. Commit in the foreground. Message:
      `feat(#640): add the one-off scraper marker repair script`.

## 3. Docs (design D9)

- [ ] 3.1 In `docs/docs/configuration.md`, after the bullet list under "Two rows need care
      when a marker is absent" (`:216-222`) and before "To keep a link source out of an
      ingest today", add the paragraph of design D9. Name the command exactly:
      `python scripts/maintenance/repair_scraper_markers.py --pg-dsn <dsn> --input-list
      <path>`. Say that it runs a dry run by default and writes only with `--apply`, and
      that it reports rows that it cannot place. Do not claim anything that the code does
      not do (for example, the script does not read the deployment config; the operator
      names each list file). Run `python -m pytest -q
      tests/unit/test_input_list_precedence_docs.py tests/unit/test_restart_source_flags_docs.py
      tests/unit/test_port_config_docs_contract.py`: all pass. If `mkdocs` is installed, run
      `mkdocs build --strict -f docs/mkdocs.yml` and read its INFO and WARNING lines for this
      page. Commit in the foreground. Message: `docs(#640): marker survives a re-fetch;
      repair command`.

## 4. Publish

- [ ] 4.1 Tick this box, commit the tick through the hook, then push:
      `git push -u origin fix/issue-640-scraper-marker-survives-refetch`. Check for an
      existing PR first: `gh pr list --repo fasrc/archi --head
      fix/issue-640-scraper-marker-survives-refetch --state open --json number`. If one
      exists (the harness can open a placeholder PR after an earlier push), set its title
      and body with `gh pr edit <number> --repo fasrc/archi --title ... --body-file ...`.
      Otherwise open it with `gh pr create --repo fasrc/archi --base dev --head
      fix/issue-640-scraper-marker-survives-refetch --title ... --body-file ...`. Title:
      `fix(#640): keep the scraper marker on ELOG and Indico rows across a link re-fetch`.
      Write the body to a file under `/tmp`; never commit a PR-body file to the repository.
      The body has `Closes #640` (in the BODY, not the title) and states:
      - what changed (the D1 clause; the repair module and script; the docs paragraph);
      - the proof: copy the Appendix A table from `design.md`;
      - the behavior change: a marker is now sticky — a URL moved from an `indico-` entry to a
        plain link entry stays an Indico row until the row is deleted;
      - that `schedule_collect_links` did not change (operator decision), so its comment at
        `scraper_manager.py:376-378` still describes rows re-fetched before this change;
      - the operator step after deploy: on the dev host, run the script with `--pg-dsn` and
        every input-list file of the deployment (dry run), read the `unresolved` lines, then
        re-run with `--apply`;
      - tests: the new files and their counts, and the gate result.
      Then confirm the link: `gh pr view <number> --repo fasrc/archi --json
      closingIssuesReferences` lists `640`. STOP at the open PR — do not merge.

## Appendix (no checkboxes; copy these bodies exactly in task 1.1)

Loop 1 ran both files on 2026-10-26: on `origin/dev` `bbc52059`, **2 failed, 4 passed**; with
the design D1 clause, **6 passed**. With the clause, the existing files
`test_catalog*.py`, `test_scraper*.py`, and `test_input_list*.py` also passed (211 passed;
the only 2 failures were the docs tests, which need `docs/` and ran in a scratch copy
without it).

### Appendix A — `tests/unit/test_catalog_postgres_upsert_scraper_marker.py`

```python
"""Tests for the scraper marker in catalog_postgres.upsert_resource (#640).

Spec: openspec/changes/fix-issue-640-scraper-marker-survives-refetch/specs/scraper-source-marker/spec.md
Requirement: The catalog upsert SHALL keep a stored scraper marker when the incoming
metadata has none.

The unit suite cannot run SQL, so these tests pin the conflict clause text (design D2).
The behavior of that exact text was proven against a throwaway Postgres (design,
Appendix A). Do not change the clause without a new proof.
"""

import json
import re
from contextlib import contextmanager
from unittest.mock import MagicMock

from src.data_manager.collectors.utils.catalog_postgres import PostgresCatalogService

_MARKER_CLAUSE = """
    extra_json = CASE
        WHEN COALESCE(jsonb_typeof(EXCLUDED.extra_json -> 'scraper'), 'null') = 'null'
            AND COALESCE(jsonb_typeof(documents.extra_json -> 'scraper'), 'null') <> 'null'
        THEN COALESCE(EXCLUDED.extra_json, '{}'::jsonb)
            || jsonb_build_object('scraper', documents.extra_json -> 'scraper')
        ELSE EXCLUDED.extra_json
    END,
"""


def _normalized(text):
    return " ".join(text.split())


def _make_service(cursor):
    """Build a PostgresCatalogService with a fully mocked connection."""
    service = PostgresCatalogService.__new__(PostgresCatalogService)
    service._file_index = {}
    service._metadata_index = {}
    service._id_cache = {}

    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = False

    @contextmanager
    def _connect():
        yield conn

    service._connect = _connect
    return service


def _upsert_sql_and_params(metadata):
    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)
    service = _make_service(cursor)
    service.upsert_resource(
        resource_hash="hash1", path="/data/page.html", metadata=metadata
    )
    sql, params = cursor.execute.call_args[0]
    return sql, params


def test_conflict_clause_keeps_a_stored_scraper_marker():
    """A link re-fetch upserts the same hash with no marker; the stored one stays."""
    sql, _params = _upsert_sql_and_params({"source_type": "web", "url": "https://x/a"})
    assert "ON CONFLICT" in sql, "the upsert must still be a conflict-update"
    conflict = sql.split("ON CONFLICT", 1)[1]
    assert _normalized(_MARKER_CLAUSE) in _normalized(conflict), (
        "the conflict clause must keep a stored scraper marker (design D1); "
        f"got {conflict!r}"
    )


def test_conflict_clause_has_no_unconditional_extra_json_assignment():
    """The old form replaced the whole blob and erased the marker (#640)."""
    sql, _params = _upsert_sql_and_params({"source_type": "web", "url": "https://x/a"})
    conflict = sql.split("ON CONFLICT", 1)[1]
    assert not re.search(r"extra_json\s*=\s*EXCLUDED\.extra_json\s*,", conflict), (
        "the unconditional form erases the scraper marker on every link re-fetch; "
        f"got {conflict!r}"
    )


def test_insert_binds_the_incoming_extra_json_unchanged():
    """The marker logic is SQL-only: Python still binds the incoming extras as is."""
    _sql, params = _upsert_sql_and_params(
        {"source_type": "web", "url": "https://x/a", "title": "plain"}
    )
    assert json.dumps({"title": "plain"}, sort_keys=True) in params
```

### Appendix B — `tests/unit/test_scraper_marker_links_schedule.py`

```python
"""The links schedule obeys a stored scraper marker, whatever the URL shape (#640).

Spec: openspec/changes/fix-issue-640-scraper-marker-survives-refetch/specs/scraper-source-marker/spec.md
Requirement: The links schedule SHALL NOT re-fetch a marked row of an explicitly
disabled source.

These are guards (design D3). With the upsert clause of design D1, a plain link re-fetch
keeps the marker, so a disabled source's row stays out of the links schedule. The
sibling assertions are in tests/unit/test_catalog_postgres_upsert_scraper_marker.py; the
two files together are the whole chain.
"""

import types

from src.data_manager.collectors.scrapers import scraper_manager as sm_module
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager

_PLAIN = ("h1", {"url": "https://x/page", "source_type": "web"})
# An explicit elog- entry can carry any path (tests/unit/test_input_list_prefixes.py:33),
# so the /elog/ shape fallback cannot rescue this row: only its marker names the source.
_ELOG_NO_PATH = (
    "h2",
    {"url": "https://x/r.git", "source_type": "web", "scraper": "elog"},
)
_INDICO = (
    "h3",
    {"url": "https://indico.h/event/1/", "source_type": "web", "scraper": "indico"},
)


def _links_schedule_urls(monkeypatch, tmp_path, sources_config, web_rows):
    monkeypatch.setattr(
        sm_module, "get_global_config", lambda: {"DATA_PATH": str(tmp_path)}
    )
    manager = ScraperManager({"sources": sources_config})
    monkeypatch.setattr(
        manager, "_collect_urls_from_lists_by_type", lambda _l: ([], [], [], [], [], [])
    )
    seen = {}
    monkeypatch.setattr(
        manager,
        "collect_links",
        lambda persistence, link_urls=None: seen.setdefault("urls", link_urls),
    )

    def get_metadata_by_filter(field, value=None, metadata_keys=None, **kw):
        rows = [(h, m) for h, m in web_rows if m.get(field) == kw.get(field, value)]
        if metadata_keys:
            rows = [(h, {k: m[k] for k in metadata_keys if k in m}) for h, m in rows]
        return rows

    persistence = types.SimpleNamespace(
        data_path=tmp_path,
        catalog=types.SimpleNamespace(get_metadata_by_filter=get_metadata_by_filter),
    )
    manager.schedule_collect_links(persistence)
    return seen["urls"]


def test_links_schedule_skips_a_marked_elog_row_with_no_elog_path_when_disabled(
    monkeypatch, tmp_path
):
    urls = _links_schedule_urls(
        monkeypatch, tmp_path, {"elog": {"enabled": False}}, [_PLAIN, _ELOG_NO_PATH]
    )
    assert urls == ["https://x/page"]


def test_links_schedule_skips_a_marked_indico_row_when_disabled(monkeypatch, tmp_path):
    urls = _links_schedule_urls(
        monkeypatch, tmp_path, {"indico": {"enabled": False}}, [_PLAIN, _INDICO]
    )
    assert urls == ["https://x/page"]


def test_links_schedule_keeps_marked_rows_when_flags_absent(monkeypatch, tmp_path):
    urls = _links_schedule_urls(
        monkeypatch, tmp_path, {}, [_PLAIN, _ELOG_NO_PATH, _INDICO]
    )
    assert urls == ["https://x/page", "https://x/r.git", "https://indico.h/event/1/"]
```

"""Tests for scraper_marker_repair: database functions (#640).

Spec: openspec/changes/fix-issue-640-scraper-marker-survives-refetch/specs/scraper-source-marker/spec.md
Covers: SELECT_UNMARKED_WEB_ROWS_SQL, UPDATE_MARKER_SQL, read_unmarked_web_rows,
apply_marker_repairs, run_repair (design D7).
Plan-only functions are covered in test_scraper_marker_repair.py (task 2.1).
"""

from unittest.mock import MagicMock, call

import pytest

from src.data_manager.collectors.utils.scraper_marker_repair import (
    SELECT_UNMARKED_WEB_ROWS_SQL,
    UPDATE_MARKER_SQL,
    ListEntries,
    MarkerRepair,
    apply_marker_repairs,
    read_list_entries,
    read_unmarked_web_rows,
    run_repair,
)


def _make_conn(fetchall_return=None, rowcount=1):
    """Build a MagicMock connection with a cursor that returns preset values."""
    cursor = MagicMock()
    cursor.fetchall.return_value = fetchall_return or []
    cursor.rowcount = rowcount
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = False
    return conn, cursor


def test_read_unmarked_web_rows_runs_the_select_once():
    conn, cursor = _make_conn(fetchall_return=[("h1", "https://x/a")])
    result = read_unmarked_web_rows(conn)
    assert result == [("h1", "https://x/a")]
    cursor.execute.assert_called_once_with(SELECT_UNMARKED_WEB_ROWS_SQL)


def test_select_sql_matches_the_proven_text():
    expected = (
        "SELECT resource_hash, url "
        "FROM documents "
        "WHERE NOT is_deleted "
        "AND source_type = 'web' "
        "AND COALESCE(jsonb_typeof(extra_json -> 'scraper'), 'null') = 'null' "
        "ORDER BY resource_hash"
    )
    assert " ".join(SELECT_UNMARKED_WEB_ROWS_SQL.split()) == expected


def test_update_sql_matches_the_proven_text():
    expected = (
        "UPDATE documents "
        "SET extra_json = COALESCE(extra_json, '{}'::jsonb) "
        "|| jsonb_build_object('scraper', %s::text) "
        "WHERE resource_hash = %s "
        "AND NOT is_deleted "
        "AND COALESCE(jsonb_typeof(extra_json -> 'scraper'), 'null') = 'null'"
    )
    assert " ".join(UPDATE_MARKER_SQL.split()) == expected


def test_apply_marker_repairs_executes_one_update_per_repair_and_commits_once():
    conn, cursor = _make_conn(rowcount=1)
    repairs = [
        MarkerRepair("h1", "https://x/a", "elog", "https://x/lb"),
        MarkerRepair(
            "h2", "https://indico.h/event/1/", "indico", "https://indico.h/event/1/"
        ),
    ]
    result = apply_marker_repairs(conn, repairs)
    assert result == 2
    assert cursor.execute.call_args_list == [
        call(UPDATE_MARKER_SQL, ("elog", "h1")),
        call(UPDATE_MARKER_SQL, ("indico", "h2")),
    ]
    conn.commit.assert_called_once()


def test_apply_marker_repairs_counts_only_rows_written():
    conn, cursor = _make_conn()
    rowcounts = iter([1, 0])

    def set_rowcount(*_args):
        cursor.rowcount = next(rowcounts)

    cursor.execute.side_effect = set_rowcount
    repairs = [
        MarkerRepair("h1", "https://x/a", "elog", "https://x/lb"),
        MarkerRepair("h2", "https://x/b", "indico", "https://indico.h/event/1/"),
    ]
    result = apply_marker_repairs(conn, repairs)
    assert result == 1


def test_apply_marker_repairs_rolls_back_on_error():
    conn, cursor = _make_conn()
    cursor.execute.side_effect = RuntimeError("boom")
    repairs = [MarkerRepair("h1", "https://x/a", "elog", "https://x/lb")]
    with pytest.raises(RuntimeError):
        apply_marker_repairs(conn, repairs)
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()


def test_run_repair_dry_run_executes_no_update_and_never_commits(tmp_path):
    list_file = tmp_path / "test.list"
    list_file.write_text("elog-https://x/lb\n")
    entries = read_list_entries([list_file])
    conn, cursor = _make_conn(fetchall_return=[("h1", "https://x/lb/12")])
    lines = run_repair(conn, entries, apply=False)
    assert lines[-1] == "dry run: nothing written; rerun with --apply to write"
    assert lines[0].startswith("repair elog ")
    executed_sqls = [c.args[0] for c in cursor.execute.call_args_list]
    assert all(UPDATE_MARKER_SQL not in sql for sql in executed_sqls)
    conn.commit.assert_not_called()


def test_run_repair_apply_writes_and_reports(tmp_path):
    list_file = tmp_path / "test.list"
    list_file.write_text("elog-https://x/lb\n")
    entries = read_list_entries([list_file])
    conn, cursor = _make_conn(fetchall_return=[("h1", "https://x/lb/12")], rowcount=1)
    lines = run_repair(conn, entries, apply=True)
    assert lines[-1] == "wrote 1 of 1 marker(s)"
    conn.commit.assert_called_once()

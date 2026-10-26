"""Tests for scraper_marker_repair: plan-only functions (#640).

Spec: openspec/changes/fix-issue-640-scraper-marker-survives-refetch/specs/scraper-source-marker/spec.md
Covers: normalize_url, entry_scope, read_list_entries, plan_marker_repairs, format_plan (design D5, D6).
Database functions are covered in test_scraper_marker_repair_db.py (task 2.2).
"""

import pytest

from src.data_manager.collectors.utils.scraper_marker_repair import (
    ListEntries,
    MarkerRepair,
    RepairPlan,
    UnresolvedRow,
    entry_scope,
    format_plan,
    normalize_url,
    plan_marker_repairs,
    read_list_entries,
)

_LIST_LINES = "\n".join(
    [
        "# comment",
        "",
        "elog-https://x/lb",
        "indico-https://indico.h/event/7/",
        "git-https://g/r.git",
        "sso-https://s/",
        "sitemap-https://x/s.xml",
        "https://x/plain/,2",
    ]
)


def test_normalize_url_lowercases_scheme_and_host_and_drops_slash_query_fragment():
    assert (
        normalize_url("HTTPS://Indico.H/event/1/?x=1#a") == "https://indico.h/event/1"
    )


def test_entry_scope_elog_is_the_normalized_logbook_url():
    assert entry_scope("elog", "https://h/lb/") == "https://h/lb"


def test_entry_scope_indico_cuts_after_the_event_id():
    assert (
        entry_scope("indico", "https://indico.h/event/7/timetable/")
        == "https://indico.h/event/7"
    )


def test_entry_scope_indico_without_event_id_is_none():
    assert entry_scope("indico", "https://indico.h/category/3/") is None


def test_read_list_entries_splits_explicit_and_plain_entries(tmp_path):
    list_file = tmp_path / "sources.list"
    list_file.write_text(_LIST_LINES)
    result = read_list_entries([list_file])
    assert result.explicit == (
        ("elog", "https://x/lb"),
        ("indico", "https://indico.h/event/7/"),
    )
    assert result.plain == frozenset({"https://x/plain"})


def test_read_list_entries_raises_on_a_missing_file(tmp_path):
    with pytest.raises(OSError):
        read_list_entries([tmp_path / "absent.list"])


def test_plan_repairs_rows_under_explicit_entries(tmp_path):
    list_file = tmp_path / "sources.list"
    list_file.write_text(_LIST_LINES)
    entries = read_list_entries([list_file])
    rows = [
        ("h1", "https://x/lb/12"),
        ("h2", "https://indico.h/event/7/contributions/3/"),
        ("h3", "https://x/page"),
    ]
    plan = plan_marker_repairs(rows, entries)
    assert plan.repairs == (
        MarkerRepair("h1", "https://x/lb/12", "elog", "https://x/lb"),
        MarkerRepair(
            "h2",
            "https://indico.h/event/7/contributions/3/",
            "indico",
            "https://indico.h/event/7/",
        ),
    )
    assert plan.unresolved == ()


def test_plan_repairs_an_elog_entry_with_any_path(tmp_path):
    list_file = tmp_path / "elog.list"
    list_file.write_text("elog-https://x/r.git\n")
    entries = read_list_entries([list_file])
    plan = plan_marker_repairs([("h1", "https://x/r.git")], entries)
    assert len(plan.repairs) == 1
    assert plan.repairs[0].source == "elog"


def test_plan_reports_a_row_past_the_event_scope(tmp_path):
    list_file = tmp_path / "indico.list"
    list_file.write_text("indico-https://indico.h/event/7/\n")
    entries = read_list_entries([list_file])
    plan = plan_marker_repairs([("h1", "https://indico.h/event/71/")], entries)
    assert plan.repairs == ()
    assert plan.unresolved == (
        UnresolvedRow(
            "h1",
            "https://indico.h/event/71/",
            "no explicit elog- or indico- entry covers this row",
        ),
    )


def test_plan_reports_a_row_covered_by_two_sources(tmp_path):
    list_file = tmp_path / "multi.list"
    list_file.write_text(
        "elog-https://indico.h/event/7\nindico-https://indico.h/event/7/\n"
    )
    entries = read_list_entries([list_file])
    plan = plan_marker_repairs([("h1", "https://indico.h/event/7/")], entries)
    assert plan.repairs == ()
    assert len(plan.unresolved) == 1
    assert (
        plan.unresolved[0].reason
        == "covered by entries of more than one source: elog, indico"
    )


def test_plan_reports_a_row_also_listed_as_a_plain_link(tmp_path):
    list_file = tmp_path / "mixed.list"
    list_file.write_text(
        "indico-https://indico.h/event/7/\nhttps://indico.h/event/7/timetable/\n"
    )
    entries = read_list_entries([list_file])
    plan = plan_marker_repairs([("h1", "https://indico.h/event/7/timetable")], entries)
    assert plan.repairs == ()
    assert len(plan.unresolved) == 1
    assert plan.unresolved[0].reason == "also listed as a plain link"


def test_plan_reports_shaped_rows_with_no_entry_and_skips_plain_rows(tmp_path):
    list_file = tmp_path / "empty.list"
    list_file.write_text("")
    entries = read_list_entries([list_file])
    rows = [
        ("h1", "https://h/elog/lb/5"),
        ("h2", "https://indico.h/event/9/"),
        ("h3", "https://x/page"),
    ]
    plan = plan_marker_repairs(rows, entries)
    assert plan.repairs == ()
    assert len(plan.unresolved) == 2
    assert plan.unresolved[0].resource_hash == "h1"
    assert plan.unresolved[1].resource_hash == "h2"
    assert (
        plan.unresolved[0].reason
        == "no explicit elog- or indico- entry covers this row"
    )
    assert (
        plan.unresolved[1].reason
        == "no explicit elog- or indico- entry covers this row"
    )


def test_plan_skips_rows_with_no_url(tmp_path):
    list_file = tmp_path / "empty.list"
    list_file.write_text("")
    entries = read_list_entries([list_file])
    plan = plan_marker_repairs([("h1", None), ("h2", "")], entries)
    assert plan.repairs == ()
    assert plan.unresolved == ()


def test_format_plan_lines():
    plan = RepairPlan(
        repairs=(MarkerRepair("h1", "https://x/lb/12", "elog", "https://x/lb"),),
        unresolved=(
            UnresolvedRow(
                "h2",
                "https://h/elog/lb/5",
                "no explicit elog- or indico- entry covers this row",
            ),
        ),
    )
    lines = format_plan(plan)
    assert lines == [
        "repair elog h1 https://x/lb/12 (entry https://x/lb)",
        "unresolved h2 https://h/elog/lb/5: no explicit elog- or indico- entry covers this row",
        "1 repair(s), 1 unresolved",
    ]

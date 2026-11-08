"""Unit tests for the status board's provenance view model.

These cover the branching the board depends on — the match verdict, the
live-edited state, configuration drift, duration formatting and the
missing-record fallbacks — so the route handler and the Jinja template can stay
thin call sites (design D7).
"""

import logging
from datetime import datetime, timezone

import pytest

from src.interfaces.chat_app.status_provenance import (
    build_deployment_panel,
    build_knowledge_base_panel,
    format_duration,
    load_status_provenance,
    load_status_provenance_for,
)

STARTED = datetime(2026, 9, 22, 19, 49, tzinfo=timezone.utc)
FINISHED = datetime(2026, 9, 22, 20, 56, tzinfo=timezone.utc)
DEPLOYED = datetime(2026, 9, 22, 18, 42, tzinfo=timezone.utc)

PIN = "48022ed74a1f5eed183268d0f82fd7c6d646f9b5"


def _deploy_row(**overrides):
    row = {
        "config_ref": "deploy-pin-2026-09e",
        "config_sha": PIN,
        "config_head": PIN,
        "pin_matched": True,
        "dirty_paths": "",
        "app_version": "v2026.08.0-32-g8667940d",
        "deployed_at": DEPLOYED,
    }
    row.update(overrides)
    return row


def _run_row(**overrides):
    row = {
        "started_at": STARTED,
        "completed_at": FINISHED,
        "status": "updated",
        "documents_embedded": 1091,
        "documents_failed": 0,
        "documents_pending": 0,
        "chunk_count": 6926,
        "config_snapshot": {"categorization": False, "chunk_size": 1000},
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# format_duration
# ---------------------------------------------------------------------------


def test_duration_reads_in_hours_and_minutes():
    assert format_duration(STARTED, FINISHED) == "1 hr 7 min"


def test_a_short_duration_reads_in_minutes():
    end = datetime(2026, 9, 22, 19, 56, tzinfo=timezone.utc)
    assert format_duration(STARTED, end) == "7 min"


def test_a_sub_minute_duration_reads_in_seconds():
    end = datetime(2026, 9, 22, 19, 49, 30, tzinfo=timezone.utc)
    assert format_duration(STARTED, end) == "30 sec"


def test_a_run_with_no_completion_has_no_duration():
    assert format_duration(STARTED, None) is None


def test_a_negative_duration_is_refused_rather_than_shown():
    """Clock skew must not render as a negative run time."""
    assert format_duration(FINISHED, STARTED) is None


# ---------------------------------------------------------------------------
# build_deployment_panel
# ---------------------------------------------------------------------------


def test_a_clean_on_pin_deploy_reports_the_pin_and_no_warning():
    panel = build_deployment_panel(_deploy_row())

    assert panel["available"] is True
    assert panel["config_ref"] == "deploy-pin-2026-09e"
    assert panel["config_sha_short"] == "48022ed7"
    assert panel["live_edited"] is False
    assert panel["dirty_path_count"] == 0
    assert panel["app_version"] == "v2026.08.0-32-g8667940d"
    assert panel["deployed_at"] == DEPLOYED


def test_tracked_edits_on_the_pin_raise_the_live_edited_warning():
    """The deploy is permitted, so the pin alone would misrepresent it."""
    row = _deploy_row(dirty_paths="M\tlists/sources.list\nM\tenvironments/dev.yaml")

    panel = build_deployment_panel(row)

    assert panel["live_edited"] is True
    assert panel["dirty_path_count"] == 2
    assert "lists/sources.list" in panel["dirty_paths_preview"][0]


def test_an_off_pin_deploy_raises_the_warning_and_shows_the_real_head():
    row = _deploy_row(pin_matched=False, config_head="0123456789abcdef")

    panel = build_deployment_panel(row)

    assert panel["live_edited"] is True
    assert panel["config_head_short"] == "01234567"


def test_the_dirty_preview_is_capped_so_a_very_dirty_tree_stays_readable():
    row = _deploy_row(dirty_paths="\n".join(f"M\tfile{n}" for n in range(20)))

    panel = build_deployment_panel(row)

    assert panel["dirty_path_count"] == 20
    assert len(panel["dirty_paths_preview"]) <= 5


def test_an_absent_deployment_record_reports_unavailable_not_empty():
    panel = build_deployment_panel(None)

    assert panel["available"] is False
    assert panel.get("config_ref") is None


# ---------------------------------------------------------------------------
# build_knowledge_base_panel
# ---------------------------------------------------------------------------


def test_the_panel_reports_the_window_counts_and_snapshot():
    panel = build_knowledge_base_panel(_run_row(), current_snapshot={})

    assert panel["available"] is True
    assert panel["started_at"] == STARTED
    assert panel["duration"] == "1 hr 7 min"
    assert panel["documents_embedded"] == 1091
    assert panel["chunk_count"] == 6926
    assert panel["config"] == {"categorization": False, "chunk_size": 1000}


def test_flags_come_from_the_snapshot_not_from_current_config():
    """The board must not attribute current config to an older corpus."""
    run = _run_row(config_snapshot={"categorization": True})

    panel = build_knowledge_base_panel(run, current_snapshot={"categorization": False})

    assert panel["config"]["categorization"] is True


def test_drift_is_reported_per_flag_with_both_values():
    run = _run_row(config_snapshot={"categorization": True, "chunk_size": 1000})

    panel = build_knowledge_base_panel(
        run, current_snapshot={"categorization": False, "chunk_size": 1000}
    )

    assert panel["drift"] == [
        {"key": "categorization", "current": False, "at_ingest": True}
    ]


def test_no_drift_when_current_config_matches_the_snapshot():
    snapshot = {"categorization": False, "chunk_size": 1000}
    panel = build_knowledge_base_panel(
        _run_row(config_snapshot=snapshot), current_snapshot=dict(snapshot)
    )

    assert panel["drift"] == []


def test_an_absent_ingest_run_reports_unavailable():
    panel = build_knowledge_base_panel(None, current_snapshot={"categorization": False})

    assert panel["available"] is False
    assert panel["drift"] == []


def test_counts_that_were_never_recorded_stay_none():
    run = _run_row(documents_embedded=None, chunk_count=None)

    panel = build_knowledge_base_panel(run, current_snapshot={})

    assert panel["documents_embedded"] is None
    assert panel["chunk_count"] is None


# ---------------------------------------------------------------------------
# load_status_provenance
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, results, raises=False):
        self.results = list(results)
        self.raises = raises
        self._row = None

    def execute(self, sql, params=None):
        if self.raises:
            raise RuntimeError("query exploded")
        self._row = self.results.pop(0) if self.results else None

    def fetchone(self):
        return self._row

    def close(self):
        pass


class _FakeConn:
    def __init__(self, results=(), raises=False):
        self.cursor_obj = _FakeCursor(results, raises)

    def cursor(self, **kwargs):
        return self.cursor_obj

    def close(self):
        pass


def test_loading_returns_both_panels():
    conn = _FakeConn(
        results=[
            tuple(_deploy_row().values()),
            tuple(_run_row().values()),
            ({"data_manager": {}},),
        ]
    )

    view = load_status_provenance(conn)

    assert "deployment" in view
    assert "knowledge_base" in view


def test_a_failing_query_still_returns_a_renderable_view():
    """The page must render, and the alert sections must survive (design D9)."""
    conn = _FakeConn(raises=True)

    view = load_status_provenance(conn)

    assert view["deployment"]["available"] is False
    assert view["knowledge_base"]["available"] is False


@pytest.mark.parametrize("conn", [None, object()])
def test_loading_never_raises_on_a_bad_connection(conn):
    view = load_status_provenance(conn)
    assert view["deployment"]["available"] is False


# ---------------------------------------------------------------------------
# load_status_provenance_for (the route's call site)
# ---------------------------------------------------------------------------


def test_load_for_connects_loads_and_closes(monkeypatch):
    conn = _FakeConn(
        results=[
            tuple(_deploy_row().values()),
            tuple(_run_row().values()),
            ({"data_manager": {}},),
        ]
    )
    closed = []
    conn.close = lambda: closed.append(True)

    import psycopg2

    monkeypatch.setattr(psycopg2, "connect", lambda **kwargs: conn)

    view = load_status_provenance_for({"host": "db"})

    assert view["deployment"]["available"] is True
    assert closed == [True]


def test_load_for_returns_an_unavailable_view_when_the_connection_fails(monkeypatch):
    """The status page must still render, alerts included (design D9)."""

    def _boom(**kwargs):
        raise RuntimeError("no database")

    import psycopg2

    monkeypatch.setattr(psycopg2, "connect", _boom)

    view = load_status_provenance_for({"host": "db"})

    assert view["deployment"]["available"] is False
    assert view["knowledge_base"]["available"] is False


def test_load_for_tolerates_an_empty_config(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("no config")

    import psycopg2

    monkeypatch.setattr(psycopg2, "connect", _boom)

    assert load_status_provenance_for({})["deployment"]["available"] is False


# ---------------------------------------------------------------------------
# Review round 1: unknown provenance, and malformed persisted snapshots
# ---------------------------------------------------------------------------


def test_an_unrecorded_verdict_is_not_rendered_as_a_live_edit():
    """A hand-run `archi create` records no verdict; that is not an edit."""
    panel = build_deployment_panel(_deploy_row(pin_matched=None))

    assert panel["live_edited"] is False
    assert panel["pin_state"] == "unknown"


def test_a_clean_deploy_reports_the_matched_state():
    assert build_deployment_panel(_deploy_row())["pin_state"] == "matched"


def test_dirty_paths_still_prove_a_live_edit_without_a_verdict():
    row = _deploy_row(pin_matched=None, dirty_paths="M\tlists/sources.list")

    panel = build_deployment_panel(row)

    assert panel["pin_state"] == "live_edited"
    assert panel["live_edited"] is True


def test_an_absent_record_reports_the_unknown_state():
    assert build_deployment_panel(None)["pin_state"] == "unknown"


@pytest.mark.parametrize("bad", [[1, 2], "nope", 7, None])
def test_a_non_object_snapshot_cannot_reach_the_template(bad):
    """The JSONB column accepts anything; dictsort in Jinja does not.

    A truthy non-mapping would otherwise 500 the page the unavailable-panel
    requirement says must keep rendering.
    """
    panel = build_knowledge_base_panel(_run_row(config_snapshot=bad), {})

    assert panel["config"] == {}
    assert panel["drift"] == []


def test_snapshot_keys_are_coerced_to_strings():
    panel = build_knowledge_base_panel(_run_row(config_snapshot={1: "a"}), {})

    assert panel["config"] == {"1": "a"}


# ---------------------------------------------------------------------------
# D3 — corpus run vs failed attempt
# ---------------------------------------------------------------------------

FAILED_LATER = datetime(2026, 9, 23, 1, 0, tzinfo=timezone.utc)
FAILED_EARLIER = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)
FAILED_AT = datetime(2026, 9, 23, 3, 0, tzinfo=timezone.utc)


def test_sql_latest_ingest_run_filters_by_success_status():
    from src.interfaces.chat_app.status_provenance import _SQL_LATEST_INGEST_RUN

    assert "status IN ('updated', 'up_to_date')" in _SQL_LATEST_INGEST_RUN


def test_build_kb_panel_reports_failed_at_when_newer_than_corpus_run():
    panel = build_knowledge_base_panel(
        _run_row(), current_snapshot={}, last_failed_at=FAILED_LATER
    )
    assert panel["last_attempt_failed_at"] == FAILED_LATER


def test_build_kb_panel_clears_failed_at_when_older_than_corpus_run():
    panel = build_knowledge_base_panel(
        _run_row(), current_snapshot={}, last_failed_at=FAILED_EARLIER
    )
    assert panel["last_attempt_failed_at"] is None


def test_build_kb_panel_unavailable_carries_failed_at():
    panel = build_knowledge_base_panel(
        None, current_snapshot={}, last_failed_at=FAILED_LATER
    )
    assert panel["available"] is False
    assert panel["last_attempt_failed_at"] == FAILED_LATER


def test_build_kb_panel_naive_vs_aware_datetime_does_not_raise():
    naive_failed = datetime(2026, 9, 23, 2, 0)
    panel = build_knowledge_base_panel(
        _run_row(), current_snapshot={}, last_failed_at=naive_failed
    )
    assert panel["last_attempt_failed_at"] == naive_failed


def test_load_status_provenance_four_results_puts_failed_at_in_kb():
    conn = _FakeConn(
        results=[
            tuple(_deploy_row().values()),
            tuple(_run_row().values()),
            ({"data_manager": {}},),
            (FAILED_AT,),
        ]
    )
    view = load_status_provenance(conn)
    assert view["knowledge_base"]["last_attempt_failed_at"] == FAILED_AT


def test_load_status_provenance_three_results_leaves_failed_at_none():
    conn = _FakeConn(
        results=[
            tuple(_deploy_row().values()),
            tuple(_run_row().values()),
            ({"data_manager": {}},),
        ]
    )
    view = load_status_provenance(conn)
    assert view["knowledge_base"]["last_attempt_failed_at"] is None


# ---------------------------------------------------------------------------
# D4 — unavailable current config
# ---------------------------------------------------------------------------


def test_build_kb_panel_none_current_snapshot_gives_no_drift_and_config_unavailable():
    panel = build_knowledge_base_panel(_run_row(), None)
    assert panel["drift"] == []
    assert panel["current_config_available"] is False


def test_build_kb_panel_with_mapping_current_snapshot_gives_config_available_and_drift():
    run = _run_row(config_snapshot={"categorization": True})
    panel = build_knowledge_base_panel(run, {"categorization": False})
    assert panel["current_config_available"] is True
    assert panel["drift"] == [
        {"key": "categorization", "current": False, "at_ingest": True}
    ]


class _NthCallRaisingCursor:
    """Raises on the Nth execute call; otherwise behaves like _FakeCursor."""

    def __init__(self, results, raise_on_call):
        self.results = list(results)
        self.raise_on_call = raise_on_call
        self._row = None
        self._call_count = 0

    def execute(self, sql, params=None):
        self._call_count += 1
        if self._call_count == self.raise_on_call:
            raise RuntimeError("forced error on call %d" % self.raise_on_call)
        self._row = self.results.pop(0) if self.results else None

    def fetchone(self):
        return self._row

    def close(self):
        pass


class _NthCallRaisingConn:
    def __init__(self, results, raise_on_call):
        self._cursor = _NthCallRaisingCursor(results, raise_on_call)

    def cursor(self, **kwargs):
        return self._cursor


def test_load_provenance_config_query_raises_keeps_deploy_and_run_panels_available():
    conn = _NthCallRaisingConn(
        results=[
            tuple(_deploy_row().values()),
            tuple(_run_row().values()),
        ],
        raise_on_call=3,
    )
    view = load_status_provenance(conn)
    assert view["deployment"]["available"] is True
    assert view["knowledge_base"]["available"] is True
    assert view["knowledge_base"]["drift"] == []
    assert view["knowledge_base"]["current_config_available"] is False


class _RollbackRecordingConn(_NthCallRaisingConn):
    def __init__(self, results, raise_on_call):
        super().__init__(results, raise_on_call)
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


_FAILED_AT = datetime(2026, 9, 23, 1, 0, tzinfo=timezone.utc)


def test_a_failed_config_query_still_reads_the_newer_failed_attempt():
    conn = _RollbackRecordingConn(
        results=[
            tuple(_deploy_row().values()),
            tuple(_run_row().values()),
            (_FAILED_AT,),
        ],
        raise_on_call=3,
    )
    view = load_status_provenance(conn)
    assert view["knowledge_base"]["last_attempt_failed_at"] == _FAILED_AT
    assert view["knowledge_base"]["current_config_available"] is False


def test_a_failed_config_query_rolls_the_connection_back():
    conn = _RollbackRecordingConn(
        results=[
            tuple(_deploy_row().values()),
            tuple(_run_row().values()),
            (_FAILED_AT,),
        ],
        raise_on_call=3,
    )
    load_status_provenance(conn)
    assert conn.rollbacks == 1


def test_a_config_snapshot_build_error_still_reads_the_failed_attempt(monkeypatch):
    import src.interfaces.chat_app.status_provenance as provenance

    def _boom(_config):
        raise ValueError("bad config")

    monkeypatch.setattr(provenance, "build_ingest_config_snapshot", _boom)
    conn = _FakeConn(
        results=[
            tuple(_deploy_row().values()),
            tuple(_run_row().values()),
            ({"data_manager": {}},),
            (_FAILED_AT,),
        ]
    )
    view = load_status_provenance(conn)
    assert view["knowledge_base"]["last_attempt_failed_at"] == _FAILED_AT
    assert view["knowledge_base"]["current_config_available"] is False


def test_a_config_read_error_without_rollback_support_does_not_raise():
    conn = _NthCallRaisingConn(
        results=[
            tuple(_deploy_row().values()),
            tuple(_run_row().values()),
            (_FAILED_AT,),
        ],
        raise_on_call=3,
    )
    view = load_status_provenance(conn)
    assert view["knowledge_base"]["last_attempt_failed_at"] == _FAILED_AT


def test_load_provenance_config_query_no_row_gives_config_unavailable():
    conn = _FakeConn(
        results=[
            tuple(_deploy_row().values()),
            tuple(_run_row().values()),
        ]
    )
    view = load_status_provenance(conn)
    assert view["knowledge_base"]["current_config_available"] is False
    assert view["knowledge_base"]["drift"] == []


# ---------------------------------------------------------------------------
# D5 — pin mismatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pin_matched, expected_mismatch",
    [(True, False), (False, True), (None, False)],
)
def test_pin_mismatch_reflects_only_a_confirmed_false_verdict(
    pin_matched, expected_mismatch
):
    panel = build_deployment_panel(_deploy_row(pin_matched=pin_matched))
    assert panel["pin_mismatch"] is expected_mismatch


def test_unavailable_deployment_panel_has_no_pin_mismatch():
    assert build_deployment_panel(None)["pin_mismatch"] is False


# ---------------------------------------------------------------------------
# D2–D3 — corpus run for config and drift
# ---------------------------------------------------------------------------


def test_corpus_run_config_and_drift_come_from_the_corpus_not_the_newest_run():
    corpus = _run_row(
        status="updated",
        started_at=DEPLOYED,
        completed_at=STARTED,
        documents_embedded=5,
        chunk_count=50,
        config_snapshot={"categorization": False, "chunk_size": 1000},
    )
    run = _run_row(
        status="up_to_date", config_snapshot={"categorization": True, "chunk_size": 500}
    )
    current = {"categorization": True, "chunk_size": 500}

    panel = build_knowledge_base_panel(run, current, corpus_run=corpus)

    assert panel["config"] == {"categorization": False, "chunk_size": 1000}
    sorted_drift = sorted(panel["drift"], key=lambda d: d["key"])
    assert sorted_drift == [
        {"key": "categorization", "current": True, "at_ingest": False},
        {"key": "chunk_size", "current": 500, "at_ingest": 1000},
    ]
    assert panel["corpus_config_available"] is True
    assert panel["corpus_built_at"] == STARTED
    assert panel["status"] == "up_to_date"
    assert panel["started_at"] == STARTED
    assert panel["completed_at"] == FINISHED
    assert panel["duration"] == "1 hr 7 min"
    assert panel["documents_embedded"] == 1091
    assert panel["chunk_count"] == 6926


def test_updated_run_uses_its_own_snapshot_and_corpus_built_at_is_completed_at():
    run = _run_row()  # status="updated", completed_at=FINISHED
    different_corpus = _run_row(
        status="updated",
        completed_at=DEPLOYED,
        config_snapshot={"categorization": True, "chunk_size": 999},
    )

    panel = build_knowledge_base_panel(run, {}, corpus_run=different_corpus)

    assert panel["config"] == {"categorization": False, "chunk_size": 1000}
    assert panel["corpus_built_at"] == FINISHED


def test_up_to_date_run_with_no_corpus_run_gives_unknown_config():
    run = _run_row(status="up_to_date")

    panel = build_knowledge_base_panel(run, {}, corpus_run=None)

    assert panel["config"] == {}
    assert panel["drift"] == []
    assert panel["corpus_config_available"] is False
    assert panel["corpus_built_at"] is None
    assert panel["current_config_available"] is True


def test_absent_run_gives_corpus_config_unavailable_and_no_corpus_built_at():
    panel = build_knowledge_base_panel(None, {})

    assert panel["corpus_config_available"] is False
    assert panel["corpus_built_at"] is None


def test_loader_up_to_date_newest_run_reads_corpus_row_for_config():
    upd = _run_row(
        status="updated",
        completed_at=STARTED,
        config_snapshot={"categorization": False, "chunk_size": 1000},
    )
    utd = _run_row(
        status="up_to_date",
        config_snapshot={"categorization": False, "chunk_size": 500},
    )
    conn = _FakeConn(
        results=[
            tuple(_deploy_row().values()),
            tuple(utd.values()),
            ({"chunk_size": 500},),
            None,
            tuple(upd.values()),
        ]
    )

    view = load_status_provenance(conn)
    kb = view["knowledge_base"]

    assert kb["config"] == {"categorization": False, "chunk_size": 1000}
    assert kb["drift"] == [{"key": "chunk_size", "current": 500, "at_ingest": 1000}]
    assert kb["corpus_built_at"] == STARTED
    assert kb["status"] == "up_to_date"
    assert kb["completed_at"] == FINISHED
    assert kb["corpus_config_available"] is True


class _CountingFakeCursor:
    def __init__(self, results):
        self.results = list(results)
        self._row = None
        self.execute_count = 0

    def execute(self, sql, params=None):
        self.execute_count += 1
        self._row = self.results.pop(0) if self.results else None

    def fetchone(self):
        return self._row

    def close(self):
        pass


class _CountingFakeConn:
    def __init__(self, results=()):
        self.cursor_obj = _CountingFakeCursor(results)

    def cursor(self, **kwargs):
        return self.cursor_obj

    def close(self):
        pass

    def rollback(self):
        pass


def test_loader_updated_newest_run_executes_exactly_four_reads():
    conn = _CountingFakeConn(
        results=[
            tuple(_deploy_row().values()),
            tuple(_run_row().values()),
            ({"data_manager": {}},),
            None,
        ]
    )
    load_status_provenance(conn)
    assert conn.cursor_obj.execute_count == 4


def test_loader_corpus_read_raises_gives_unknown_config_and_logs_warning(caplog):
    upd = _run_row(
        status="updated",
        completed_at=STARTED,
        config_snapshot={"categorization": False, "chunk_size": 1000},
    )
    utd = _run_row(
        status="up_to_date",
        config_snapshot={"categorization": False, "chunk_size": 500},
    )
    conn = _NthCallRaisingConn(
        results=[
            tuple(_deploy_row().values()),
            tuple(utd.values()),
            ({"chunk_size": 500},),
            None,
        ],
        raise_on_call=5,
    )

    with caplog.at_level(logging.WARNING):
        view = load_status_provenance(conn)

    assert view["deployment"]["available"] is True
    kb = view["knowledge_base"]
    assert kb["available"] is True
    assert kb["status"] == "up_to_date"
    assert kb["config"] == {}
    assert kb["drift"] == []
    assert kb["corpus_config_available"] is False
    assert any(
        r.levelname == "WARNING"
        and r.name == "src.interfaces.chat_app.status_provenance"
        for r in caplog.records
    )


def test_sql_latest_corpus_run_selects_newest_updated_run_only():
    from src.interfaces.chat_app.status_provenance import _SQL_LATEST_CORPUS_RUN

    assert "status = 'updated'" in _SQL_LATEST_CORPUS_RUN
    assert "up_to_date" not in _SQL_LATEST_CORPUS_RUN
    assert "ORDER BY completed_at DESC" in _SQL_LATEST_CORPUS_RUN
    assert "LIMIT 1" in _SQL_LATEST_CORPUS_RUN


class _SqlRecordingFakeCursor:
    def __init__(self, results):
        self.results = list(results)
        self._row = None
        self.sqls = []

    def execute(self, sql, params=None):
        self.sqls.append(sql)
        self._row = self.results.pop(0) if self.results else None

    def fetchone(self):
        return self._row

    def close(self):
        pass


class _SqlRecordingFakeConn:
    def __init__(self, results=()):
        self.cursor_obj = _SqlRecordingFakeCursor(results)

    def cursor(self, **kwargs):
        return self.cursor_obj

    def close(self):
        pass

    def rollback(self):
        pass


def test_loader_fifth_read_runs_the_corpus_query():
    from src.interfaces.chat_app.status_provenance import _SQL_LATEST_CORPUS_RUN

    upd = _run_row(
        status="updated",
        completed_at=STARTED,
        config_snapshot={"categorization": False, "chunk_size": 1000},
    )
    utd = _run_row(
        status="up_to_date",
        config_snapshot={"categorization": False, "chunk_size": 500},
    )
    conn = _SqlRecordingFakeConn(
        results=[
            tuple(_deploy_row().values()),
            tuple(utd.values()),
            ({"chunk_size": 500},),
            None,
            tuple(upd.values()),
        ]
    )

    load_status_provenance(conn)

    assert len(conn.cursor_obj.sqls) == 5
    assert conn.cursor_obj.sqls[4] == _SQL_LATEST_CORPUS_RUN


def test_loader_up_to_date_newest_run_with_no_updated_run_gives_unknown_config():
    utd = _run_row(
        status="up_to_date",
        config_snapshot={"categorization": False, "chunk_size": 500},
    )
    conn = _FakeConn(
        results=[
            tuple(_deploy_row().values()),
            tuple(utd.values()),
            ({"chunk_size": 500},),
            None,
            None,
        ]
    )

    view = load_status_provenance(conn)
    kb = view["knowledge_base"]

    assert kb["available"] is True
    assert kb["status"] == "up_to_date"
    assert kb["config"] == {}
    assert kb["drift"] == []
    assert kb["corpus_config_available"] is False
    assert kb["corpus_built_at"] is None

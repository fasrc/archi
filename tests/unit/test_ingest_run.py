"""Unit tests for the ingest-run record.

The record is what lets the status board attribute a corpus to the run that
built it, and to the configuration that governed that run. It must therefore be
written for every completed run — including one that found the store already up
to date — and it must never be able to fail the ingest that produced it.
"""

from datetime import datetime, timezone

import pytest

from src.utils.ingest_run import (
    INGEST_RUN_STATUSES,
    collect_ingest_counts,
    record_ingest_run,
)


class _FakeCursor:
    def __init__(self, rows=None, raises=False):
        self.rows = rows if rows is not None else []
        self.raises = raises
        self.executed = []
        self._result = []

    def execute(self, sql, params=None):
        if self.raises:
            raise RuntimeError("query exploded")
        self.executed.append((sql, params))
        self._result = self.rows.pop(0) if self.rows else []

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None

    def close(self):
        pass


class _FakeConn:
    def __init__(self, rows=None, raises=False):
        self.cursor_obj = _FakeCursor(rows, raises)
        self.committed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def close(self):
        pass


STARTED = datetime(2026, 9, 22, 19, 49, tzinfo=timezone.utc)
SNAPSHOT = {"categorization": False, "chunking_strategy": "sentence"}


# ---------------------------------------------------------------------------
# record_ingest_run
# ---------------------------------------------------------------------------


def test_a_completed_run_is_recorded_with_its_window_and_snapshot():
    conn = _FakeConn()

    assert (
        record_ingest_run(
            conn,
            started_at=STARTED,
            status="updated",
            config_snapshot=SNAPSHOT,
            counts={
                "documents_embedded": 1091,
                "documents_failed": 0,
                "documents_pending": 0,
                "chunk_count": 6926,
            },
        )
        is True
    )

    sql, params = conn.cursor_obj.executed[0]
    assert "INSERT INTO ingest_run" in sql
    assert STARTED in params
    assert 1091 in params
    assert conn.committed is True


def test_an_up_to_date_run_is_recorded_too():
    """A no-change re-ingest still happened.

    The manager's completion log sits inside the "needs updating" branch only,
    so recording there would leave the board reporting a stale last ingest after
    a run that really occurred.
    """
    conn = _FakeConn()

    assert (
        record_ingest_run(
            conn,
            started_at=STARTED,
            status="up_to_date",
            config_snapshot=SNAPSHOT,
            counts={},
        )
        is True
    )

    _, params = conn.cursor_obj.executed[0]
    assert "up_to_date" in params


def test_every_declared_status_is_accepted():
    for status in INGEST_RUN_STATUSES:
        conn = _FakeConn()
        assert (
            record_ingest_run(
                conn,
                started_at=STARTED,
                status=status,
                config_snapshot=SNAPSHOT,
                counts={},
            )
            is True
        )


def test_an_unknown_status_is_refused_rather_than_violating_the_check_constraint():
    conn = _FakeConn()

    assert (
        record_ingest_run(
            conn,
            started_at=STARTED,
            status="finished-ish",
            config_snapshot=SNAPSHOT,
            counts={},
        )
        is False
    )
    assert conn.cursor_obj.executed == []


def test_missing_counts_become_none_not_zero():
    """Zero embedded documents and 'we did not count' are different facts."""
    conn = _FakeConn()

    record_ingest_run(
        conn,
        started_at=STARTED,
        status="up_to_date",
        config_snapshot=SNAPSHOT,
        counts={},
    )

    _, params = conn.cursor_obj.executed[0]
    assert None in params
    assert 0 not in params


def test_a_failed_write_never_fails_the_ingest():
    """The corpus is the product; the record is provenance (design D9)."""
    conn = _FakeConn(raises=True)

    assert (
        record_ingest_run(
            conn,
            started_at=STARTED,
            status="updated",
            config_snapshot=SNAPSHOT,
            counts={},
        )
        is False
    )


def test_a_write_without_a_connection_never_raises():
    assert (
        record_ingest_run(
            None,
            started_at=STARTED,
            status="updated",
            config_snapshot=SNAPSHOT,
            counts={},
        )
        is False
    )


# ---------------------------------------------------------------------------
# collect_ingest_counts
# ---------------------------------------------------------------------------


def test_counts_group_documents_by_ingestion_status():
    conn = _FakeConn(
        rows=[
            [("embedded", 1091), ("failed", 2), ("pending", 5)],
            [(6926,)],
        ]
    )

    counts = collect_ingest_counts(conn)

    assert counts == {
        "documents_embedded": 1091,
        "documents_failed": 2,
        "documents_pending": 5,
        "chunk_count": 6926,
    }


def test_absent_statuses_count_zero():
    """A status with no rows is genuinely zero, unlike an unqueryable count."""
    conn = _FakeConn(rows=[[("embedded", 10)], [(42,)]])

    counts = collect_ingest_counts(conn)

    assert counts["documents_failed"] == 0
    assert counts["documents_pending"] == 0


def test_an_embedding_status_outside_the_declared_set_is_ignored():
    conn = _FakeConn(rows=[[("embedded", 10), ("weird", 3)], [(42,)]])

    counts = collect_ingest_counts(conn)

    assert counts["documents_embedded"] == 10
    assert "documents_weird" not in counts


def test_a_failing_count_query_yields_an_empty_mapping():
    conn = _FakeConn(raises=True)

    assert collect_ingest_counts(conn) == {}


@pytest.mark.parametrize("conn", [None, object()])
def test_counts_never_raise_on_a_bad_connection(conn):
    assert collect_ingest_counts(conn) == {}


# ---------------------------------------------------------------------------
# VectorStoreManager._record_ingest_run (the ingest-side call site)
# ---------------------------------------------------------------------------


def _bare_manager(**attrs):
    """Build a manager without running __init__, as the sibling suites do."""
    from src.data_manager.vectorstore.manager import VectorStoreManager

    mgr = VectorStoreManager.__new__(VectorStoreManager)
    mgr._pg_config = {"host": "db"}
    for key, value in attrs.items():
        setattr(mgr, key, value)
    return mgr


def test_manager_records_the_run_with_a_snapshot_of_its_own_config(monkeypatch):
    conn = _FakeConn(rows=[[("embedded", 3)], [(9,)]])
    monkeypatch.setattr(
        "src.data_manager.vectorstore.manager.psycopg2.connect",
        lambda **kwargs: conn,
    )

    mgr = _bare_manager(
        _data_manager_config={"processing": {"categorization": {"enabled": True}}}
    )
    mgr._record_ingest_run(STARTED, "updated")

    insert_sql, params = conn.cursor_obj.executed[-1]
    assert "INSERT INTO ingest_run" in insert_sql
    assert "updated" in params
    # The snapshot must describe THIS run's config, not current config read back.
    assert '"categorization": true' in params[-1]


def test_manager_records_the_run_even_without_a_config_attribute(monkeypatch):
    """Provenance must degrade to defaults, never raise inside an ingest."""
    conn = _FakeConn(rows=[[("embedded", 1)], [(1,)]])
    monkeypatch.setattr(
        "src.data_manager.vectorstore.manager.psycopg2.connect",
        lambda **kwargs: conn,
    )

    mgr = _bare_manager()
    mgr._record_ingest_run(STARTED, "up_to_date")

    _, params = conn.cursor_obj.executed[-1]
    assert '"categorization": false' in params[-1]


def test_manager_swallows_a_connection_failure(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("no database")

    monkeypatch.setattr("src.data_manager.vectorstore.manager.psycopg2.connect", _boom)

    mgr = _bare_manager(_data_manager_config={})
    mgr._record_ingest_run(STARTED, "updated")  # must not raise


# ---------------------------------------------------------------------------
# Review round 1: a failed or interrupted run must be recorded, not skipped
# ---------------------------------------------------------------------------


def test_a_raising_sync_records_a_failed_run_and_reraises(monkeypatch):
    """Otherwise the board shows the PREVIOUS run as the current corpus."""
    conn = _FakeConn(rows=[[("embedded", 1)], [(1,)]])
    monkeypatch.setattr(
        "src.data_manager.vectorstore.manager.psycopg2.connect",
        lambda **kwargs: conn,
    )

    mgr = _bare_manager(_data_manager_config={})
    monkeypatch.setattr(
        mgr, "_sync_vectorstore", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError):
        mgr.update_vectorstore()

    _, params = conn.cursor_obj.executed[-1]
    assert "failed" in params


def test_a_successful_sync_records_the_status_it_returned(monkeypatch):
    conn = _FakeConn(rows=[[("embedded", 1)], [(1,)]])
    monkeypatch.setattr(
        "src.data_manager.vectorstore.manager.psycopg2.connect",
        lambda **kwargs: conn,
    )

    mgr = _bare_manager(_data_manager_config={})
    monkeypatch.setattr(mgr, "_sync_vectorstore", lambda: "up_to_date")

    mgr.update_vectorstore()

    _, params = conn.cursor_obj.executed[-1]
    assert "up_to_date" in params

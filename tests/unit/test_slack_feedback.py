"""Unit tests for slack_feedback helpers and SlackFeedbackStore (task 3.1)."""

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest import mock

import psycopg2
import psycopg2.errors
import pytest

from src.interfaces.slack_feedback import (
    _CHECKBOXES_ACTION_ID,
    _CHECKBOXES_BLOCK_ID,
    _COMMENT_ACTION_ID,
    _COMMENT_BLOCK_ID,
    FEEDBACK_FORM_CALLBACK_ID,
    FEEDBACK_PROMPT_TEXT,
    FEEDBACK_THANKS_TEXT,
    HELPFUL_ACTION_ID,
    MAX_FEEDBACK_COMMENT_CHARS,
    NOT_HELPFUL_ACTION_ID,
    FeedbackRecord,
    SlackFeedbackStore,
    build_feedback_blocks,
    build_feedback_modal,
    like_record,
    parse_action_ts,
    parse_feedback_submission,
    parse_message_id,
)
from src.utils.connection_pool import ConnectionPool

# ---------------------------------------------------------------------------
# parse_message_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("42", 42),
        ("1", 1),
        ("100", 100),
    ],
)
def test_parse_message_id_accepts_positive_integer_strings(value, expected):
    assert parse_message_id(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "0", "-1", "1.5", "abc", " 42", "４２", None, 42],
)
def test_parse_message_id_invalid_returns_none(value):
    assert parse_message_id(value) is None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_constants_are_correct():
    assert HELPFUL_ACTION_ID == "archi_feedback_helpful"
    assert NOT_HELPFUL_ACTION_ID == "archi_feedback_not_helpful"
    assert FEEDBACK_FORM_CALLBACK_ID == "archi_feedback_form"
    assert FEEDBACK_THANKS_TEXT == "Thanks for the feedback."
    assert MAX_FEEDBACK_COMMENT_CHARS == 2000
    assert isinstance(FEEDBACK_PROMPT_TEXT, str) and FEEDBACK_PROMPT_TEXT


# ---------------------------------------------------------------------------
# build_feedback_blocks
# ---------------------------------------------------------------------------


def test_build_feedback_blocks_section_contains_prompt_text():
    blocks = build_feedback_blocks(42)
    sections = [b for b in blocks if b["type"] == "section"]
    assert len(sections) == 1
    assert sections[0]["text"]["text"] == FEEDBACK_PROMPT_TEXT


def test_build_feedback_blocks_actions_has_both_ids_and_value():
    blocks = build_feedback_blocks(42)
    actions = [b for b in blocks if b["type"] == "actions"]
    assert len(actions) == 1
    elements = actions[0]["elements"]
    ids = {e["action_id"] for e in elements}
    assert HELPFUL_ACTION_ID in ids
    assert NOT_HELPFUL_ACTION_ID in ids
    for e in elements:
        assert e["value"] == "42"


# ---------------------------------------------------------------------------
# build_feedback_modal
# ---------------------------------------------------------------------------


def test_build_feedback_modal_type_callback_metadata():
    modal = build_feedback_modal(42)
    assert modal["type"] == "modal"
    assert modal["callback_id"] == FEEDBACK_FORM_CALLBACK_ID
    assert modal["private_metadata"] == "42"


def test_build_feedback_modal_checkboxes_has_three_options():
    modal = build_feedback_modal(42)
    checkboxes = None
    for block in modal["blocks"]:
        el = block.get("element") or {}
        if el.get("type") == "checkboxes":
            checkboxes = el
            break
    assert checkboxes is not None
    option_values = {opt["value"] for opt in checkboxes["options"]}
    assert option_values == {"incorrect", "unhelpful", "inappropriate"}


def test_build_feedback_modal_has_multiline_comment_input():
    modal = build_feedback_modal(42)
    comment_input = None
    for block in modal["blocks"]:
        el = block.get("element") or {}
        if el.get("type") == "plain_text_input":
            comment_input = el
            break
    assert comment_input is not None
    assert comment_input.get("multiline") is True


# ---------------------------------------------------------------------------
# parse_feedback_submission
# ---------------------------------------------------------------------------


def _view(
    callback_id=FEEDBACK_FORM_CALLBACK_ID,
    private_metadata="42",
    selected=(),
    comment=None,
):
    return {
        "callback_id": callback_id,
        "private_metadata": private_metadata,
        "state": {
            "values": {
                _CHECKBOXES_BLOCK_ID: {
                    _CHECKBOXES_ACTION_ID: {
                        "type": "checkboxes",
                        "selected_options": [{"value": s} for s in selected],
                    }
                },
                _COMMENT_BLOCK_ID: {
                    _COMMENT_ACTION_ID: {
                        "type": "plain_text_input",
                        "value": comment,
                    }
                },
            }
        },
    }


def test_parse_feedback_submission_returns_dislike_record_with_flags_and_comment():
    view = _view(selected=["incorrect", "inappropriate"], comment="wrong partition")
    r = parse_feedback_submission(view)
    assert r is not None
    assert r.message_id == 42
    assert r.feedback == "dislike"
    assert r.feedback_msg == "wrong partition"
    assert r.incorrect is True
    assert r.unhelpful is False
    assert r.inappropriate is True


def test_parse_feedback_submission_wrong_callback_id_returns_none():
    assert parse_feedback_submission(_view(callback_id="other")) is None


def test_parse_feedback_submission_invalid_private_metadata_returns_none():
    assert parse_feedback_submission(_view(private_metadata="-1")) is None


def test_parse_feedback_submission_no_flags_all_false_no_comment():
    r = parse_feedback_submission(_view())
    assert r is not None
    assert r.incorrect is False
    assert r.unhelpful is False
    assert r.inappropriate is False
    assert r.feedback_msg is None


def test_parse_feedback_submission_whitespace_comment_gives_none():
    r = parse_feedback_submission(_view(comment="   "))
    assert r.feedback_msg is None


def test_parse_feedback_submission_long_comment_truncated_to_2000():
    r = parse_feedback_submission(_view(comment="x" * 3000))
    assert r.feedback_msg == "x" * MAX_FEEDBACK_COMMENT_CHARS


def test_parse_feedback_submission_null_comment_value_gives_none_flags_still_parsed():
    view = _view(selected=["incorrect"], comment=None)
    r = parse_feedback_submission(view)
    assert r is not None
    assert r.feedback_msg is None
    assert r.incorrect is True


def test_parse_feedback_submission_missing_state_does_not_raise():
    view = {"callback_id": FEEDBACK_FORM_CALLBACK_ID, "private_metadata": "42"}
    r = parse_feedback_submission(view)
    assert r is not None
    assert r.feedback_msg is None
    assert r.incorrect is False


# ---------------------------------------------------------------------------
# like_record
# ---------------------------------------------------------------------------


def test_like_record_feedback_and_all_flags_none():
    r = like_record(42)
    assert isinstance(r, FeedbackRecord)
    assert r.message_id == 42
    assert r.feedback == "like"
    assert r.feedback_msg is None
    assert r.incorrect is None
    assert r.unhelpful is None
    assert r.inappropriate is None


# ---------------------------------------------------------------------------
# SlackFeedbackStore
# ---------------------------------------------------------------------------


def _fake_conn():
    """Return (conn_mock, cursor_mock) with cursor as a working context manager."""
    cur = mock.Mock()
    conn = mock.Mock()
    conn.cursor.return_value.__enter__ = mock.Mock(return_value=cur)
    conn.cursor.return_value.__exit__ = mock.Mock(return_value=False)
    return conn, cur


def _fake_pool(conns):
    """Return a pool whose get_connection() yields each conn in order."""
    state = {"idx": 0}
    pool = mock.Mock()

    @contextmanager
    def get_connection():
        conn = conns[state["idx"]]
        state["idx"] += 1
        yield conn

    pool.get_connection = get_connection
    return pool


def _pk_pool(conn_specs):
    """Primary-key aware fake pool. Returns (pool, rows).

    conn_specs: list of "working" | "ambiguous" | "down".
    rows is shared across all connections and keyed by (message_id, feedback_ts).
    "ambiguous" commits the row then raises OperationalError (models network loss after commit).
    "down" raises OperationalError from execute (models a dead pooled connection).
    """
    rows = {}
    connections = []

    for spec in conn_specs:
        pending = {}
        cur = mock.Mock()
        cur.__enter__ = mock.Mock(return_value=cur)
        cur.__exit__ = mock.Mock(return_value=False)

        if spec == "down":
            cur.execute.side_effect = psycopg2.OperationalError("down")
        else:

            def _make_execute(_pending=pending):
                def _execute(sql, params):
                    key = (params[0], params[1])
                    if key in rows:
                        raise psycopg2.errors.UniqueViolation("duplicate key")
                    _pending[key] = params

                return _execute

            cur.execute.side_effect = _make_execute()

        conn = mock.Mock()
        conn.closed = 0
        conn.cursor.return_value = cur
        conn.rollback.return_value = None

        if spec == "ambiguous":

            def _make_commit(_pending=pending):
                def _commit():
                    rows.update(_pending)
                    _pending.clear()
                    raise psycopg2.OperationalError("connection lost after commit")

                return _commit

            conn.commit.side_effect = _make_commit()
        else:

            def _make_commit(_pending=pending):
                def _commit():
                    rows.update(_pending)
                    _pending.clear()

                return _commit

            conn.commit.side_effect = _make_commit()

        connections.append(conn)

    state = {"idx": 0}
    pool = mock.Mock()

    @contextmanager
    def get_connection():
        conn = connections[state["idx"]]
        state["idx"] += 1
        yield conn

    pool.get_connection = get_connection
    return pool, rows


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_store_insert_executes_insert_sql_with_correct_params():
    conn, cur = _fake_conn()
    store = SlackFeedbackStore(_fake_pool([conn]))
    record = like_record(42)
    store.insert(record, now=_NOW)
    assert cur.execute.call_count == 1
    sql, params = cur.execute.call_args[0]
    assert "INSERT" in sql.upper()
    assert params == (42, _NOW, "like", None, None, None, None)
    conn.commit.assert_called_once()


def test_store_two_inserts_give_two_executes_and_no_delete():
    conn1, cur1 = _fake_conn()
    conn2, cur2 = _fake_conn()
    store = SlackFeedbackStore(_fake_pool([conn1, conn2]))
    store.insert(like_record(42), now=_NOW)
    store.insert(like_record(42), now=_NOW)
    assert cur1.execute.call_count == 1
    assert cur2.execute.call_count == 1
    sql = cur1.execute.call_args[0][0]
    assert "DELETE" not in sql.upper()


def test_store_retries_once_on_operational_error():
    conn1, cur1 = _fake_conn()
    cur1.execute.side_effect = psycopg2.OperationalError("dead")
    conn2, cur2 = _fake_conn()
    store = SlackFeedbackStore(_fake_pool([conn1, conn2]))
    store.insert(like_record(42), now=_NOW)
    assert cur1.execute.call_count == 1
    assert cur2.execute.call_count == 1
    conn2.commit.assert_called_once()


def test_store_insert_uses_utc_now_when_no_now_given():
    conn, cur = _fake_conn()
    store = SlackFeedbackStore(_fake_pool([conn]))
    store.insert(like_record(42))
    _, params = cur.execute.call_args[0]
    ts = params[1]
    assert ts.tzinfo is not None


def test_store_drains_every_stale_idle_connection_after_a_restart():
    # The pool keeps up to min_conn (5) idle connections; after a Postgres restart
    # all of them can be dead, and only a sixth checkout opens a fresh one.
    stale = []
    for i in range(5):
        conn, cur = _fake_conn()
        cur.execute.side_effect = psycopg2.OperationalError(f"dead {i}")
        stale.append(conn)
    fresh, fresh_cur = _fake_conn()
    store = SlackFeedbackStore(_fake_pool(stale + [fresh]))
    store.insert(like_record(42), now=_NOW)
    assert fresh_cur.execute.call_count == 1
    fresh.commit.assert_called_once()


def test_store_raises_when_every_attempt_fails():
    conns = []
    for i in range(10):
        conn, cur = _fake_conn()
        cur.execute.side_effect = psycopg2.InterfaceError(f"dead {i}")
        conns.append(conn)
    store = SlackFeedbackStore(_fake_pool(conns))
    with pytest.raises(psycopg2.InterfaceError):
        store.insert(like_record(42), now=_NOW)


# ---------------------------------------------------------------------------
# parse_action_ts (task 1.1 a)
# ---------------------------------------------------------------------------


def test_parse_action_ts_full_microseconds():
    assert parse_action_ts("1700000000.123456") == datetime(
        2023, 11, 14, 22, 13, 20, 123456, tzinfo=timezone.utc
    )


def test_parse_action_ts_integer_only_gives_zero_microseconds():
    result = parse_action_ts("1700000000")
    assert result is not None
    assert result.microsecond == 0


def test_parse_action_ts_single_fraction_digit():
    result = parse_action_ts("1700000000.5")
    assert result is not None
    assert result.microsecond == 500000


def test_parse_action_ts_seven_fraction_digits_truncated_to_six():
    result = parse_action_ts("1700000000.1234567")
    assert result is not None
    assert result.microsecond == 123456


def test_parse_action_ts_deterministic():
    s = "1700000000.123456"
    assert parse_action_ts(s) == parse_action_ts(s)


@pytest.mark.parametrize(
    "value",
    [
        None,
        1700000000.5,
        "",
        "abc",
        "-1.0",
        " 1.0",
        "1.0 ",
        "１.0",
        "1.",
        "123456789012.0",
    ],
)
def test_parse_action_ts_invalid_returns_none(value):
    assert parse_action_ts(value) is None


# ---------------------------------------------------------------------------
# FeedbackRecord.feedback_ts field (task 1.1 b)
# ---------------------------------------------------------------------------


def test_feedback_record_has_feedback_ts_as_last_field():
    assert FeedbackRecord._fields[-1] == "feedback_ts"


def test_feedback_record_feedback_ts_defaults_to_none():
    r = FeedbackRecord(1, "like", None, None, None, None)
    assert r.feedback_ts is None


def test_like_record_without_ts_has_none_feedback_ts():
    r = like_record(42)
    assert r.feedback_ts is None


def test_like_record_with_ts_sets_feedback_ts():
    ts = datetime(2023, 11, 14, 22, 13, 20, 123456, tzinfo=timezone.utc)
    r = like_record(42, ts)
    assert r.feedback_ts == ts


# ---------------------------------------------------------------------------
# Store time selection (task 1.1 c)
# ---------------------------------------------------------------------------


def test_store_insert_uses_record_feedback_ts_over_now_arg():
    conn, cur = _fake_conn()
    ts = datetime(2023, 11, 14, 22, 13, 20, 123456, tzinfo=timezone.utc)
    SlackFeedbackStore(_fake_pool([conn])).insert(like_record(42, ts), now=_NOW)
    _, params = cur.execute.call_args[0]
    assert params[1] == ts


def test_store_insert_uses_now_arg_when_record_feedback_ts_is_none():
    conn, cur = _fake_conn()
    SlackFeedbackStore(_fake_pool([conn])).insert(like_record(42), now=_NOW)
    _, params = cur.execute.call_args[0]
    assert params[1] == _NOW


# ---------------------------------------------------------------------------
# UniqueViolation silenced (task 1.1 d)
# ---------------------------------------------------------------------------


def test_store_insert_unique_violation_returns_silently(caplog):
    conn, cur = _fake_conn()
    cur.execute.side_effect = psycopg2.errors.UniqueViolation("duplicate key")
    ts = datetime(2023, 11, 14, 22, 13, 20, 123456, tzinfo=timezone.utc)
    with caplog.at_level("DEBUG"):
        SlackFeedbackStore(_fake_pool([conn])).insert(like_record(42, ts))
    assert cur.execute.call_count == 1
    conn.rollback.assert_called_once()
    assert any(r.levelno == logging.INFO for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


# ---------------------------------------------------------------------------
# UniqueViolation through the REAL ConnectionPool (task 1.1 d2)
# ---------------------------------------------------------------------------


def test_store_insert_unique_violation_through_real_pool_no_error_log(caplog):
    returned = []
    cur_fake = mock.Mock()
    cur_fake.execute.side_effect = psycopg2.errors.UniqueViolation("duplicate key")
    cur_fake.__enter__ = mock.Mock(return_value=cur_fake)
    cur_fake.__exit__ = mock.Mock(return_value=False)
    conn_fake = mock.Mock()
    conn_fake.closed = 0
    conn_fake.cursor.return_value = cur_fake

    fake_threaded_pool = mock.Mock()
    fake_threaded_pool.getconn.return_value = conn_fake
    fake_threaded_pool.putconn.side_effect = lambda c: returned.append(c)

    with mock.patch(
        "psycopg2.pool.ThreadedConnectionPool", return_value=fake_threaded_pool
    ):
        pool = ConnectionPool(pg_config={"host": "fake"})

    ts = datetime(2023, 11, 14, 22, 13, 20, 123456, tzinfo=timezone.utc)
    with caplog.at_level("DEBUG"):
        SlackFeedbackStore(pool).insert(like_record(42, ts))

    assert conn_fake in returned
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


# ---------------------------------------------------------------------------
# ForeignKeyViolation propagates (task 1.1 e)
# ---------------------------------------------------------------------------


def test_store_insert_foreign_key_violation_raises_after_one_execute():
    conn, cur = _fake_conn()
    cur.execute.side_effect = psycopg2.errors.ForeignKeyViolation("fk")
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        SlackFeedbackStore(_fake_pool([conn])).insert(like_record(42))
    assert cur.execute.call_count == 1


# ---------------------------------------------------------------------------
# Primary-key oracle: idempotent across ambiguous commit (task 1.1 f)
# ---------------------------------------------------------------------------


def test_store_insert_idempotent_across_ambiguous_commit():
    ts = datetime(2023, 11, 14, 22, 13, 20, 123456, tzinfo=timezone.utc)
    pool, rows = _pk_pool(["ambiguous", "working"])
    SlackFeedbackStore(pool).insert(like_record(42, ts))
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# build_feedback_modal with action_ts (task 2.1 E5)
# ---------------------------------------------------------------------------


def test_build_feedback_modal_with_valid_action_ts_embeds_ts_in_metadata():
    modal = build_feedback_modal(42, "1700000000.123456")
    assert modal["private_metadata"] == "42:1700000000.123456"


def test_build_feedback_modal_without_action_ts_arg_gives_plain_message_id():
    modal = build_feedback_modal(42)
    assert modal["private_metadata"] == "42"


def test_build_feedback_modal_with_invalid_action_ts_gives_plain_message_id():
    modal = build_feedback_modal(42, "abc")
    assert modal["private_metadata"] == "42"


# ---------------------------------------------------------------------------
# parse_feedback_submission with feedback_ts (task 2.1 E5)
# ---------------------------------------------------------------------------


def test_parse_feedback_submission_with_ts_in_metadata_gives_correct_feedback_ts():
    view = _view(private_metadata="42:1700000000.123456")
    r = parse_feedback_submission(view)
    assert r is not None
    assert r.message_id == 42
    assert r.feedback_ts == datetime(
        2023, 11, 14, 22, 13, 20, 123456, tzinfo=timezone.utc
    )


def test_parse_feedback_submission_plain_metadata_gives_none_feedback_ts():
    view = _view(private_metadata="42")
    r = parse_feedback_submission(view)
    assert r is not None
    assert r.message_id == 42
    assert r.feedback_ts is None


def test_parse_feedback_submission_invalid_ts_part_gives_none_feedback_ts():
    view = _view(private_metadata="42:abc")
    r = parse_feedback_submission(view)
    assert r is not None
    assert r.message_id == 42
    assert r.feedback_ts is None


def test_parse_feedback_submission_empty_ts_part_gives_none_feedback_ts():
    view = _view(private_metadata="42:")
    r = parse_feedback_submission(view)
    assert r is not None
    assert r.message_id == 42
    assert r.feedback_ts is None


@pytest.mark.parametrize(
    "private_metadata",
    ["abc:1700000000.0", ":1700000000.0", "0:1700000000.0"],
)
def test_parse_feedback_submission_invalid_message_id_with_ts_returns_none(
    private_metadata,
):
    view = _view(private_metadata=private_metadata)
    assert parse_feedback_submission(view) is None

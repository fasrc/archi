"""Unit tests for slack_feedback helpers and SlackFeedbackStore (task 3.1)."""

from contextlib import contextmanager
from datetime import datetime, timezone
from unittest import mock

import psycopg2
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
    parse_feedback_submission,
    parse_message_id,
)

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

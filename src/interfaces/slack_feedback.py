"""Slack feedback helpers: blocks, modal, parsing, and store (design D1-D8)."""

import re
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

import psycopg2
import psycopg2.errors

from src.utils.logging import get_logger
from src.utils.sql import SQL_INSERT_FEEDBACK

logger = get_logger(__name__)

HELPFUL_ACTION_ID = "archi_feedback_helpful"
NOT_HELPFUL_ACTION_ID = "archi_feedback_not_helpful"
FEEDBACK_FORM_CALLBACK_ID = "archi_feedback_form"
FEEDBACK_PROMPT_TEXT = "Was this answer helpful?"
FEEDBACK_THANKS_TEXT = "Thanks for the feedback."
MAX_FEEDBACK_COMMENT_CHARS = 2000

_CHECKBOXES_BLOCK_ID = "archi_feedback_flags"
_CHECKBOXES_ACTION_ID = "archi_feedback_flags_action"
_COMMENT_BLOCK_ID = "archi_feedback_comment"
_COMMENT_ACTION_ID = "archi_feedback_comment_action"

_ASCII_POS_INT_RE = re.compile(r"[1-9][0-9]*")
_ACTION_TS_RE = re.compile(r"[0-9]{1,11}(\.[0-9]{1,9})?")


def parse_message_id(value):
    """Return positive integer from an ASCII-digit string, or None (design D5)."""
    if not isinstance(value, str):
        return None
    if not _ASCII_POS_INT_RE.fullmatch(value):
        return None
    return int(value)


def parse_action_ts(value):
    """Return UTC datetime from a Slack action_ts string, or None on invalid input (design E1).

    Accepts only ASCII-digit strings matching [0-9]{1,11}(\\.[0-9]{1,9})?.
    The fractional part is truncated to 6 digits (microseconds) and right-padded with zeros.
    Uses integer arithmetic — the same string always produces the same value.
    """
    if not isinstance(value, str):
        return None
    if not _ACTION_TS_RE.fullmatch(value):
        return None
    dot = value.find(".")
    if dot == -1:
        int_part = int(value)
        microseconds = 0
    else:
        int_part = int(value[:dot])
        frac_str = value[dot + 1 :]
        microseconds = int(frac_str[:6].ljust(6, "0"))
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
        seconds=int_part, microseconds=microseconds
    )


class FeedbackRecord(NamedTuple):
    message_id: int
    feedback: str
    feedback_msg: Optional[str]
    incorrect: Optional[bool]
    unhelpful: Optional[bool]
    inappropriate: Optional[bool]
    feedback_ts: Optional[datetime] = None


def build_feedback_blocks(message_id):
    """Return Slack blocks with the feedback prompt and two rating buttons (design D1)."""
    value = str(message_id)
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": FEEDBACK_PROMPT_TEXT},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Helpful"},
                    "action_id": HELPFUL_ACTION_ID,
                    "value": value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Not helpful"},
                    "action_id": NOT_HELPFUL_ACTION_ID,
                    "value": value,
                },
            ],
        },
    ]


def build_feedback_modal(message_id):
    """Return a Slack modal for the Not helpful flow (design D7)."""
    return {
        "type": "modal",
        "callback_id": FEEDBACK_FORM_CALLBACK_ID,
        "private_metadata": str(message_id),
        "title": {"type": "plain_text", "text": "Feedback"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": _CHECKBOXES_BLOCK_ID,
                "optional": True,
                "label": {"type": "plain_text", "text": "What was wrong?"},
                "element": {
                    "type": "checkboxes",
                    "action_id": _CHECKBOXES_ACTION_ID,
                    "options": [
                        {
                            "text": {"type": "plain_text", "text": "Incorrect"},
                            "value": "incorrect",
                        },
                        {
                            "text": {"type": "plain_text", "text": "Unhelpful"},
                            "value": "unhelpful",
                        },
                        {
                            "text": {"type": "plain_text", "text": "Inappropriate"},
                            "value": "inappropriate",
                        },
                    ],
                },
            },
            {
                "type": "input",
                "block_id": _COMMENT_BLOCK_ID,
                "optional": True,
                "label": {"type": "plain_text", "text": "Comment"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": _COMMENT_ACTION_ID,
                    "multiline": True,
                },
            },
        ],
    }


def parse_feedback_submission(view):
    """Parse a view_submission and return a FeedbackRecord, or None on failure."""
    if view.get("callback_id") != FEEDBACK_FORM_CALLBACK_ID:
        return None
    message_id = parse_message_id(view.get("private_metadata"))
    if message_id is None:
        return None

    state = (view.get("state") or {}).get("values") or {}

    flags_block = state.get(_CHECKBOXES_BLOCK_ID) or {}
    flags_action = flags_block.get(_CHECKBOXES_ACTION_ID) or {}
    selected = {opt["value"] for opt in (flags_action.get("selected_options") or [])}
    incorrect = "incorrect" in selected
    unhelpful = "unhelpful" in selected
    inappropriate = "inappropriate" in selected

    comment_block = state.get(_COMMENT_BLOCK_ID) or {}
    comment_action = comment_block.get(_COMMENT_ACTION_ID) or {}
    raw_comment = comment_action.get("value")
    if raw_comment:
        stripped = raw_comment.strip()
        feedback_msg = stripped[:MAX_FEEDBACK_COMMENT_CHARS] if stripped else None
    else:
        feedback_msg = None

    return FeedbackRecord(
        message_id=message_id,
        feedback="dislike",
        feedback_msg=feedback_msg,
        incorrect=incorrect,
        unhelpful=unhelpful,
        inappropriate=inappropriate,
    )


def ephemeral_thanks_kwargs(payload, text):
    """Return chat_postEphemeral kwargs that keep the thanks in the prompt's thread (D7)."""
    kwargs = {
        "channel": (payload.get("channel") or {}).get("id"),
        "user": (payload.get("user") or {}).get("id"),
        "text": text,
    }
    thread_ts = (payload.get("container") or {}).get("thread_ts")
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    return kwargs


def interaction_key(payload):
    """Return a dedup key that a retried envelope repeats, or None if there is none.

    A click is keyed by its user and ``action_ts``; a form submission by its view
    ID, because Slack closes a view once it is submitted.
    """
    if payload.get("type") == "block_actions":
        action_ts = ((payload.get("actions") or [{}])[0]).get("action_ts")
        if action_ts:
            user_id = (payload.get("user") or {}).get("id")
            return (f"block_actions:{user_id}", action_ts)
    elif payload.get("type") == "view_submission":
        view_id = (payload.get("view") or {}).get("id")
        if view_id:
            return ("view_submission", view_id)
    return None


def like_record(message_id, feedback_ts=None):
    """Return a FeedbackRecord for a Helpful click (design D4, E2)."""
    return FeedbackRecord(
        message_id=message_id,
        feedback="like",
        feedback_msg=None,
        incorrect=None,
        unhelpful=None,
        inappropriate=None,
        feedback_ts=feedback_ts,
    )


# ConnectionPool keeps up to min_conn (5) idle connections, and after a Postgres
# restart each one can be dead; the pool drops a broken connection when it gets it
# back, so the sixth checkout opens a fresh connection (design D8).
MAX_INSERT_ATTEMPTS = 6


class SlackFeedbackStore:
    """Insert feedback rows into Postgres; retries on connection errors (design D8)."""

    def __init__(self, pool):
        self._pool = pool

    def insert(self, record, now=None):
        """Insert one feedback row, retrying on OperationalError/InterfaceError (D8, E3, E4)."""
        if record.feedback_ts is not None:
            ts = record.feedback_ts
        elif now is not None:
            ts = now
        else:
            ts = datetime.now(timezone.utc)
        params = (
            record.message_id,
            ts,
            record.feedback,
            record.feedback_msg,
            record.incorrect,
            record.unhelpful,
            record.inappropriate,
        )
        last_exc = None
        for _ in range(MAX_INSERT_ATTEMPTS):
            try:
                with self._pool.get_connection() as conn:
                    with conn.cursor() as cur:
                        try:
                            cur.execute(SQL_INSERT_FEEDBACK, params)
                        except psycopg2.errors.UniqueViolation:
                            conn.rollback()
                            logger.info(
                                "slack feedback: row for message %s at %s is already stored",
                                record.message_id,
                                ts,
                            )
                            return
                    conn.commit()
                return
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
                last_exc = exc
        raise last_exc

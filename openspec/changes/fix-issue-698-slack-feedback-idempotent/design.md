## Context

PR #693 (issue #638) added Slack feedback buttons. The design of that change is `openspec/changes/fix-issue-638-slack-feedback-buttons/design.md`. Its D4 sets `feedback_ts` to `datetime.now(timezone.utc)` for each insert, and its D8 retries the insert on a dead pooled connection.

Relevant facts on `origin/dev` `c2e885ca`:

- `feedback` primary key is `(message_id, feedback_ts)` (`src/cli/templates/init.sql:459-469`). `feedback_ts` is `TIMESTAMPTZ` (microsecond resolution).
- `ConnectionPool.get_connection()` (`src/utils/connection_pool.py:149-199`) rolls the connection back on any `psycopg2.Error` and re-raises it, then returns the connection to the pool. A caught `UniqueViolation` therefore leaves no aborted transaction behind.
- psycopg2 2.9 raises `psycopg2.errors.UniqueViolation` (a subclass of `psycopg2.IntegrityError`) for SQLSTATE `23505`, and `psycopg2.errors.ForeignKeyViolation` for `23503`.
- `interaction_key()` (`src/interfaces/slack_feedback.py`) already reads `actions[0].action_ts` for click dedup. Slack sends `action_ts` as a string of epoch seconds with a fraction, for example `"1700000000.123456"`.
- A `view_submission` payload has no timestamp that a retry repeats. Its `view.id` is stable, but it is not a time.

## Goals / Non-Goals

**Goals:** one stored row for one click and one submission, across an ambiguous commit, a Slack envelope retry, and a bot restart between the two; a duplicate-key error is never logged as a failure.

**Non-Goals:** a schema change; a per-user rating model; the web chat path; dedup of two DIFFERENT clicks by one user (they stay two rows, as in #638 D4).

## Decisions

- **E1: `parse_action_ts(value)` gives an exact, repeatable time.** It accepts only a `str` that fully matches `[0-9]{1,11}(\.[0-9]{1,9})?` (ASCII digits). It returns `datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=<int part>, microseconds=<fraction>)`, where the fraction is the first 6 digits, right-padded with `0`. It uses no `float`, so one string always gives one value. Any other input (`None`, non-string, empty, `"abc"`, `"-1.0"`, `"１.0"`, an overflow) returns `None`. Two strings that differ only after the sixth fraction digit give the same time; this is accepted, because Slack sends 6 digits.
- **E2: `FeedbackRecord.feedback_ts`.** Add `feedback_ts: Optional[datetime] = None` as the LAST field of `FeedbackRecord`, so every current positional and keyword construction stays valid. `like_record(message_id, feedback_ts=None)` passes it through.
- **E3: the store picks the time in this order:** `record.feedback_ts` if it is not `None`; else the `now` argument; else `datetime.now(timezone.utc)`. The store computes the value once, before the retry loop (as today), so in-loop retries repeat it.
- **E4: a unique violation means "already stored", caught INSIDE the pool's `with` block.** The real `ConnectionPool.get_connection()` catches every `psycopg2.Error` that leaves its `with` body and calls `logger.error(f"Database error: {e}")` before it rolls back and re-raises (`src/utils/connection_pool.py:189-192`). A catch in the store's retry loop, OUTSIDE the `with`, is too late: production would still log an ERROR line for every duplicate (issue AC 3). As a result, wrap only `cur.execute(SQL_INSERT_FEEDBACK, params)` in `try: ... except psycopg2.errors.UniqueViolation:` inside `with self._pool.get_connection() as conn:`. In the handler: call `conn.rollback()` (the transaction is aborted), log at INFO (`"slack feedback: row for message %s at %s is already stored"`), and `return` (the `with` exits normally, so the pool returns the connection with no ERROR log). Do not retry it and do not raise it. Any other `psycopg2.IntegrityError` (for example `ForeignKeyViolation`, an answer that was deleted) still leaves the `with` and raises at once, with no retry, as today. A test must run this path through the REAL `ConnectionPool.get_connection()` (patch `psycopg2.pool.ThreadedConnectionPool` with a fake whose `getconn()` returns a fake connection and whose `putconn()` records the return), because a fake `get_connection()` does not log and cannot catch this defect. `src/utils/logging.get_logger` loggers propagate, so `caplog` sees the pool's logger.
- **E5: the click time rides in the modal.** `build_feedback_modal(message_id, action_ts=None)`: when `parse_action_ts(action_ts)` is not `None`, `private_metadata` is `f"{message_id}:{action_ts}"` (the raw string from Slack); otherwise it stays `str(message_id)`, so the existing tests and #638's "Open the form" scenario hold when there is no valid `action_ts`. `parse_feedback_submission(view)` splits `private_metadata` on the FIRST `:`. The left part goes through `parse_message_id()` (invalid → `None`, as today). The right part goes through `parse_action_ts()`; an invalid or absent right part gives `feedback_ts=None` (fallback, not a rejection), because the message ID is still good. A value with no `:` is the old format: the record gets `feedback_ts=None`. Slack limits `private_metadata` to 3000 characters; the new value is under 40.
- **E6: the bot wires the time at its two call sites.** In `SlackBot.handle_interactive()` (`src/interfaces/slack_bot.py`): the Helpful branch calls `like_record(message_id, parse_action_ts(action.get("action_ts")))`; the Not helpful branch calls `build_feedback_modal(message_id, action.get("action_ts"))`. The submission branch does not change (the record already carries the time). No other bot logic changes; the in-memory dedup (`_first_interaction`, `_forget_interaction`) stays as a first, cheap filter.
- **E7: fallback is documented, not hidden.** When no valid `action_ts` exists, the row uses the current time and a Slack retry after an ambiguous commit can still store a second row. `docs/docs/services.md` ("Answer feedback") says so in one sentence, and says that a retry of one click with a valid `action_ts` stores one row.

## Test oracle for the acceptance criteria

The issue asks for "a fake store whose first `insert` commits and then raises `OperationalError`". A plain `mock.Mock()` store cannot show a duplicate row, so the bot-level tests use the REAL `SlackFeedbackStore` over a fake pool that models the primary key:

- A shared `rows` dict keyed by `(message_id, feedback_ts)`.
- Each fake cursor's `execute(sql, params)` raises `psycopg2.errors.UniqueViolation("duplicate key")` if `(params[0], params[1])` is in `rows`; else it stages the row.
- Each fake connection's `commit()` writes the staged row into `rows`. A connection flagged "ambiguous" writes the row and THEN raises `psycopg2.OperationalError("connection lost after commit")`. A connection flagged "down" raises `psycopg2.OperationalError` from `execute` and stores nothing.
- The pool hands out connections from a list in order.

Click scenario (issue AC 1): the bot gets a Helpful click with `action_ts` `"1700000000.123456"`, envelope `E1`. The pool gives one ambiguous connection, then five down connections, so `insert()` raises after storing one row; the bot logs the failure and forgets the key. The pool then gives working connections. The same payload arrives as envelope `E1-retry`. Result: `len(rows) == 1`, and `chat_postEphemeral` is called once (on the retry). Before this change the retry stores a second row with a new `now`, so `len(rows) == 2`: the test fails.

Submission scenario (issue AC 2): the same shape with a `view_submission` whose `private_metadata` is `"42:1700000000.123456"` and `view.id` `"V1"`.

Restart scenario: the same click goes to a NEW `SlackBot` (empty dedup) that shares the same `rows`. Result: one row.

## Risks / Trade-offs

- **Two ratings in the same microsecond collide.** Two users who click on one answer with the same `action_ts` (to the microsecond), or two forms opened from the same Not helpful click, map to one key; the second is treated as "already stored" and is lost (INFO log). This needs Slack to give two distinct interactions the same microsecond timestamp, which is far less likely than the duplicate this change removes. A per-user key needs a schema change and a human decision (issue constraint), so it is out of scope.
- **A `dislike` time is the click time.** The time between the Not helpful click and the submit (the time the form was open) no longer shows in `feedback_ts`. No current reader depends on it; the Grafana panels count rows.
- **A form opened before the deploy** has `private_metadata` `"42"` and falls back to the current time (E5). It still stores one row on the happy path.
- **Spec text in #638.** Its "Open the form" scenario says `private_metadata` is `"42"`. That scenario has no `action_ts` in its payload, so it stays true (E5). When both changes are archived, the archiver must merge the ADDED requirements of this change into the same `slack-answer-feedback` spec.

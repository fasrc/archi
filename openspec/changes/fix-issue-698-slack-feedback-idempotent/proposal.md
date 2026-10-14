## Why

One Slack feedback click or one form submission can store two `feedback` rows (issue #698, deferred from the round-2 Codex review of PR #693). On `origin/dev` `c2e885ca`:

- `SlackFeedbackStore.insert()` (`src/interfaces/slack_feedback.py:216-238`) sets `feedback_ts` to `datetime.now(timezone.utc)` once per call. The table key is `(message_id, feedback_ts)` (`src/cli/templates/init.sql:459-469`).
- The store retries only `OperationalError` and `InterfaceError`. After an ambiguous commit (the server committed, then the client lost the connection), the in-loop retry repeats the same `feedback_ts` and gets an `IntegrityError` from the key. The store does not catch it, so it raises, and `SlackBot.handle_interactive()` (`src/interfaces/slack_bot.py:409-444`) logs a failure for a row that is stored.
- After that failure the bot forgets the interaction key (`_forget_interaction()`), so a Slack envelope retry runs `insert()` again with a NEW `now`. The key does not stop it, and a second row is stored. A bot restart also clears the in-memory dedup, with the same result.

The `slack-answer-feedback` requirements promise one `like` row for one click and one `dislike` row for one submission.

## What Changes

- Derive `feedback_ts` from the Slack interaction, so every retry of one interaction repeats the same key:
  - A Helpful click uses the click's `actions[0].action_ts`.
  - A Not helpful click puts its `action_ts` into the modal's `private_metadata` as `<message_id>:<action_ts>`; the form submission parses both parts.
- `FeedbackRecord` gets an optional last field `feedback_ts`. The store uses it when it is set, else the old `now` value.
- `SlackFeedbackStore.insert()` treats `psycopg2.errors.UniqueViolation` as "already stored": it logs at INFO and returns. Other integrity errors (for example a foreign-key violation) still raise.
- A payload with no valid `action_ts` (or a modal that was opened before this change, with `private_metadata` `"42"`) falls back to the current time. That path is not idempotent; the docs say so.
- No schema change, no migration, no change to `src/interfaces/chat_app/app.py` or to `SQL_INSERT_FEEDBACK`.

Out of scope: a per-user column or any other `feedback` schema change (the issue requires a human decision for that), and the web chat feedback path.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None in `openspec/specs/`. The `slack-answer-feedback` capability is not archived yet (it lives in `openspec/changes/fix-issue-638-slack-feedback-buttons/`), so this change ADDS requirements to it and does not MODIFY archived text.

## Impact

- Code: `src/interfaces/slack_feedback.py`, `src/interfaces/slack_bot.py` (two call sites).
- Tests: `tests/unit/test_slack_feedback.py`, `tests/unit/test_slack_bot.py`.
- Docs: `docs/docs/services.md` ("Answer feedback").
- Data: a `dislike` row's `feedback_ts` becomes the time of the Not helpful click, not the time of the submit. A `like` row's `feedback_ts` is Slack's click time, not the bot's insert time.

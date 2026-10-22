## Why

Web chat users can rate an answer with like and dislike; Slack users cannot. The operator asked on 2026-10-08 for a way to collect feedback on Slack answer quality and chose buttons with a short form (issue #638). Two gaps block it on `origin/dev` `0ed50a23`:

- `SlackBot.handle_request()` drops every request where `req.type != "events_api"` (`src/interfaces/slack_bot.py:336`), so button clicks (`req.type == "interactive"`) are ignored.
- `_non_streaming_response()` (`src/interfaces/chat_app/openai_compat.py:458`) returns `id`, `object`, `created`, `model`, `choices`, and `usage`, but no message ID. The `final` event's `message_id` (`src/interfaces/chat_app/final_event.py:41`) does not reach the client, so the bot cannot name the stored answer that a click rates.

## What Changes

- `/v1/chat/completions` (non-streaming) adds a top-level `archi_message_id`: the integer `message_id` of the `final` event, or `null`. The streaming path does not change.
- `ask_archi()` returns the answer text and the `archi_message_id` together.
- After the answer is delivered, the bot posts one more thread message with a "Helpful" and a "Not helpful" button (design D1). The answer delivery does not change.
- The bot routes `interactive` Socket Mode requests to a feedback handler: "Helpful" stores one `like` row; "Not helpful" opens a form (three checkboxes and a comment); the form submission stores one `dislike` row.
- Each click adds a row in the existing `feedback` table. The bot never deletes earlier rows (design D4).
- New logic lives in a new tested module `src/interfaces/slack_feedback.py`. No change to `src/interfaces/chat_app/app.py`.
- Docs: the Slack app manifest turns on interactivity; `/v1` docs list `archi_message_id`; a note shows how to filter Slack ratings.

Out of scope: who rated an answer (the table has no user column), emoji-reaction feedback, and the streaming `/v1` path.

## Capabilities

### New Capabilities

- `slack-answer-feedback`: `/v1` returns the stored message ID, and Slack users rate each answer with buttons that write to the `feedback` table.

### Modified Capabilities

None. (The `slack-integration` spec is not archived yet; it lives in `openspec/changes/add-slack-service/`.)

## Impact

- Code: `src/interfaces/chat_app/openai_compat.py`, `src/interfaces/slack_bot.py`, new `src/interfaces/slack_feedback.py`.
- Tests: `tests/unit/test_openai_compat_endpoints.py`, `tests/unit/test_slack_bot.py`, new `tests/unit/test_slack_feedback.py`.
- Docs: `docs/docs/services.md` (Slack Interface), `docs/docs/api-reference-v1.md`.
- Operators must turn on **Interactivity & Shortcuts** in the Slack app (or re-apply the manifest). Without it, Slack does not send the clicks; answers still work.

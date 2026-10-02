## Context

The chat app already exposes an OpenAI-compatible API when
`services.chat_app.openai_compat.enabled` is true
(`src/interfaces/chat_app/openai_compat.py`). Facts this design relies on, read on
`origin/dev` @ `4cb1cca3`:

- `POST /v1/chat/completions` takes `{"model", "messages", "stream"}`. `model` must equal the
  deployment's config name or the call returns 404 (`openai_compat.py:229-238`).
- The last message is the question. The earlier messages become the history:
  `assistant` → an archi turn, `system` → dropped, any other role → a user turn
  (`openai_compat.py:240-241, 497-514`). The server uses this history and does not load
  earlier turns from the database for the call.
- The header `X-OpenWebUI-Chat-Id` (200 characters at most) maps a caller's thread to one
  persisted archi conversation (`openai_compat.py:254-264`).
- The non-streaming answer is the answer text plus a markdown source block,
  `\n\n---\n**Sources:**\n` followed by `` - `name` `` lines
  (`openai_compat.py:396-450`, `src/archi/utils/citation_formatter.py:79-80`).
- Bearer auth applies only when chat authentication is on (`openai_compat.py:79-100`).

`slack_sdk` 3.45.0 ships `slack_sdk.socket_mode.SocketModeClient(app_token, web_client=…,
auto_reconnect_enabled=True, concurrency=10)`. A listener receives `(client, req)` with
`req.type`, `req.envelope_id`, `req.payload`, and acknowledges with
`client.send_socket_mode_response(SocketModeResponse(envelope_id=…))`.

## Goals / Non-Goals

**Goals:**
- Answer `@archi` mentions in channels and direct messages, in the Slack thread.
- Keep one archi conversation for each Slack thread, with the earlier thread turns as context.
- Run without a GPU, model dependencies, or a public inbound URL.
- Small, pure seams that unit tests can cover without network access.

**Non-Goals:**
- Mapping each Slack user to an archi user or role. All Slack questions use one archi
  identity (none when chat authentication is off, `ARCHI_API_TOKEN` when it is on).
- Token streaming into Slack, slash commands, feedback buttons, or the Slack "AI app"
  assistant pane.
- Ingesting Slack messages as a document source.
- CLI wiring, Compose, Dockerfile, config template, and docs. PR 2 of this change does those.
- Fixing the Mattermost service.

## Decisions

### D1. Thin HTTP client to `/v1`, not an in-process pipeline
The bot calls `/v1/chat/completions`. **Alternative:** construct `archi()` and `DataManager`
in the bot, as `mattermost.py` and `piazza.py` do. Rejected: that copies conversation
storage and citation format, and it needs the model stack and a GPU Dockerfile. The `/v1`
path also reuses the chat app's RBAC, tracing, and conversation records.

### D2. `slack_sdk` built-in Socket Mode client, not Bolt or the HTTP Events API
**Alternatives:** `slack_bolt` (a framework on top of `slack_sdk`; one more dependency for
features this bot does not use), and the HTTP Events API (needs a public HTTPS URL; the FASRC
dev host is behind a firewall). The built-in client has no required dependencies.

### D3. Acknowledge first, then answer in a bounded worker pool
Slack wants an acknowledgement within 3 seconds, and an answer can take up to 600 seconds.
The listener sends the acknowledgement, filters and de-duplicates the event, and then
submits the work to a `ThreadPoolExecutor` (`services.slack.max_workers`, default 4). An
exception in a worker never reaches the socket listener.

### D4. One conversation per thread; the thread is the history
- Conversation key: `slack:{team_id}:{channel}:{thread_ts}`. A top-level message uses its own
  `ts` as `thread_ts`. This value is sent as `X-OpenWebUI-Chat-Id`.
- For a reply in an existing thread, the bot reads the thread with `conversations.replies`.
  It keeps only messages older than the current event, maps the bot's own messages to
  `assistant` and the others to `user`, removes `<@U…>` mentions, and drops empty text. It
  keeps the newest `services.slack.history_limit` turns (default 20).
- **Alternative:** send only the question and let the server load history by chat ID.
  Rejected: when `/v1` gets messages it uses them and ignores the stored history, and the
  Slack thread can also hold turns that other people wrote without a mention.

### D5. A placeholder message, then an in-place update
The bot posts `_Searching the archi knowledge base…_` in the thread at once, and then
replaces it with `chat.update` when the answer arrives. On failure, it replaces it with one
short error line. This needs only `chat:write`. The placeholder is newer than the event, so
D4's filter keeps it out of the history. **Alternative:** an emoji reaction. Rejected: it
needs one more scope (`reactions:write`).

### D6. De-duplicate by `(channel, ts)`
Slack can deliver one event again, and a mention in a direct message can arrive as both
`message` and `app_mention`. The bot keeps the last 1024 `(channel, ts)` pairs and answers a
pair only once.

### D7. Learn the model ID from `GET /v1/models` with a bounded retry
At startup the bot calls `/v1/models` until it succeeds (default 30 attempts, 10 seconds
apart) and uses the first model ID. This replaces the `time.sleep(30)` start-up hack of the
other integration services and proves the chat app is reachable before Slack connects. If
all attempts fail, the service exits with a non-zero status.

### D8. Minimal Markdown → Slack mrkdwn conversion
Outside code fences: `**x**` → `*x*`, `[t](u)` → `<u|t>`, a `#` heading line → a bold line.
Code fences pass through unchanged. The text is cut to 39,000 characters (Slack's limit for
`text` is 40,000) with a visible marker.

### D9. Secrets and configuration
- `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` are required; `ARCHI_API_TOKEN` is optional. All
  come from `read_secret` (`src/utils/env.py:4`). The bot never logs a token value.
- `services.slack` keys with in-code defaults: `chat_url` (`http://chatbot:7861`),
  `timeout_seconds` (600), `max_workers` (4), `history_limit` (20). PR 2 adds them to
  `base-config.yaml`.
- The bot's own user ID comes from `auth.test` at startup, never from a constant.
- `src/utils/logging.py` prints only `%(message)s`, so values go into the message text, not
  into `extra=`.

## Risks / Trade-offs

- [Every person in the workspace who can message the bot can query the knowledge base] →
  The Slack workspace is the access boundary. PR 2's docs say this, and the service is off
  unless an operator enables it.
- [One shared archi identity hides which Slack user asked] → The Slack user ID is in the
  bot's log line for each question. A per-user mapping is a later change.
- [An answer takes minutes] → The placeholder (D5) shows the question was received;
  `timeout_seconds` bounds the wait.
- [The Socket Mode connection drops] → `auto_reconnect_enabled=True` (the client default).
- [Thread history grows] → `history_limit` bounds the turns sent.
- [`/v1` is off on the deployment] → D7's startup check fails with a clear log line naming
  `services.chat_app.openai_compat.enabled`.

## Migration Plan

Nothing to migrate in PR 1: the module is not wired into any deployment. PR 2 adds the
optional service; a deployment turns it on with `--services chatbot,slack`. Rollback is to
remove `slack` from the service list and redeploy.

## Open Questions

- None that block PR 1. PR 2 needs the human Slack-app step from issue #510 before the live
  check.

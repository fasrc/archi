## Why

On 2026-10-14 the operator asked to see Slack bot traffic alone in Phoenix. The dev Phoenix (`archi.rc.fas.harvard.edu:3000`, version 20.9.0, project `default`) held 9 Slack turns, from 2026-10-10 to 2026-10-13. Nothing on those traces names Slack. The only way to find them was an inference: the span name is `POST /v1/chat/completions` and the user agent is `python-requests`. The Slack thread key (`X-OpenWebUI-Chat-Id: slack:<team>:<channel>:<thread_ts>`) went to Postgres, but it did not go into the trace. Phoenix has no custom dashboards, and the operator chose a Phoenix filter plus the Sessions tab.

## What Changes

- New module `src/interfaces/slack_trace_tags.py`. It turns a Slack thread key into the OpenInference attributes `session.id`, `tag.tags = ["slack"]`, and `metadata` (`source`, `slack_team`, `slack_channel`, `slack_thread_ts`).
- The Slack bot opens a `Slack question` CHAIN span around each `/v1` call. This span is the root of the trace, so Phoenix's Sessions tab groups each Slack thread. The question and answer text go on the span only when content capture is on.
- The chat app's `/v1` handler tags its request span, and it keeps the attributes active while the answer runs. The LangGraph, retriever, and model spans then carry them. This applies to the streaming and the non-streaming path.
- `docs/docs/observability.md` documents the attributes and the Phoenix filters.

## Capabilities

### New Capabilities

- `slack-phoenix-tags`: Slack bot traces carry a session ID, a `slack` tag, and Slack metadata.

### Modified Capabilities

None.

## Impact

- `src/interfaces/slack_trace_tags.py` (new), `src/interfaces/slack_bot.py`, `src/interfaces/chat_app/openai_compat.py`, `docs/docs/observability.md`.
- Tests: `tests/unit/test_slack_trace_tags.py` (new).
- No new dependency: `opentelemetry-sdk` and `openinference-instrumentation-langchain` are already pinned in `pyproject.toml`.

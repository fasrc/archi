## Context

A Slack turn on dev is one trace with this shape (trace `d3d5c425…`, measured 2026-10-14):

- root: the Slack bot's outbound `POST` span (requests instrumentation, Slack process);
- child: the chat app's Flask span `POST /v1/chat/completions`;
- grandchildren: `LangGraph` (CHAIN), the model call, and other outbound calls.

## Decisions

### D1. Tag on both sides

Phoenix's Sessions view groups traces by the root span's `session.id`, and the root is in the bot process. So the bot opens its own `Slack question` CHAIN span around `ask_archi()`, with the attributes, and that span becomes the root. The chat app tags its spans too, so a span filter such as `metadata['source'] == 'slack'` also finds the LangGraph and model spans.

### D2. Key on the thread key, not on a new header

The chat app derives the attributes from `X-OpenWebUI-Chat-Id` when it starts with `slack:`. The bot already sends it (`thread_key()`), so the change needs no new header and no change to the bot's request. Any caller can send such a key. The tag is for observation, not for access control.

### D3. Attach the attributes for one step at a time

The spans of an answer start while the response pulls the next event from `ChatWrapper.stream()`. A plain `with using_attributes(...)` around a generator would stay attached across each `yield`. A consumer that stops early would then detach in another context. `with_trace_attributes()` attaches the attributes for one `next()` call and detaches before it yields. The worker thread of the agent copies the context during the first step, so the agent's spans get the attributes.

### D4. Content follows the capture switch

The bot's root span records the question and the answer only when `capture_content_enabled()` is true, the same switch that the rest of the tracing obeys.

### D5. No dashboard code

Phoenix 20.9 has charts for each project, filters, and a Sessions view, but no saved dashboards. The operator chose a filter and the Sessions tab, so this change adds no report job.

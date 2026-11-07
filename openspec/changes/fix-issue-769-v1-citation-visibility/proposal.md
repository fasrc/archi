## Why

The OpenAI-compatible endpoint `/v1/chat/completions` ignores
`data_manager.sources.<name>.visible: false` (issue #769). The native chat obeys the flag:
`ChatWrapper.get_top_sources` (`src/interfaces/chat_app/app.py:678`) drops a document when
`_get_doc_visibility` (`:819`, a thin call into `is_visible` in
`src/interfaces/chat_app/source_visibility.py`, PR #767) returns false.

The `/v1` path does not. `ChatWrapper.stream` puts the unfiltered retriever documents and
scores into the `final` event (`raw_source_documents`, `app.py:2638`, passed at `:2713`).
`src/interfaces/chat_app/openai_compat.py` gives them straight to `format_citations` in both
modes:

- streaming: `_streaming_response`, `openai_compat.py:408-414`;
- non-streaming: `_non_streaming_response`, `openai_compat.py:500-504` and `:519-522`.

`format_citations` (`src/archi/utils/citation_formatter.py:11`) does no visibility check. So a
Jira ticket with `jira.visible: false` is hidden in the native chat but is cited by every
`/v1` client, which includes the Slack bot (`src/interfaces/slack_bot.py:1`, it answers
through `/v1`).

Measured on `origin/dev` `5f3d80e9` (2026-11-05) with the tests of this change: a `final`
event with a hidden Jira document (score `0.9`) and a visible `git` document (score `0.4`)
gives a `/v1` citation block that lists both, in streaming and in non-streaming mode.

## What Changes

- A new pure function `visible_sources(documents, scores, sources_config)` in
  `src/interfaces/chat_app/source_visibility.py` drops each document that `is_visible`
  hides, together with its own score (design D1). A `sources_config` that is not a dict means
  no filter.
- A new helper `_format_visible_citations(source_documents, source_scores)` in
  `openai_compat.py` reads `sources_config` from the chat wrapper with `getattr`, calls
  `visible_sources`, then calls `format_citations` (design D2). Both response paths call it
  in place of their direct `format_citations` call.
- `docs/docs/configuration.md` (the `visible` paragraph and the one-line note) and
  `docs/docs/api-reference-v1.md` ("Source Citations") say that the flag also applies to
  `/v1` citations, and that it does not remove a document from retrieval.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `source-citations`: adds one requirement. A `/v1` citation block leaves out each document
  whose source has `visible: false`, with its own score, in streaming and non-streaming
  mode. It uses `## ADDED Requirements` because the #459 requirement ("Source visibility
  applies to every configured source") is not archived into `openspec/specs/` yet.

## Impact

- Code: `src/interfaces/chat_app/source_visibility.py` (one new function),
  `src/interfaces/chat_app/openai_compat.py` (one import, one new helper, two call sites).
  `src/interfaces/chat_app/app.py` and `src/archi/utils/citation_formatter.py` do not change.
- Tests: `tests/unit/test_source_visibility.py` (6 new test functions, 9 test items) and
  `tests/unit/test_openai_compat_endpoints.py` (one new class with 3 test functions, 6 test
  items, and 4 new module helpers). No existing test changes.
- Docs: `docs/docs/configuration.md` and `docs/docs/api-reference-v1.md`.
- Clients: a `/v1` client (the Slack bot too) stops seeing hidden sources in the appended
  citation block. A deployment that sets no `visible: false` sees no change.
- Non-goal: the model's own answer text. The retriever does not read `visible`, so the model
  still reads a hidden document and can still name or link it in its answer. The native chat
  has the same property. This change filters the appended citation block only.

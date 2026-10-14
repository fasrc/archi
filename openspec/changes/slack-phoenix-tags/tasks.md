## 1. Tags

- [x] 1.1 In `tests/unit/test_slack_trace_tags.py`, add failing tests for `slack_trace_attributes`, `with_trace_attributes`, `tag_current_span`, and the `/v1` handler on both paths. Implement `src/interfaces/slack_trace_tags.py` and the call sites in `openai_compat.py` (design D2, D3).
- [x] 1.2 Add failing tests for `slack_question_span` and its use in `SlackBot.answer()`. Implement them (design D1, D4).
- [x] 1.3 Document the attributes and the Phoenix filters in `docs/docs/observability.md`.

## 2. Check on dev (operator)

- [ ] 2.1 Redeploy dev, ask the bot a question in a thread, then ask a follow-up.
- [ ] 2.2 In Phoenix, check that `metadata['source'] == 'slack'` finds both turns, and that the Sessions tab shows one session with two traces.

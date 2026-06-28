## 1. Failing test (TDD red)

- [x] 1.1 Add `tests/unit/test_message_content.py` calling `flatten_message_content` with an Anthropic-style content-block list `[{"type": "text", "text": "..."}]` and asserting the result is the bare text with no `{'text'` / `{'type': 'text'` substring. Confirmed it FAILS (module missing).
- [x] 1.2 Add the remaining cases — multiple text blocks → space-joined; plain string unchanged; mixed text + non-text (`tool_use`) → text only, no dict literal, no exception; bare string parts pass through; all-non-text → empty string. Plus mixin cases (flattens, plain-string passthrough, missing `.content` → empty) and a `FASRCDocsAgent` wiring assertion in `tests/unit/test_fasrc_docs_agent.py`. Confirmed RED (ImportError: `MessageContentMixin`).

## 2. Fix (TDD green)

- [x] 2.1 Add `src/archi/pipelines/agents/message_content.py` with `flatten_message_content(content)` (extract `part["text"]` for dict blocks, pass through `str` parts, skip the rest, join with one space; non-list → `str(content)`) and `MessageContentMixin` whose `_message_content` delegates to it. `base_react.py` left untouched (format-churn gate — see design.md).
- [x] 2.2 Wire `FASRCDocsAgent(MessageContentMixin, BaseReActAgent)` (import + class declaration in the black-clean `fasrc_docs_agent.py`). Confirmed the new + wiring tests PASS (21 passed) with no regression in existing base_react/agent tests.

## 3. Gate & hygiene

- [x] 3.1 `bash scripts/gate.sh` green — 578 passed, patch coverage 100% on the diff (`fasrc_docs_agent.py` 2 lines + fully-covered new module); black left all files unchanged (no churn).
- [x] 3.2 No new dependency: `git diff origin/dev -- pyproject.toml requirements/requirements-base.txt` is empty.
- [x] 3.3 pyright clean on changed files (new module/test 0 errors; the 2 `fasrc_docs_agent.py` errors are pre-existing on `origin/dev`, unrelated to this change).

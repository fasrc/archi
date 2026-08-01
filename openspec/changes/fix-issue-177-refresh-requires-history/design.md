## Context

`_prepare_chat_context` (`src/interfaces/chat_app/app.py:1618`) resolves the conversation history a
request will be answered against. It has three source branches (`:1635-1648`):

| Branch | Condition | History comes from |
|---|---|---|
| external | `external_history is not None` | the caller's supplied list (a conversation row is created if `conversation_id` is None) |
| new | `conversation_id is None` | `[]` — a fresh conversation is created |
| existing | otherwise | `query_conversation_history(...)` |

Then two refresh-dependent steps run:

```python
1650        if is_refresh:
1651            while history and history[-1][0] == ARCHI_SENDER:
1652                _ = history.pop(-1)
...
1657        if not is_refresh:
1658            history = history + [(sender, content)]
```

The **new** branch combined with `is_refresh` is the defect: history is `[]`, the trim is a no-op,
the append is skipped, and the pipeline receives no turns at all.

## Goals / Non-Goals

**Goals**
- Make an unsatisfiable refresh fail explicitly instead of answering an empty prompt.
- Reject before the conversation row is created, so a rejected request leaves no trace.
- Report `400` with a message that identifies it as a client error on both endpoints.

**Non-Goals**
- No change to a refresh that has prior turns to work with, from either source.
- No change to the `is_refresh` trim semantics (`:1650-1652`).
- No change to the unguarded timeout check at `:1654` — that is
  [#175](https://github.com/fasrc/archi/issues/175) and is deliberately untouched here.

## Decisions

**Decision: the condition is "no source of prior turns", not "no conversation_id".**

This is the correction that shaped the change. [#177](https://github.com/fasrc/archi/issues/177)
proposed rejecting `is_refresh` without a `conversation_id`. That is too broad: the **external**
branch supplies history directly, so a refresh with `external_history` and no `conversation_id` is
coherent — the supplied turns are trimmed of trailing assistant messages and re-answered. Rejecting
it would break a legitimate use of the API to fix a different bug.

The guard is therefore:

```python
if is_refresh and conversation_id is None and external_history is None:
    return None, 400
```

`openai_compat.py:280` is the only in-repo caller that passes `external_history`, and it always
sends `is_refresh: False` — so nothing exercises the coherent case today. It remains supported
because the API allows it and it has a well-defined meaning, not because a caller depends on it.

- *Alternative — treat the request as a non-refresh* (append the message and answer normally):
  rejected. It silently reinterprets what the caller asked for. That is the same class of behaviour
  as the two-character-sender bug documented in `api_reference.md`, where a malformed request
  succeeds against the wrong content. A request that cannot be honoured should say so.
- *Alternative — return 200 with an empty answer:* rejected; indistinguishable from a real answer.

**Decision: place the guard before the branch table, not inside the `new` branch.**

It sits immediately after `sender, content = tuple(message[0])` (`:1633`) and before
`if external_history is not None:` (`:1635`). Placing it inside the `new` branch would work but
would run *after* `create_conversation` in the external branch's `conversation_id is None` path,
and reads as a special case rather than a precondition. A precondition on the whole function is what
it is.

**Decision: collapse the two error-message chains into a shared helper.**

`app.py:2019-2025` (streaming) and `:4668-4674` (non-streaming route) contain the same
`408 / 403 / else` mapping, duplicated. Adding a fourth status to both would deepen a duplication
that already invites drift — and drift here is invisible, because each endpoint is exercised
separately.

A module-level `_chat_error_message(error_code)` gives one place to add a status, is a pure function
that unit-tests directly, and makes both call sites shorter than they were. The alternative —
adding an `elif error_code == 400:` to each chain — is two more places to forget next time.

## Risks / Trade-offs

- **[A caller depends on the current behaviour]** → Very unlikely: the current behaviour is an answer
  to an empty prompt. No in-repo client sends the combination. The change is nonetheless a `200` →
  `400` transition on a live endpoint and is called out in the proposal's Impact section.
- **[The route's error branch is not reached by tests]** → The `get_chat_response` route body is not
  exercised by any unit test, so the helper call site there lands uncovered. Mitigation: the helper
  itself is pure and fully unit-tested, and the streaming call site *is* covered, so the untested
  surface is one line rather than a branch chain. Net changed-line coverage is what the gate
  measures, and the guard plus helper tests carry it.
- **[The guard could mask a future legitimate refresh source]** → If a third history source is added,
  the guard must learn about it. Mitigation: the spec states the condition as "no source of prior
  turns" rather than naming two fields, and the scenario for the `external_history` case exists
  precisely so that a narrowing of the guard fails a test.

## Migration Plan

None required. No data, config, or deploy change. Rollback is reverting the commit; the guard is
additive and the helper is a refactor of existing behaviour for every status except the new one.

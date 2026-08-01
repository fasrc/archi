## Context

`_prepare_chat_context` (`src/interfaces/chat_app/app.py:1622`) resolves the conversation history a
request will be answered against. It has three source branches (`:1639-1652`):

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
- No change to the `is_refresh` trim semantics (`:1654-1656`).
- No change to the unguarded timeout check at `:1658` — that is
  [#175](https://github.com/fasrc/archi/issues/175) and is deliberately untouched here.

## Decisions

**Decision: test the resolved history, not which fields were supplied.**

The guard is:

```python
if is_refresh:
    while history and history[-1][0] == ARCHI_SENDER:
        history.pop(-1)
    if not history:
        return None, 400
```

This arrived in two corrections, both worth recording because each looked finished.

[#177](https://github.com/fasrc/archi/issues/177) proposed rejecting `is_refresh` without a
`conversation_id`. Too broad: the **external** branch supplies turns directly, so a refresh over
`external_history` with no `conversation_id` is coherent and must not be rejected.

The obvious repair — `conversation_id is None and external_history is None` — is still wrong, and
adversarial review caught it. Testing which *fields* were supplied is a **proxy** for "prior turns
exist", and the proxy admits three requests that reach the identical unsatisfiable state:

| Request | Why the proxy misses it |
|---|---|
| `external_history=[]` | an empty list is not `None` |
| history of assistant turns only | non-empty on arrival; the trim empties it |
| `conversation_id` naming a conversation with no turns | reaches the state through the third branch, which the proxy never considered |

All three were reproduced against `dev`. Only the resolved, post-trim history distinguishes them,
and testing it collapses four routes into one condition instead of accumulating special cases.

The lesson generalizes past this function: when a guard is written in terms of inputs but the defect
is defined in terms of a computed state, the guard is a proxy and will have holes. The delta spec
already said "no source of prior turns" while the first implementation tested field presence — the
spec was right and the code quietly substituted something weaker and easier to check.

- *Alternative — treat the request as a non-refresh* (append the message and answer normally):
  rejected. It silently reinterprets what the caller asked for. That is the same class of behaviour
  as the two-character-sender bug documented in `api_reference.md`, where a malformed request
  succeeds against the wrong content. A request that cannot be honoured should say so.
- *Alternative — return 200 with an empty answer:* rejected; indistinguishable from a real answer.

**Decision: resolve history without side effects, and commit the writes afterwards.**

Because the check now runs *after* history resolution, resolution must not write anything, or a
refused request would still leave a conversation row behind — which the `external_history=[]` path
did in the first implementation. So resolution is pure, and `create_conversation` /
`update_conversation_timestamp` run only once the request is known to be serviceable.

Two consequences worth stating. `external_history` is **copied** rather than aliased, because the
trim pops from the resolved list and mutating the caller's argument is not this function's to do.
And the branch that performs the writes has to reproduce the original pairing exactly — create when
there is no `conversation_id`, touch the timestamp only for an existing conversation that did not
supply its own history — so three tests pin those side effects rather than trusting the rewrite.

**Decision: collapse the two error-message chains into a shared helper.**

`app.py:2023-2029` (streaming) and `:4672-4678` (non-streaming route) contain the same
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
- **[The route bodies are not reached by unit tests]** → *Resolved during review.* This was
  originally accepted as a known gap, leaving the non-streaming route's helper call uncovered.
  Review pushed back on validating a status-code change only through stubs, which was right: both
  view functions are now registered on a Flask app and driven with `test_client()`, asserting the
  real `400` from `POST /api/get_chat_response` and the `200`-with-in-band-error from the streaming
  endpoint. Changed-line coverage is 100% as a result. What remains unverified is behaviour against
  a *deployed* service; that needs a redeploy of a shared environment for an unmerged branch, which
  is a human's call, and the in-process route tests cover the status-code and channel claims that
  the stubs could not.
- **[The guard could mask a future legitimate refresh source]** → If a third history source is added,
  the guard must learn about it. Mitigation: the guard tests the resolved history, so a new source
  is covered automatically as long as it flows into `history` before the trim; the spec states the
  condition in those terms rather than naming two fields, and the scenario for the `external_history` case exists
  precisely so that a narrowing of the guard fails a test.

## Migration Plan

None required. No data, config, or deploy change. Rollback is reverting the commit; the guard is
additive and the helper is a refactor of existing behaviour for every status except the new one.

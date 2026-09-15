# Design — reject booleans and non-finite numbers in the chat timing fields

## The function as it stands

`src/interfaces/chat_app/request_validation.py:21-31`, on `origin/dev@3040e608`:

```python
def _milliseconds_to_seconds(value: Any, field: str) -> float:
    if not value:
        # Falsey means "not supplied" -- the documented optional case, not a bad one.
        return 0
    try:
        return value / 1000
    except _MS_ERRORS as exc:          # (OverflowError, TypeError)
        raise InvalidClientTiming(...) from exc
```

Two escape routes through it, both measured rather than reasoned about:

| input | falsey guard | division | today's result |
|---|---|---|---|
| `True` | truthy, passes | `True / 1000` → `0.001` | accepted as 1 ms |
| `False` | **falsey, returns `0`** | never reached | accepted as "omitted" |
| `inf` / `-inf` | truthy, passes | `inf / 1000` → `inf` | accepted verbatim |
| `nan` | truthy, passes | `nan / 1000` → `nan` | accepted verbatim |

## Decision 1 — the boolean check goes *before* the falsey guard

`False` is falsey. A type check placed anywhere after `if not value: return 0` therefore
never sees it, and `"client_timeout": false` would keep being silently accepted as "field
omitted" while `true` was rejected — a half-fix that is harder to reason about than the
original bug, because the two booleans would then behave differently for no stated reason.

So the check is the first statement in the function. This is the one ordering constraint in
the change, and the issue calls it out explicitly.

**Why a type check rather than making the falsey guard stricter.** The falsey guard is
load-bearing and specified: `fix-issue-175-optional-client-timeout` established that *any*
falsey value — `null`, `0`, `false`, `""`, `[]`, `{}` — means "not supplied". Narrowing that
guard to, say, `value is None or value == 0` would change the contract for `""`, `[]` and
`{}` as collateral damage. An explicit `isinstance(value, bool)` check ahead of it removes
exactly the two values in question and leaves the rest of the rule intact.

## Decision 2 — the non-finite check goes *after* the division

`math.isfinite` raises `TypeError` on a string, a list or a dict. Placed before the
division it would therefore need its own guard, duplicating what the division's `except`
clause already does. Placed *after* the division, everything that reaches it has already
survived `value / 1000`, which means it is a real number: a non-numeric value raised
`TypeError` and an over-large integer raised `OverflowError`, both already handled.

Ordering is also safe with respect to the falsey guard: `inf`, `-inf` and `nan` are all
truthy, so none of them is shadowed by it and no second check is needed up front.

`math.isfinite` is the right predicate rather than `math.isnan`, because it rejects the
infinities too, and both are broken in the same way — a comparison against `inf` or `NaN`
never behaves as a deadline. The repo already uses exactly this predicate for exactly this
reason at `tests/smoke/ragas_smoke.py:129`.

## Decision 3 — one exception type, two messages

Both rejections raise the existing `InvalidClientTiming`, which both route handlers already
translate into a 400 whose body names the field. That is why **no handler or route code
changes**: the new refusals travel the path the string/array/object refusals already travel.

Two distinct messages, though, because the two causes are distinguishable and a caller can
act on the difference: a boolean means "you put a flag in a number field", a non-finite
literal means "your serializer emitted `NaN`/`Infinity`". Both name the field, matching the
existing wording.

## Decision 4 — scope stops at booleans and non-finite values

The docs row at `docs/docs/api_reference.md:36` lists three badly-behaved inputs together:
a negative number, `true`, and infinity/`NaN`. Only the last two are this change's.

- **Negative `client_sent_msg_ts`** — legitimately an ordinary pre-1970 timestamp inside
  years 1–9999. The docs already say so. Rejecting it would be a regression.
- **Negative `client_timeout`** — expires the deadline immediately, which is arguably a bug,
  but rejecting it is a separate contract decision: it is in neither the issue's title, its
  Objective, nor its acceptance criteria. Folding it in would put a decision nobody made
  into a change whose tests cannot justify it. Leave it; file it separately if wanted.

## Consequence worth stating: one existing behaviour is refused earlier

A non-finite `client_sent_msg_ts` is *already* a 400 today, via `datetime.fromtimestamp`
in `parse_client_sent_msg_ts` raising `ValueError`/`OverflowError` — the message reads
"client_sent_msg_ts is not a representable time". After this change the non-finite check in
`_milliseconds_to_seconds` catches it one step earlier with the type-oriented message.

Same status, same field named, different text. The existing tests in
`TestAnUnrepresentableTimestampIsRefusedNotCrashed` are unaffected: their `BAD` values are
all *finite* (`-1e20`, the year-0 and year-10000 edges, `10**30`), so they still take the
`fromtimestamp` path, and they assert only on status and field name regardless.

## Verification shape

The existing `TestNormalizationItselfCannotRaise` in
`tests/unit/test_chat_timing_field_validation.py` is the right home: its `CASES` list is
parametrized `(field, value)` over both fields and its `_post` harness drives **both** routes
through the real `_parse_chat_request` with a real JSON body. Adding the four boolean cases
and the non-finite cases to `CASES` gets route-level coverage of both endpoints for free.

`nan` needs care in one respect only: `assert nan == nan` is `False`, so any assertion about
an accepted `nan` would be vacuous. Since every `nan` case here is a *rejection*, the
assertions are about status and message and the trap does not arise — but do not "fix" a
passing test by comparing `nan` to anything.

The discriminating negative test is the one that must stay green: a real number is still
accepted (`parse_client_timeout(600000) == 600.0`), and an omitted field still yields `0`
(`parse_client_timeout(None) == 0`). Rejecting `false` must not break omission — that is the
single most likely way to get this wrong.

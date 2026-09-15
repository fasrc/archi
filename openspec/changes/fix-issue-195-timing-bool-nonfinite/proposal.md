## Why

`_milliseconds_to_seconds` in `src/interfaces/chat_app/request_validation.py` guards the
millisecond→second division against the two ways it can raise on well-formed JSON
(`OverflowError`, `TypeError`). Two kinds of value divide perfectly well and so are never
seen by that guard:

1. **A JSON boolean.** `bool` is a subclass of `int` in Python, so `True / 1000 == 0.001`
   and `False` is caught earlier by the falsey guard. Measured on `origin/dev@3040e608`:

   | payload | result | consequence |
   |---|---|---|
   | `"client_timeout": true` | `0.001` | a **1 ms deadline** — any request that also supplies `client_sent_msg_ts` is past it on arrival, so the non-streaming route returns 408 and the streaming route ends the stream with an in-band 408 almost immediately |
   | `"client_timeout": false` | `0` | silently identical to omitting the field |
   | `"client_sent_msg_ts": true` | `0.001` | persisted as `1970-01-01T00:00:00.001Z` — indistinguishable at a glance from a real measurement, and *not* the documented `1970-01-01T00:00:00Z` absent-value sentinel |
   | `"client_sent_msg_ts": false` | `0` | silently identical to omitting the field |

2. **A non-finite literal.** Python's `json` module accepts the bare `NaN`, `Infinity` and
   `-Infinity` tokens by default, which is what Flask's JSON provider uses — verified:
   `json.loads('{"a": NaN, "b": Infinity}')` returns `{'a': nan, 'b': inf}`. `inf / 1000` is
   `inf` and `nan / 1000` is `nan`, so both survive. Measured on the same tip,
   `parse_client_timeout` returns `inf`, `-inf` and `nan` verbatim. Every comparison against
   `NaN` is `False`, so a `NaN` timeout **disables the deadline outright** while looking like
   a deadline was declared.

`true` is the harmful case: a client that serializes a flag into the wrong field gets 408 on
every request, with nothing in the response indicating that the *field* was the problem.

The published contract already documents this hole and points at this issue —
`docs/docs/api_reference.md:35` ("A JSON boolean slips through this rule as a usable number
instead of being refused") and `:36` ("`true` becomes a **1 ms** deadline … a literal that
decodes to infinity or `NaN` disables the deadline outright. That is issue #195"). Closing
the hole is what lets those two rows simply list the rejected types.

**Not a regression.** This behaviour predates #175/#185; those changes added the guards that
made every *other* non-numeric type a 400 and left these two gaps. Found by adversarial
review of the #194 docs PR, which had claimed "anything not numeric is rejected"; that claim
was narrowed to the types actually rejected, so the docs are currently accurate about a
contract with a hole in it.

## What Changes

- Reject a `bool` supplied for `client_sent_msg_ts` or `client_timeout` with **HTTP 400**,
  in `_milliseconds_to_seconds`, **before** the falsey guard. The ordering is the whole
  subtlety: `if not value: return 0` runs first today, so `False` never reaches the
  division and a type check placed after that guard would miss `false` entirely.
- Reject a non-finite value (`inf`, `-inf`, `NaN`) for either field with **HTTP 400**,
  checked **after** the division — by which point a non-numeric value has already raised
  `TypeError`, so `math.isfinite` is only ever handed a real number.
- Both rejections raise `InvalidClientTiming` naming the offending field, matching the
  wording of the existing message, so both routes return the 400 they already return for
  strings, arrays and objects. No route or handler code changes.
- Update both timing-field rows in `docs/docs/api_reference.md` to list a boolean and a
  non-finite literal among the rejected values, and retire the two `issue #195` pointers.
- **Explicitly out of scope:** negative and fractional values. A negative
  `client_sent_msg_ts` inside years 1–9999 is an ordinary pre-1970 timestamp and the docs
  already say so; a negative `client_timeout` expiring immediately is arguably wrong but is
  a separate contract judgment, named in neither this issue's title nor its acceptance
  criteria. Do not fold it in.

## Capabilities

### New Capabilities
- `chat-api-request-contract`: what the chat endpoints require of a request body, and which
  values of the optional timing fields they refuse. Not yet present in `openspec/specs/`;
  the unarchived changes `fix-issue-138-chat-docstring-payload-shape` and
  `fix-issue-175-optional-client-timeout` each carry an `ADDED` delta for the same
  capability (see Impact).

### Modified Capabilities
<!-- None. No capability in openspec/specs/ changes; the same-capability deltas in the two
     unarchived changes named above are a cross-change ordering concern at archive time,
     not a requirement modification here. -->

## Impact

**Code**
- `src/interfaces/chat_app/request_validation.py` — `_milliseconds_to_seconds` only. A small,
  black-clean module of pure functions, so an in-place edit carries none of the reflow risk
  that `app.py` does. Roughly six added lines plus an `import math`.

**Tests**
- `tests/unit/test_chat_timing_field_validation.py` — extends the existing
  `TestNormalizationItselfCannotRaise` class, whose `CASES` list and `_post` harness already
  drive both routes with a real JSON body through the real `_parse_chat_request`.

**Docs**
- `docs/docs/api_reference.md` — the `client_sent_msg_ts` and `client_timeout` rows of the
  chat request-body table (lines 35 and 36). **No `[name]: …/app.py#Lnnn` anchor definition
  may be renumbered** — that is issue #190, and this change does not touch `app.py`, so
  every anchor stays valid. The edits are confined to prose inside the two table cells.

**APIs** — `POST /api/get_chat_response` and `POST /api/get_chat_response_stream` reject four
request shapes they previously accepted (a boolean or a non-finite literal in either timing
field). Technically breaking, but no in-repo caller is affected: the JS clients send
`Date.now()` and a numeric constant (`static/chat.js:269-270`,
`static/script.js:782-783,827-828`) and `openai_compat.py:274` synthesizes
`now.timestamp()`. A caller sending `true` today is already receiving 408 on every request,
so it is being converted from a misleading rejection into an accurate one.

**Behaviour change worth calling out:** a non-finite `client_sent_msg_ts` is *already*
rejected with 400 today, by the `datetime.fromtimestamp` representable-time check in
`parse_client_sent_msg_ts`. This change refuses it one step earlier with a different
message. The status is unchanged and the existing tests assert only the status and the field
name, both of which still hold — but the message text changes, so anything asserting on it
must be re-read rather than assumed.

**Cross-change** — whoever archives `fix-issue-138`, `fix-issue-175` and this change must
reconcile three `ADDED` deltas for one capability into a single merged
`openspec/specs/chat-api-request-contract/spec.md`. Nothing here contradicts either of the
others: #175 established that falsey means "not supplied" and this change preserves that
exactly, narrowing only the set of *truthy* values that are accepted.

## Why

`/api/get_chat_response` and `/api/get_chat_response_stream` return HTTP 408 to any caller
that omits `client_sent_msg_ts` or `client_timeout`, because both fields are coerced to `0`
and then compared with an unguarded `server_received_msg_ts.timestamp() - 0 > 0`, which is
true on every request (`app.py:4652-4655`, `app.py:1710`). The streaming loop asks the same
question with a guard — `if client_timeout and ...` (`app.py:2156`) — so two sites in one
file disagree about what `client_timeout == 0` means, and the unguarded one runs first.
Every in-repo client sends both fields, so the bug is invisible in normal operation and
bites only a new integrator building from the published contract (issue #175, found by a
Codex P1 review on PR #159).

## What Changes

- Guard the timeout check in `ChatWrapper._prepare_chat_context` so an absent client
  deadline means "no deadline" instead of "already expired". Both a `client_timeout` of `0`
  and a `client_sent_msg_ts` of `0` disable the check — a timeout with no send time has no
  meaningful baseline to measure from.
- Preserve the real timeout: an explicitly-sent `client_timeout` that the server genuinely
  exceeds still returns 408.
- Add the first unit tests to reach `_prepare_chat_context`'s body. The module sits at ~14%
  line coverage because importing it registers signatures as covered while no test executes
  the handler path; new executable lines there land uncovered and sink the ≥80% diff-coverage
  gate unless the change brings its own tests.
- Add a cross-reference comment at each of the two timeout sites pointing at the other, so
  they cannot silently drift apart again.
- Simplify `docs/docs/api_reference.md`: return both fields to optional in the request-body
  table, drop the "required in practice" warning added by PR #159, and keep the published
  example a request that still succeeds.
- **Explicitly out of scope:** changing `_parse_chat_request`'s `if x else 0` coercion to
  `None`. See Impact — it would break two downstream consumers.

## Capabilities

### New Capabilities
- `chat-api-request-contract`: what the chat endpoints require of a request body, and what
  they do when the optional timing fields are absent. Not yet present in `openspec/specs/`;
  change `fix-issue-138-chat-docstring-payload-shape` also carries a delta for it (see
  Impact).

### Modified Capabilities
<!-- None. No capability in openspec/specs/ changes; see the note above on the unarchived
     fix-issue-138 delta, which is a cross-change ordering concern rather than a
     requirement modification here. -->

## Impact

**Code**
- `src/interfaces/chat_app/app.py` — `ChatWrapper._prepare_chat_context` (the guard at
  `:1710`) and a cross-reference comment at the streaming check (`:2156`). A large file:
  an in-place edit risks a black reflow of unrelated code, which has previously produced
  ~17% diff coverage from a one-line change.
- `tests/unit/` — a new test module exercising the guard.

**Docs**
- `docs/docs/api_reference.md` — request-body table, the timing-fields warning, and the
  example.

**APIs** — `POST /api/get_chat_response` and `POST /api/get_chat_response_stream` accept
request bodies they previously rejected. No currently-accepted request changes behaviour,
so this is not breaking.

**Deliberately untouched:** `_parse_chat_request`'s coercion of absent timing fields to `0`
(`app.py:4654-4655`). Issue #175 floats replacing it with `None` to distinguish "absent"
from "zero". `client_sent_msg_ts` is consumed by
`datetime.fromtimestamp(client_sent_msg_ts, tz=timezone.utc)` at `app.py:2532` and
`app.py:4741`, whose result is written to a timing row via `insert_timing`; `None` there
raises `TypeError` on every request that omits the field, converting a wrong status code
into a hard 500. The `0` sentinel stays, and the guard reads it the way `app.py:2156`
already does.

**Cross-change** — unarchived change `fix-issue-138-chat-docstring-payload-shape` carries a
`chat-api-request-contract` delta requiring the API reference to mark both timing fields
"required in practice **for as long as #175 is open**". That clause is self-limiting, so
this change retires it rather than contradicting it, but whoever archives the two changes
must reconcile them in one merged capability.

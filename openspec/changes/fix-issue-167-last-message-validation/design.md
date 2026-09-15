## Context

Both chat endpoints funnel their payload through one parser and one consumer:

- `FlaskAppWrapper._parse_chat_request` (`src/interfaces/chat_app/app.py:4590`) reads
  `payload.get("last_message")` and returns it as `request_data["message"]`
  (`app.py:4606`). No shape check.
- `ChatWrapper._prepare_chat_context` (`app.py:1618`) consumes it at `app.py:1633`:
  `sender, content = tuple(message[0])`.

`tuple(message[0])` unpacks whatever the first element is. A flat `last_message` therefore
makes the first element a **string**, and the unpack consumes its characters:

| Input | `message[0]` | Today |
|---|---|---|
| `[["User", "hello"]]` | `["User", "hello"]` | correct |
| `["User", "hello"]` | `"User"` | 4 items → `ValueError` → HTTP 500 |
| `["AI", "hello"]` | `"AI"` | 2 items → `sender="A"`, `content="I"` → **HTTP 200, wrong content** |

Row three is the reason this change exists. Nothing distinguishes it from a successful
request — not the status, not the body shape, not the logs.

Two constraints shape the implementation more than the logic does:

1. **The diff-coverage gate.** `scripts/gate.sh:146` runs pytest with `--cov=src` and then
   `diff-cover coverage.xml --compare-branch=origin/dev --fail-under=80`. Only files under
   `src/` appear in `coverage.xml`, so **new test files contribute nothing to the
   denominator** — the ratio is computed purely over the changed lines in `src/`. The chat
   request path has no test coverage today: four unit tests import `app.py`
   (`test_chat_override_concurrency.py`, `test_chat_override_persistence.py`,
   `test_provider_config_override.py`, `test_request_local_pipeline.py`), but none enters
   `get_chat_response` or `_prepare_chat_context`. Uncovered lines added to `app.py` come
   straight off the numerator.
2. **`app.py` is already black-clean** (verified: `black --check` reports "1 file would be
   left unchanged"). An in-place edit will not trigger a whole-file reflow, so there is no
   black-churn risk here and the diff stays as small as the edit itself.

## Goals / Non-Goals

**Goals:**

- Reject a malformed `last_message` with HTTP 400 and a message naming the expected shape,
  on both `POST /api/get_chat_response` and `POST /api/get_chat_response_stream`.
- Keep the accepted shape exactly what it is today — `[["User", "hello"]]` and the tuple
  form `[("user", "hello")]` that `openai_compat.py:242` builds.
- Put the logic somewhere it can be tested cheaply and reused by both endpoints.
- Cover the new `app.py` call-site lines, not just the helper, so the gate passes on the
  numbers rather than on hope.
- Correct `docs/docs/api_reference.md`, which documents the pre-change behaviour in detail.

**Non-Goals:**

- Changing the canonical payload shape, or accepting more shapes than today. Both in-repo
  clients already send the nested form; this is about rejecting the rest cleanly.
- Validating any other field (`client_sent_msg_ts`, `client_timeout`, `is_refresh`). Those
  are #175 and #177 and must not be folded in here.
- Refactoring `_prepare_chat_context` or the `_parse_chat_request` return contract.
- Adding a schema/validation library. No new dependency.

## Decisions

### D1 — A pure helper module, `src/interfaces/chat_app/request_validation.py`

A single pure function, no Flask import, no config, no database:

```python
class InvalidLastMessage(ValueError):
    """Raised when the `last_message` payload field is not a [sender, message] pair."""


def parse_last_message(value: Any) -> tuple[str, str]:
    """Return (sender, content) from a well-formed `last_message`, else raise."""
```

**Why a module rather than an inline check** (this is the issue's own instruction, and the
gate is the reason): every executable line added inline to `app.py` lands uncovered unless a
test drives the route. A helper module is covered by direct unit tests at near-zero cost and
is imported unchanged by both endpoints.

**Why raise rather than return a sentinel**: the two call sites want to turn the failure into
a 400 with a message; an exception carries that message without inventing an `(ok, err)`
tuple protocol, and a typed subclass of `ValueError` keeps the failure legible if the helper
is ever reused off the request path.

**Alternative considered — validate inside `_parse_chat_request`.** It is the one place both
endpoints already share, which is tempting. Rejected: `_parse_chat_request` returns a plain
dict and has no way to produce a response, so it would have to raise and both call sites
would still need a `try/except` — the same two edits, with the check hidden a layer further
from the `return 400`. Rejected also because it would run the check on a code path that has
no test coverage today, defeating D3.

**Alternative considered — validate inside `_prepare_chat_context`.** Rejected: it is on
`ChatWrapper`, downstream of the streaming generator, so a rejection there surfaces as an
in-band NDJSON error event under HTTP 200 for the streaming endpoint — exactly the
status-invisible failure mode the spec rules out.

### D2 — Structural acceptance rule, with strings excluded explicitly

Accept when **all** of:

- the value is a `list` or `tuple` and is non-empty;
- `value[0]` is a `list` or `tuple` (explicitly **not** `str`/`bytes`);
- `len(value[0]) == 2`;
- both members are `str`.

The `str`/`bytes` exclusion is the whole bug: a string *is* a sequence of length 2 when it
is `"AI"`, so any check written as "is it a sequence of two items" reproduces the defect it
is meant to fix. Test it directly with `isinstance(value[0], (list, tuple))` rather than
with `len()`/iterability.

Members are **not** coerced with `str()`. Coercing `[["User", 42]]` into `"42"` would be the
same silently-wrong-content failure in a new costume.

Only `value[0]` is validated, matching what the handler reads — the spec's "only the first
pair is read" contract is unchanged.

### D3 — Cover the `app.py` call sites with request-context tests

The two new call sites are roughly:

```python
try:
    parse_last_message(message)
except InvalidLastMessage as exc:
    return jsonify({"error": str(exc)}), 400
```

placed in each handler **immediately after the existing `if not client_id` check**
(`app.py:4649` and `app.py:4730`) and **before** `session.get("user", ...)` and any call into
`self.chat`. That ordering keeps the missing-`client_id` 400 first (no behaviour change for
it) and guarantees the spec's "pipeline is not invoked / no conversation row is created"
scenario.

Those ~4 lines per handler are executable and uncovered by default. Cover them by invoking
the **unbound methods** with a stub `self` inside a Flask request context — no ChatApp
construction, no database:

```python
app = Flask(__name__)
with app.test_request_context(json={"last_message": ["AI", "hello"], "client_id": "c"}):
    response, status = FlaskAppWrapper.get_chat_response(SimpleNamespace(...))
assert status == 400
```

This works because the rejection returns before the handler touches `self.chat`,
`session`, or any other collaborator. `tests/unit/test_openai_compat_endpoints.py` is the
in-repo precedent for driving these routes with a bare `Flask` app and mocks.

**Why this matters numerically**: without route-level tests the numerator is the helper's
lines and the denominator is helper + ~8 uncovered `app.py` lines. With a helper of ~15
executable lines that is ~65% — under the 80% bar, gate red, loop stalls. With the call
sites covered the ratio is ~100%. Do not skip these tests as "extra"; they are what makes
the change land.

### D4 — Streaming endpoint rejects pre-stream

`get_chat_response_stream` builds its NDJSON generator at `app.py:4740` and returns the
`Response` at `app.py:4769`. The validation goes *above* both, so the rejection is a normal
`(jsonify(...), 400)` return. This mirrors the endpoint's existing missing-`client_id`
behaviour (`app.py:4730`) and keeps the failure visible to a client that only reads the
status. It is deliberately **not** emitted as `{"type": "error", "status": 400}`.

### D5 — Documentation edits are scoped to what becomes false

In `docs/docs/api_reference.md`:

- The "**`last_message` is nested**" section (around lines 92–107) currently ends with the
  flat-payload failure analysis and "The endpoint does not currently validate the shape."
  Replace that with the 400 contract, keeping the nested-vs-flat explanation itself — the
  shape guidance is still correct and useful.
- The request-body table row for `last_message` (line 33) gains the rejection behaviour.
- The streaming endpoint's "pre-stream failures still report an ordinary HTTP status"
  material gains the malformed-payload 400 alongside `401`/`302`/missing-`client_id`.

Leave the `client_sent_msg_ts` / `client_timeout` warning and the #175 references alone —
that is a different open issue.

## Risks / Trade-offs

- **An external caller depends on the flat shape and starts getting 400s.** → This is the
  intended behaviour change and the issue's explicit ask. Both in-repo clients
  (`static/chat.js:266`, `openai_compat.py:242`) already send the nested form, so no bundled
  client breaks. The 400 message names the expected shape, so a third-party caller gets a
  diagnosis rather than a mystery — strictly better than today's silent wrong answer.
- **Diff coverage still lands under 80%.** → D3 is the mitigation. If the route-level tests
  prove unworkable for a reason not visible here, the fallback is to keep the `app.py`
  addition to the smallest possible number of executable lines (a single
  `if (err := check(message)): return jsonify({"error": err}), 400`), not to drop the
  validation.
- **`openai_compat.py` builds `[("user", query)]` and would break if the check demanded
  `list`.** → D2 accepts tuples. There is a test scenario for exactly this shape.
- **Validation placed too early rejects a request the timeout check would have 408'd.** →
  Accepted and intended: a malformed payload is malformed regardless of timing, and 400 is
  the more accurate status. No existing test asserts 408-before-400 ordering.
- **`is_refresh` requests.** → A refresh still sends `last_message`; `_prepare_chat_context`
  reads `content` from it even when `is_refresh` skips appending the turn (`app.py:1650`,
  `app.py:1657`). Validation therefore applies uniformly and needs no `is_refresh` special
  case. Do not add one.

## Migration Plan

None required — no data model, no config, no dependency. The change is additive at the two
call sites and deployable in a single release. Rollback is a revert of the branch.

## Open Questions

None. The issue body specifies the accepted shape, the status code, the placement, and the
acceptance criteria; every decision above follows from those plus the gate's mechanics.

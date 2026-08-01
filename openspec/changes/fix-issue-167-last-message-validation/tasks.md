## 1. Ground the change in the current code

- [x] 1.1 Read `src/interfaces/chat_app/app.py:1618-1672` (`_prepare_chat_context`) and
  confirm line 1633 is still `sender, content = tuple(message[0])` with no shape check
  before it. Read `app.py:4590-4618` (`_parse_chat_request`) and confirm `last_message` is
  passed through unvalidated as `request_data["message"]` (`:4606`).
- [x] 1.2 Read both handlers — `get_chat_response` (`app.py:4620`) and
  `get_chat_response_stream` (`app.py:4711`) — and note the exact insertion point named in
  design D3: immediately after the existing `if not client_id: return jsonify(...), 400`
  (`:4649` and `:4730`), before `session.get("user", ...)` and before any `self.chat` call.
- [x] 1.3 Confirm the shapes the in-repo clients send, so the accepted set does not shrink:
  `src/interfaces/chat_app/static/chat.js:266` (`history.slice(-1)` → nested list) and
  `src/interfaces/chat_app/openai_compat.py:242` (`[("user", query)]` → nested **tuple**).
  Both must remain accepted.

## 2. Red: unit tests for the pure helper

- [x] 2.1 Write `tests/unit/test_last_message_validation.py` covering every scenario in
  `specs/chat-request-validation/spec.md`, importing the not-yet-existing
  `src.interfaces.chat_app.request_validation`. Accept cases: `[["User", "hello"]]` →
  `("User", "hello")`, and `[("user", "hello")]` → `("user", "hello")`. Reject cases (each
  raises `InvalidLastMessage`): `["User", "hello"]`, `["AI", "hello"]`, `[]`, `None`,
  `[["User"]]`, `[["User", "hello", "extra"]]`, `[["User", 42]]`, `[[None, "hello"]]`,
  `["AI"]`, `[{"sender": "User"}]`, `"hello"`, `42`.
- [x] 2.2 Include an explicit test that a **two-character sender string** is rejected —
  `["AI", "hello"]` — with a comment naming it as the regression guard for #167, since it
  is the only failure mode that currently returns HTTP 200 with the wrong content.
- [x] 2.3 Assert the raised error's message names the expected shape (contains a nested
  example such as `[["User", "hello"]]`), per the spec's requirement that the 400 body is
  diagnosable.
- [x] 2.4 Run the new test file and watch it fail on `ModuleNotFoundError` — this is the red
  step; do not write the implementation before seeing it.

## 3. Green: the pure helper

- [x] 3.1 Create `src/interfaces/chat_app/request_validation.py` with
  `class InvalidLastMessage(ValueError)` and
  `def parse_last_message(value: Any) -> tuple[str, str]`. No Flask, config or database
  import — it must stay unit-testable in isolation (design D1).
- [x] 3.2 Implement the acceptance rule from design D2 exactly: value is a non-empty
  `list`/`tuple`; `value[0]` is a `list`/`tuple` tested with
  `isinstance(value[0], (list, tuple))` — **never** a generic "is it a sequence" check,
  because `"AI"` is a two-item sequence and that is the bug; `len(value[0]) == 2`; both
  members are `str`. No `str()` coercion of members.
- [x] 3.3 Give the raised error a message naming the expected shape, e.g.
  `last_message must be a list containing a [sender, message] pair of two strings, e.g. [["User", "hello"]]`.
- [x] 3.4 Re-run `tests/unit/test_last_message_validation.py` and confirm it is green.

## 4. Red: endpoint-level tests for the two call sites

- [x] 4.1 Write `tests/unit/test_chat_endpoint_last_message_validation.py` driving the
  **unbound** handlers with a stub `self` inside a bare Flask request context, per design
  D3 — `FlaskAppWrapper.get_chat_response(stub_self)` and
  `FlaskAppWrapper.get_chat_response_stream(stub_self)` under
  `Flask(__name__).test_request_context(json=payload)`. Follow
  `tests/unit/test_openai_compat_endpoints.py` for the in-repo pattern of driving these
  routes with a bare Flask app and mocks.
- [x] 4.2 Assert for **both** handlers: malformed `last_message` (use `["AI", "hello"]` and
  at least one of `[]` / omitted) with a valid `client_id` returns status `400` and a JSON
  body carrying an `error` field.
- [x] 4.3 Assert the pipeline is never invoked on the rejected path — the stub's
  `chat` / `chat.stream` mock records **zero** calls (spec: no conversation row is created,
  because `_prepare_chat_context` is never reached).
- [x] 4.4 Assert the streaming rejection is a plain HTTP 400, **not** a streaming response:
  no opening `meta` NDJSON line, no `{"type": "error"}` event (design D4).
- [x] 4.5 Assert the existing missing-`client_id` 400 still fires first and is unchanged
  when both `client_id` and `last_message` are bad.
- [x] 4.6 Run the file and watch it fail (the handlers do not validate yet) before writing
  the call-site code.

## 5. Green: wire the helper into both endpoints

- [x] 5.1 In `get_chat_response` (`app.py:4620`), immediately after the `if not client_id`
  check, call `parse_last_message(message)` in a `try` and return
  `jsonify({"error": str(exc)}), 400` on `InvalidLastMessage`.
- [x] 5.2 Do the same in `get_chat_response_stream` (`app.py:4711`), placed **above** the
  `_event_stream` generator definition (`:4740`) and the `Response(...)` return (`:4769`),
  so the rejection is an ordinary HTTP response and the generator is never constructed.
- [x] 5.3 Add the import at the top of `app.py` alongside the other
  `src.interfaces.chat_app` imports; keep it isort-clean.
- [x] 5.4 Keep the added executable lines minimal — the diff-coverage ratio is computed over
  changed lines in `src/` only (test files are not in `--cov=src`), so every extra
  `app.py` line is dead weight even once covered.
- [x] 5.5 Re-run both new test files and confirm green.

## 6. Documentation

- [x] 6.1 Update the `last_message` row in the request-body table of
  `docs/docs/api_reference.md` (line 33) to state that a malformed value is rejected with
  HTTP 400.
- [x] 6.2 Rewrite the "**`last_message` is nested**" section (around lines 92–107): keep the
  nested-vs-flat explanation, and replace the failure analysis — the 500 case, the
  two-character-sender "succeeds against the wrong content" case, and the sentence "The
  endpoint does not currently validate the shape" — with the enforced 400 contract on both
  endpoints.
- [x] 6.3 Add the malformed-payload 400 to the streaming endpoint's pre-stream failure list
  (alongside `401`, the login `302`, and the missing-`client_id` `400`), stating that it
  arrives as a real HTTP status with no `meta` line and no in-band event.
- [x] 6.4 Leave the `client_sent_msg_ts` / `client_timeout` warning and its #175 references
  untouched — a different open issue.
- [x] 6.5 Grep the repo for any other prose asserting the no-validation behaviour
  (`grep -rn "does not currently validate\|silently discard" docs/ src/`) and correct or
  remove what this change makes false.

## 7. Verify against the acceptance criteria

- [x] 7.1 Run the gate and confirm exit 0:
  `bash scripts/gate.sh` (outside the loop container, activate the full-deps env first:
  `source ~/miniforge3/etc/profile.d/conda.sh && conda activate archi`). Do not pipe or
  redirect the command; read the persisted output.
- [x] 7.2 Confirm diff coverage ≥ 80% in the gate's `diff-cover` output, and specifically
  that the new lines in `src/interfaces/chat_app/app.py` are reported as covered. If they
  are not, fix the endpoint tests from group 4 — do not weaken the validation.
- [x] 7.3 Walk the issue's acceptance-criteria checklist and confirm each one:
  helper unit-tested directly; `[["User", "hello"]]` unchanged; `["User", "hello"]` → 400
  not 500; `["AI", "hello"]` → 400 not 200-with-`"I"`; `[]` / `null` / missing / non-pair
  first element → 400; both endpoints validate; `api_reference.md` updated.
- [x] 7.4 Confirm no unrelated file changed: `git diff --stat origin/dev` lists only
  `src/interfaces/chat_app/request_validation.py`,
  `src/interfaces/chat_app/app.py`, the two new test files,
  `docs/docs/api_reference.md`, and this change's `openspec/` artifacts.

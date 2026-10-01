# Design — hide run-detail answer-key fields from VIEW-only callers

## D1. One constant names every hidden field

`src/evaluation/qa/run_visibility.py`:

```python
VIEWER_HIDDEN_FIELDS = {
    "preparation": ("answer", "answer_sha256", "gold_atoms", "oracle", "oracle_metadata", "oracle_calls"),
    "prepared_items": ("answer", "answer_sha256", "gold_atoms", "oracle", "oracle_metadata", "oracle_calls"),
    "live_checks": ("answer", "answer_sha256", "metadata", "calls"),
    "answers": ("answer",),
    "evaluation_results": ("answer",),
}
VIEWER_HIDDEN_JUDGMENT_FIELDS = ("rationale",)
```

(Format the dict with black; the one-line form above is for reading.)

Why these fields, by issue term:

- **oracle truth** — `preparation[].answer`/`answer_sha256` (the approved answer),
  `oracle`, `oracle_metadata`, `oracle_calls`; `live_checks[].answer`/`answer_sha256`,
  `metadata`, `calls`.
- **gold atoms** — `preparation[].gold_atoms`; `evaluation_results[].judgments[].rationale`
  restates the atom it judged.
- **raw answers** — `answers[].answer`, `evaluation_results[].answer`.

`answer_sha256` is hidden too: a short approved answer can be found from its digest.

## D2. The function

`redact_run_for_viewer(payload: Mapping[str, Any]) -> Dict[str, Any]`:

1. `result = copy.deepcopy(dict(payload))`. A deep copy matters: `get_run` builds
   `prepared_items` from the same dict objects as `preparation`, and the input must stay
   unchanged.
2. For each `section, fields` in `VIEWER_HIDDEN_FIELDS`: if `result.get(section)` is a
   list, for each row that is a dict, `row.pop(field, None)` for each field. A missing
   section or a non-dict row is left as is (a canceled run has empty lists and no
   `live_checks`).
3. For each dict row in `result.get("evaluation_results")` (if a list) whose `judgments`
   is a list, pop each `VIEWER_HIDDEN_JUDGMENT_FIELDS` name from each dict judgment.
4. Return `result`.

Kept for viewers: `id`, `manifest`, `metadata`, `capabilities`, `summary`,
`report_available`, `cancellation`, and in rows `item_id`, `status`, `question`,
`category`, attempt identity, `duration_ms`, `tool_calls`, scores, and judgment
`atom_id`/`outcome`. The console UI (`static/evaluations.js:1432-1443`) already reads
`attempt.answer || {}`, `answer.answer || result.answer`, and `prepared.gold_atoms || []`,
so absent fields render as empty, not as an error. No JS change.

## D3. Where the switch sits

`EvaluationConsoleService.get_run(self, history_id, *, include_hidden=False)` calls
`self.history.get_run(history_id)` and returns it as is when `include_hidden` is `True`,
else `redact_run_for_viewer(...)` of it. The fail-closed default means a new caller that
forgets the flag gets the viewer form.

`run_detail` becomes
`_bounded_json_response({"run": _service().get_run(history_id, include_hidden=_can_manage())})`.
`_can_manage()` is already `True` when the session is not logged in (auth-off deployments),
so auth-off behaviour does not change.

The fake service in `tests/unit/test_evaluation_routes.py:74-75` and the lambda at
`tests/unit/test_evaluation_routes.py:669` take only `history_id`; both must accept the
new keyword.

## D4. Tests

- `tests/unit/evaluation/qa/test_run_visibility.py` (new):
  - A payload with every hidden field in every section; after `redact_run_for_viewer`,
    each name in the constants is absent from each row and judgment, each kept field is
    present and equal, and the input payload is unchanged (compare to a deep copy taken
    before).
  - A payload with no `live_checks`, empty lists, a non-dict row, and a non-list
    `judgments` does not raise.
  - A test that names each hidden field literally (the expected constant written out in
    the test), so a removal from the constant fails a test.
  - Console service: build `EvaluationConsoleService(tmp_path / "console",
    agent_config_path=..., agents_dir=...)` as `test_jobs_history.py:723` does, replace
    `service.history` with a stub whose `get_run` returns the full payload, and assert that
    `service.get_run("h")` is redacted and `service.get_run("h", include_hidden=True)`
    equals the full payload.
  - Resolved config: assert that the text of `src/evaluation/qa/history.py` and of
    `src/interfaces/chat_app/evaluation_routes.py` does not contain
    `agent_config.resolved.yaml` (true on `26e6429e`), so a later change that starts to
    serve the file fails this test.
- `tests/unit/test_evaluation_routes.py`: give `_Service.get_run` the signature
  `get_run(self, history_id, *, include_hidden=False)`; it records the flag and returns
  `redact_run_for_viewer(FULL)` unless `include_hidden`. Log in a session and monkeypatch
  `evaluation_routes.has_permission` as the attention test at line 370 does:
  - VIEW only → the flag is `False` and the JSON has none of the hidden fields;
  - with `MANAGE` → the flag is `True` and the fields are back;
  - not logged in (auth off) → the flag is `True`.
  - The run-list and `/report` responses are equal for the VIEW and MANAGE caller.
  - Change the lambda at line 669 to `lambda _history_id, **_kwargs: ...`.
- The nav/route parity test (`tests/unit/test_evaluation_console.py:649`) must still pass
  unchanged.

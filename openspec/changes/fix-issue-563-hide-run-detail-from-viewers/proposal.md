# Hide oracle truth, gold atoms, and raw answers from VIEW-only run-detail callers

## Why

`GET /api/evaluations/runs/<history_id>` (`run_detail`,
`src/interfaces/chat_app/evaluation_routes.py:355-360`) returns
`_service().get_run(history_id)` to any caller that holds `Permission.Evaluations.VIEW`.
`EvaluationHistory.get_run` (`src/evaluation/qa/history.py:626-716`) puts every artifact row
into that payload:

- `preparation` and `prepared_items` rows carry the approved answer (`answer`,
  `answer_sha256`), the `gold_atoms`, and the live-oracle recipe and evidence (`oracle`,
  `oracle_metadata`, `oracle_calls`) — `PreparationRecord.to_dict`,
  `src/evaluation/qa/preparation.py:219-250`.
- `live_checks` rows carry the live-oracle truth (`answer`, `answer_sha256`, `metadata`,
  `calls`) — `LiveCheck.to_dict`, `src/evaluation/qa/live_checks.py:96-110`.
- `answers` rows and `evaluation_results` rows carry the raw agent answer (`answer`);
  `evaluation_results` judgments carry a `rationale` that restates the gold atom it judged
  (`score_answer`, `src/evaluation/qa/phases.py:106-130`).

A VIEW-only user can therefore read the answer key of any dataset. That breaks benchmark
isolation. The job-detail route already splits viewers from managers with
`include_hidden=_can_manage()` (`evaluation_routes.py:393`); run detail does not.

This is issue #563 (child 3 of #320, milestone v2026.11.0, tier `sonnet`). The decision is
recorded in the issue body: reuse `include_hidden=_can_manage()`, add no new permission.

**Anchor correction.** The issue lists "the resolved agent config" among the fields that
`get_run` returns. On `origin/dev` `26e6429e` it does not: `get_run` never reads
`agent_config.resolved.yaml`. The payload's `manifest.agent` names the artifact and carries
only `agent_class`, `provider`, and `model` (`workflow.py:584-590`). No route serves the
file. This change keeps it that way and pins it with a test, for both roles.

## What Changes

- New module `src/evaluation/qa/run_visibility.py`: one constant `VIEWER_HIDDEN_FIELDS`
  (row section → hidden field names), one constant `VIEWER_HIDDEN_JUDGMENT_FIELDS`, and
  one pure function `redact_run_for_viewer(payload) -> dict` that returns a deep copy with
  those fields removed.
- `EvaluationConsoleService.get_run` (`src/evaluation/qa/console.py:259-260`) takes a
  keyword-only `include_hidden: bool = False` and returns the redacted payload unless it is
  `True`. The default fails closed.
- `run_detail` passes `include_hidden=_can_manage()`.
- `EvaluationHistory.get_run` does not change; its other callers (tests, the CLI path)
  keep the full payload.

## Out of scope

- A new permission; the auth seam (#561); redaction of the persisted file (#562); the CLI
  rename (#564).
- The `/report` endpoint and the run list stay as they are for both roles (the issue's
  acceptance criteria).
- `tool_calls` in `answers` rows stay visible: the latency chart reads them and the issue
  does not name them.

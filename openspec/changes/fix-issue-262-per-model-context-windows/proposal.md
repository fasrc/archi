## Why

A request-local model override on a metadata-less provider installs **no in-loop
context bound at all**. Two correct-in-isolation decisions combine into the gap:

- `resolve_configured_model_window()` (`context_budget.py:373-406`) deliberately
  returns `None` for any model named in the deployment's own `models:` list,
  because `ModelInfo.context_window` defaults to a fabricated `128000` that
  nothing about the deployment produced. Its docstring names this issue as the
  tracked fix.
- `declared_window_applies=False` (`context_middleware.py:410-411`, passed from
  `base_react.py:1423` whenever `self._is_request_local`) withdraws the single
  `context_editing.context_window`, because that value describes the model the
  deployment serves, not the model a request overrode to (#235 Decision 12).

So on the FASRC dev deployment a chat-dropdown switch to the second vLLM slot
(`palmfuture/Qwen3.8-27B-GPTQ-Int4`, launched `--max-model-len 32768`) resolves
no window, installs nothing, and lets a long conversation submit an oversized
prompt that vLLM then rejects. The protection fails **open and silently** — one
warning line is the only trace, and a deployment protecting nothing looks exactly
like a healthy one.

The operator resolved the design on 2026-08-23 (issue #262 body): adopt a
per-model map, `services.chat_app.context_editing.context_windows`, keyed by
model id. An operator who names a window for an exact model id is testifying
about *that* model, which is precisely what Decision 12's "the window and the
model always describe the same thing" invariant demands.

Anchors below are verified against `origin/dev` @ `3de206bc` (2026-08-24). The
issue body cites `4fb0050c`; the two commits since then touched only
`docs/docs/proposals/release-plan-2026.md` and a new `openspec/changes/`
directory, so every anchor still holds.

## What Changes

- Add `context_windows: Mapping[str, int]` to `ContextEditingSettings`
  (`context_budget.py:173-186`) and parse it in `read_settings` (`:245`) beside
  `_read_declared_window` (`:300`), with the same validation posture: an invalid
  entry is logged and ignored, and never removes protection the other settings
  configure.
- Wire the precedence in `build_context_middleware`
  (`context_middleware.py:410-411`) and extend its keyword-only signature with an
  explicit **model id**. The model id is passed, never parsed out of
  `model_label` — a model id may itself contain `/`
  (`palmfuture/Qwen3.8-27B-GPTQ-Int4`), so splitting the label cannot recover it.
- Keep the `base_react.py:1414-1424` call-site change to one added keyword
  argument. All logic lives in the two `utils/` modules, which unit tests import
  directly.
- Docs ride the same PR (the operator's 2026-08-18 comment on the issue):
  `docs/docs/fasrc_archi.md:465-477` and `docs/docs/rag_architecture.md:173-190`.

**Precedence, exactly.** For the effective model `M` of a run:

1. `context_windows[M]` present → that entry is the declared window. This holds
   for BOTH the configured default model and a request-local override.
2. Else, the run is NOT request-local → the single `context_window` applies
   (today's behaviour).
3. Else (request-local, `M` absent from the map) → resolve from the provider;
   when that yields `None`, install nothing and log the warning (today's
   fail-open, byte-for-byte unchanged).

`declared_window_applies=False` continues to withdraw **only** the single
window, never a per-model match.

## Capabilities

### New Capabilities
<!-- None: this extends the in-loop bound behaviour introduced by issue #235. -->

### Modified Capabilities
- `agent-context-resilience`: the delta is written as an **ADDED** requirement
  rather than a `MODIFIED` one on purpose. The in-loop bound requirements this
  builds on were added by the still-unarchived change
  `openspec/changes/fix-issue-235-in-loop-context-budget/`, so they are not
  present in `openspec/specs/agent-context-resilience/spec.md` yet and there is
  no published requirement text to modify. The new requirement is named so it
  cannot collide with any of #235's when both are archived.

## Impact

- **Code**: `src/archi/pipelines/agents/utils/context_budget.py` (new field, new
  parser), `src/archi/pipelines/agents/utils/context_middleware.py` (precedence +
  signature + docstring), `src/archi/pipelines/agents/base_react.py` (one added
  keyword argument at `:1423`). All three files are black-clean at
  `3de206bc` under black 24.10.0, so an in-place edit carries no reflow risk.
- **Tests**: `tests/unit/test_context_budget.py` and the context-middleware unit
  tests. `ContextEditingSettings(**base)` at
  `tests/unit/test_context_budget.py:58` keeps working because the new field is
  defaulted.
- **Config**: one new optional key,
  `services.chat_app.context_editing.context_windows`. **No template change** —
  `src/cli/templates/base-config.yaml:98-103` passes the whole `context_editing`
  block through `tojson`, so a new key inside it renders already.
- **User-facing**: a deployment that sets no `context_windows` sees zero
  behaviour change. One that names its self-hosted model gets an in-loop bound on
  the chat dropdown's override for the first time.
- **Out of scope**: no provider-schema change (windows on `models:` list entries
  — rejected by the operator); no `min(declared, resolved)` and no
  provider-sharing heuristic (both rejected in #235 Decision 12); no change to
  the per-tool clamps in `tools/result_limits.py`; nothing under `deploy/**`,
  `config/**`, or `.github/workflows/**`; and #288's wider doc-truthfulness sweep
  beyond the two pages named above.
- **Post-merge, operator-owned (NOT a PR gate)**: add the real `context_windows`
  entry to the fasrc-dev config and redeploy — the running config lives in
  Postgres, so a `config.yaml` edit without a redeploy is a no-op.

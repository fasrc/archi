# Design — per-model declared context windows

All anchors verified against `origin/dev` @ `3de206bc` (2026-08-24).

## Context

Three seams decide whether a run gets an in-loop bound:

| Seam | File:line | What it decides |
|---|---|---|
| Window resolution | `context_budget.py:349` `resolve_model_window`, `:373` `resolve_configured_model_window` | What the provider reports — `None` for any model the deployment's own config names |
| Settings parse | `context_budget.py:245` `read_settings`, `:325` `_read_declared_window` | What the operator declared |
| Precedence | `context_middleware.py:410-411` | Which of the two wins |
| Call site | `base_react.py:1414-1424` `_build_static_middleware` | Which model the run is actually bound to |

The defect lives at the intersection of rows 1 and 3: for a request-local
override, row 1 yields `None` (correctly — `128000` is `ModelInfo`'s default, not
a measured value; the dev server is launched `--max-model-len 32768`) and row 3
withdraws the declared window (correctly — it describes a different model). The
result is a silent fail-open. Neither seam is wrong on its own; there is simply
no way for an operator to testify about the override's own model.

## Decision 1 — a map keyed by model id, in the existing config block

`services.chat_app.context_editing.context_windows`, `{model_id: total_window}`.

**Why this shape.** The map's key IS the model id, so an entry cannot be applied
to a model it does not name. That is Decision 12's invariant ("the window and the
model always describe the same thing") enforced by the data structure rather than
by a rule someone has to remember. It also needs no change to provider
construction, and no change to `base-config.yaml` — that template passes the
whole `context_editing` block through `tojson`
(`src/cli/templates/base-config.yaml:98-103`), so a new key inside the block
renders with no template edit.

**Rejected: windows on provider `models:` list entries.** A wider schema change
threaded through provider construction, for the same expressive power. Rejected
by the operator on 2026-08-23.

**Rejected: won't-fix.** The deployment this defect is measured on is the one in
production use. Rejected by the operator on the same date.

## Decision 2 — the map beats the single window, for its model only

Effective model `M`:

1. `context_windows[M]` present → use it. Applies to the configured default model
   AND to a request-local override.
2. Else, NOT request-local → the single `context_window` applies (today).
3. Else → resolve from the provider; `None` means install nothing + warn (today).

**Why the map outranks the single window even for the default model.** The two
settings differ in specificity, not in trustworthiness. Both are operator
testimony; only one of them names the model it describes. When an operator has
written both, the more specific statement is the one they meant. Making the
single window win would mean an operator could not correct it for one model
without deleting it for all of them.

**Why `declared_window_applies=False` must not withdraw a map hit.** That flag
exists to stop a window that describes *the deployment's model* from following a
view onto a *different* model. A map entry keyed by the override's own id is not
that case — it is exactly the testimony the flag was invented to demand. Letting
the flag suppress it would reintroduce the bug this change closes.

The code is a two-line precedence at `context_middleware.py:410-411`:

```python
declared = settings.context_windows.get(model_id) if model_id else None
if declared is None and declared_window_applies:
    declared = settings.context_window
window = context_window if declared is None else declared
```

`model_id` is `Optional[str]` and keyword-only, so every existing caller and test
keeps working unchanged and behaves exactly as it does today (`None` → no map
lookup → the current two branches).

## Decision 3 — pass the model id; never parse `model_label`

`model_label` is `f"{self.default_provider}/{self.default_model}"`
(`base_react.py:1422`). A model id may itself contain `/` — the worked example is
`palmfuture/Qwen3.8-27B-GPTQ-Int4` — so `label.split("/")` recovers the wrong id
on precisely the deployment this change exists to protect. `model_label` stays
what it is: a human-readable string for one log line.

On a request-local agent `self.default_model` IS the override model
(`adopt_request_local_model()` rewrites it; `_is_request_local` is set from the
same comparison at `base_react.py:1671`), so the id needed for the lookup is
already in scope at the call site. The call-site change is one added keyword
argument.

## Decision 4 — validation posture: ignore the bad entry, keep every good one

Mirrors `_read_declared_window` (`context_budget.py:325-347`) and the `enabled: 0`
rule documented in `context_middleware.py`: an invalid value is logged and
ignored, and never silently removes protection the other settings configure.

| Input | Outcome |
|---|---|
| key absent, or `{}` | empty map — identical to today |
| not a mapping (list, string, `None`, int) | warn once, empty map; the single `context_window` still applies |
| a value that is not a positive int | warn naming the key, skip **that entry**, keep the others |
| `True` as a value | rejected — `positive_int` (`context_budget.py:203-207`) rejects `bool` first, and a one-token window would clear every message on every call |
| a non-string key | warn naming the key, skip that entry |

The per-entry granularity matters: one typo in a five-model map must not cost the
operator the other four bounds.

## Decision 5 — field type and immutability

`context_windows: Mapping[str, int] = field(default_factory=dict)` on the frozen
`ContextEditingSettings`.

`frozen=True` gives shallow immutability only, so a dict field is technically
mutable through a held reference. Two things make that acceptable here: the
parser builds a fresh dict per `read_settings` call and hands out no other
reference, and nothing in `src/` or `tests/` hashes or sets a
`ContextEditingSettings` (verified by grep at `3de206bc` — the only uses are
construction at `context_budget.py:272`, the `resolve_budget` parameter at
`:453`, and `ContextEditingSettings(**base)` at
`tests/unit/test_context_budget.py:58`). Typing the field as `Mapping` rather
than `Dict` states read-only intent at the boundary. Wrapping in
`MappingProxyType` was considered and rejected as ceremony that would complicate
the test constructor for no reachable failure mode.

Because the field is defaulted, `ContextEditingSettings(**base)` at
`tests/unit/test_context_budget.py:58` and every other construction site keep
working with no edit.

## Risks

- **Over-application.** A map entry applying to a model the operator did not name
  would be the same class of defect as the one being fixed. Mitigated by keying
  on an exact model id, with unit tests for both the hit and the miss.
- **Silent regression of the fail-open path.** The "override absent from the map"
  branch must stay byte-for-byte today's behaviour, warning included. It gets its
  own test asserting the warning still fires.
- **`base_react.py` churn.** The file is 1600+ lines but black-clean under 24.10.0
  at `3de206bc`, and the edit is one keyword argument inside an existing call, so
  a reflow that would sink patch coverage is not expected. Confirm with
  `black --check` before staging anyway; if it ever does reflow, the logic is
  already in `utils/`, so the call site can absorb the churn alone.

## What this change does NOT do

- No `min(declared, resolved)`, and no provider-sharing heuristic — both rejected
  in #235 Decision 12.
- No provider-schema change.
- No change to `resolve_model_window` / `resolve_configured_model_window`. They
  keep returning `None` for config-named models; the map is a separate,
  higher-precedence source, not a repair of theirs.
- No change to the per-tool clamps in `tools/result_limits.py`.

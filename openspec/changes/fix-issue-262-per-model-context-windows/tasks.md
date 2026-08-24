All anchors are verified against `origin/dev` @ `3de206bc` (2026-08-24).

**Every task below must end with the suite GREEN and `bash scripts/gate.sh` passing.**
Each task therefore folds its failing test AND the code that turns it green into one
task: write the test, run it, watch it fail for the stated reason, then implement the
minimum that passes. A task that ends red can never be committed, and the loop halts.

## 1. Parse the per-model map

- [ ] 1.1 `model: sonnet` — In `tests/unit/test_context_budget.py`, add failing tests for
      a new `_read_declared_windows(value)` in `context_budget.py`: `None` and `{}` both
      yield an empty mapping; a well-formed `{"palmfuture/Qwen3.8-27B-GPTQ-Int4": 32768}`
      yields that pair; a non-mapping (list, string, int) warns once and yields empty; a
      non-positive, non-integer, or `True` value warns naming the key and drops **only
      that entry** while the sibling entry survives; a non-string key warns and drops only
      that entry. Watch them FAIL (no such function). Then write
      `_read_declared_windows` beside `_read_declared_window` (`context_budget.py:325`),
      reusing `positive_int` (`:203`) so `bool` is rejected before `int`, and matching the
      existing warning style ("Ignoring …=%r; …"). Green.

- [ ] 1.2 `model: sonnet` — Add a failing test that `read_settings` (`context_budget.py:245`)
      surfaces the parsed map as `ContextEditingSettings.context_windows`, through BOTH
      config layers and with `pipeline_config` overriding `services.chat_app` per the
      documented merge order at `:250`. Assert `ContextEditingSettings(**base)` at
      `tests/unit/test_context_budget.py:58` still constructs with no `context_windows`
      argument, and that the default is an empty mapping (NOT a shared mutable default —
      two `read_settings` calls must not see each other's map). Watch it FAIL. Then add
      `context_windows: Mapping[str, int] = field(default_factory=dict)` to
      `ContextEditingSettings` (`:173-186`) and wire the parse into the `read_settings`
      return beside `context_window=` (`:300`). Green.

## 2. Precedence in the middleware

- [ ] 2.1 `model: opus` — In `tests/unit/test_context_middleware.py`, add failing tests for
      the new keyword-only `model_id` parameter on `build_context_middleware`
      (`context_middleware.py:343-352`), covering all five precedence branches from
      design Decision 2:
      (a) request-local (`declared_window_applies=False`) + a map hit → the bound is sized
      from the map entry;
      (b) request-local + a map miss + no resolvable window → NO middleware is installed
      **and the existing no-window warning still fires** (assert on the log record, not
      just the empty list — this branch must stay byte-for-byte today's behaviour);
      (c) not request-local + a map hit that differs from the single `context_window` →
      the map entry wins;
      (d) not request-local + a map miss → the single `context_window` still applies;
      (e) `model_id=None` (every existing caller) → behaviour identical to today.
      Watch them FAIL (unexpected keyword argument). Then implement the precedence at
      `:410-411` as design Decision 2 spells it out, with `model_id: Optional[str] = None`
      keyword-only. Green.

- [ ] 2.2 `model: sonnet` — Add a failing test that a model id **containing `/`** (use
      `palmfuture/Qwen3.8-27B-GPTQ-Int4`) is matched in full against the map while
      `model_label="local/palmfuture/Qwen3.8-27B-GPTQ-Int4"` is passed alongside it. This
      is the regression guard for design Decision 3 — it must fail if anyone later
      "simplifies" the parameter away by splitting `model_label`. Watch it fail if 2.1's
      implementation is wrong; it passes if 2.1 was done right — record which, and in the
      passing case state in the task note that it is a guard, not a red-first step. Green.

- [ ] 2.3 `model: opus` — Update the `build_context_middleware` docstring block at
      `context_middleware.py:384-399`: state how the per-model map extends #235 Decision 12
      (the map is testimony about the override's OWN model, so
      `declared_window_applies=False` withdraws only the single window), and keep the
      measured 32768-declared-plus-64000-cap example that records why the flag exists. No
      behaviour change; the suite stays green.

## 3. Call site

- [ ] 3.1 `model: opus` — Run `python -m black --check src/archi/pipelines/agents/base_react.py`
      FIRST and record the result in the task note. It is clean at `3de206bc`; if it ever
      is not, stop and report rather than letting a reflow sink patch coverage. Then add a
      failing test — extend `tests/unit/test_request_local_pipeline.py` (it already imports
      `_build_request_local_pipeline` from the chat app at `:23`) — proving that a
      request-local view bound to a model named in `context_windows` installs a bound,
      through the REAL builder rather than a direct `build_context_middleware` call. Watch
      it FAIL. Then add `model_id=self.default_model` to the
      `build_context_middleware(...)` call in `_build_static_middleware`
      (`base_react.py:1414-1424`) — one keyword argument, no other logic at this call site.
      Green.

## 4. Docs (same PR — required by the operator's 2026-08-18 comment on #262)

- [ ] 4.1 `model: sonnet` — Re-derive the anchor at the PR head first (`grep -n "3.8 slot
      runs without a context bound" docs/docs/fasrc_archi.md`; it is `:465-477` at
      `3de206bc`). Replace that blockquote: it must now show the per-model declaration
      (`context_windows:` with `palmfuture/Qwen3.8-27B-GPTQ-Int4: 32768` under
      `services.chat_app.context_editing`) and MUST NOT contain the sentence "keep 3.8
      conversations short or drive the endpoint directly". Keep the explanation of WHY the
      provider reports nothing for a config-named model — that is still true and is why
      the declaration is needed. Suite stays green.

- [ ] 4.2 `model: sonnet` — In `docs/docs/rag_architecture.md`, add a `context_windows` row
      to the `context_editing` key table (`:173-190`, verified at `3de206bc`) with one
      sentence on precedence: a per-model entry beats the single `context_window` for that
      model only, and it is the only declaration that survives a request-local model
      override. Suite stays green.

## 5. Ship

- [ ] 5.1 `model: opus` — Verify every acceptance criterion in issue #262 against the tree,
      one by one, naming the test that proves each. Run `bash scripts/gate.sh` bare (never
      piped — the pipe guard blocks it) and confirm patch coverage >= 80%. Confirm
      `git status --porcelain` is empty after formatting and staging. Then push with
      `git push -u origin fix/issue-262-per-model-context-windows` (the branch tracks
      `origin/dev` from `checkout -b`, so `-u` is required) and open the PR against
      `fasrc/archi:dev` with `closes #262` **in the body** — a closing keyword in the
      title does not link the issue. Do NOT merge.

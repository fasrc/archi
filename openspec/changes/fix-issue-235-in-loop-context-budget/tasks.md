## 1. Branch and baseline

- [ ] 1.1 Branch from `origin/dev` as `fix/issue-235-in-loop-context-budget` (never commit to `dev`)
- [ ] 1.2 Confirm the two blind spots still exist on the branch base: `grep -n "DEFAULT_TOOL_BUDGETS" src/archi/pipelines/agents/base_react.py` shows only `search_vectorstore_hybrid`, and every `max_prompt_tokens` hit is inside `_prepare_agent_inputs`
- [ ] 1.3 Confirm `_build_static_middleware` still returns `[]` and is passed to `create_agent(..., middleware=...)`
- [ ] 1.4 Record the black-cleanliness of the intended insertion points in `base_react.py` before editing (a reflow of untouched code sinks patch coverage)

## 2. Budget derivation helper — RED then GREEN

- [ ] 2.1 Write failing unit tests for a new `src/archi/pipelines/agents/utils/context_budget.py`: budget = window − 15% of window
- [ ] 2.2 Write failing tests for fail-open: `None`, zero, negative, and non-integer windows all produce no middleware
- [ ] 2.3 Write failing tests for the three-layer config lookup (class default → `services.chat_app.context_editing` → `pipeline_config.context_editing`), later layers overriding earlier
- [ ] 2.4 Write failing tests for invalid config values (non-numeric / out-of-range margin and preserve count): warn, use the default for that value, still install the bound
- [ ] 2.5 Write a failing test that `enabled: false` installs no middleware
- [ ] 2.6 Watch all of 2.1–2.5 fail for the right reason (module does not exist / returns nothing)
- [ ] 2.7 Implement `context_budget.py` to the minimum that passes: config read + validation, budget derivation, middleware construction
- [ ] 2.8 Assert no hard-coded context length: `git diff origin/dev -- src/ | grep -E '^\+.*\b(32768|16384|8192)\b'` returns nothing

## 3. Middleware construction and its options — RED then GREEN

- [ ] 3.1 Failing test: the constructed edit carries `trigger` equal to the derived budget
- [ ] 3.2 Failing test: `keep` defaults to 3 and honours a config override
- [ ] 3.3 Failing test: `search_vectorstore_hybrid` is present in `exclude_tools`
- [ ] 3.4 Failing test: `clear_tool_inputs` is `False` (the model must retain the record of its own call)
- [ ] 3.5 Failing test: the placeholder text states the result was cleared for context reasons and instructs the model not to re-request it
- [ ] 3.6 Failing test: `token_count_method` is `"approximate"`
- [ ] 3.7 Watch 3.1–3.6 fail, then implement to green

## 4. Wire into the agent — RED then GREEN

- [ ] 4.1 Failing test in `tests/unit/test_react_agent_tool_budget.py`: with a known context window, `_build_static_middleware()` returns a non-empty middleware list (fails on `origin/dev`, which returns `[]`)
- [ ] 4.2 Failing test: with an undeterminable context window, `_build_static_middleware()` returns `[]` (behaviour identical to today)
- [ ] 4.3 Failing test: the middleware list reaches `create_agent(...)` — assert on what `_create_agent` is called with
- [ ] 4.4 Watch 4.1–4.3 fail, then make `_build_static_middleware` a thin call site delegating to `context_budget.py`
- [ ] 4.5 Verify the `base_react.py` diff is a handful of lines with no black reflow of surrounding code

## 5. Behavioural tests — the acceptance criteria

- [ ] 5.1 Failing test: given an accumulated message list over the budget with more tool results than the preserve count, the oldest tool results are reduced before the model call
- [ ] 5.2 Failing test: a message list within budget is passed through untouched
- [ ] 5.3 Failing test (the boundary criterion): a run performing many document reads still returns a substantive answer, not the canned apology
- [ ] 5.4 Failing test: the N most recent tool results retain original content after reduction
- [ ] 5.5 Failing test: retrieval-tool results retain original content regardless of age
- [ ] 5.6 Failing test: tool-call/tool-result pairing survives reduction — no dangling `tool_call_id`
- [ ] 5.7 Failing test: reduction is applied on a *later* model call when the budget is first exceeded mid-loop, not only before the loop
- [ ] 5.8 Watch 5.1–5.7 fail, then implement to green

## 6. Regression surface

- [ ] 6.1 `tests/unit/test_react_agent_context_overflow.py` passes unchanged — the reactive handler is retained as the last-resort net
- [ ] 6.2 Existing tool-budget tests (`test_react_agent_tool_budget.py`, `test_retriever_tool_budget.py`, `test_subclass_agent_memory_binding.py`, `test_active_memory_contextvar.py`) pass unchanged
- [ ] 6.3 The pre-loop budget in `_prepare_agent_inputs` is unmodified

## 7. Gate and pre-PR review loop

- [ ] 7.1 `bash scripts/gate.sh` exits 0 with patch coverage ≥ 80% against `origin/dev` (run bare, never piped; never `--no-verify`)
- [ ] 7.2 Run `/codex:adversarial-review --wait` on the branch; verify each finding against the code, fix what holds (TDD), push back with reasons on what does not
- [ ] 7.3 Re-run the adversarial review; repeat until a round returns zero findings or only nits (bound ~3–4 rounds)
- [ ] 7.4 File remaining nits as tracked issues rather than blocking the PR
- [ ] 7.5 Document the config seam in `docs/` alongside the existing `tool_budgets` documentation

## 8. PR

- [ ] 8.1 Open the PR with `gh pr create --repo fasrc/archi --base dev`; no `Co-Authored-By` or session trailers
- [ ] 8.2 PR body records the Phase 1 token accounting table, names option (c) as chosen, and states that (a) and (b) were rejected and why
- [ ] 8.3 PR body records the two corrections to the issue: the `DEFAULT_TOOL_BUDGETS` entry would be inert (no `enforce_budget` seam on `create_document_fetch_tool`), and source count is decoupled from context cost by construction
- [ ] 8.4 PR body states explicitly that the `fetch_catalog_document` call cap is deliberately out of scope, and why
- [ ] 8.5 PR body states which acceptance criteria could not be verified locally (goldenset runs need the deployment + VPN) and carries the pre-PR review summary
- [ ] 8.6 Request `@codex review` as a PR comment (never in the PR body)
- [ ] 8.7 Post-PR review loop: triage → fix (TDD) → reply in-thread per finding → push → re-request, until a clean round or only-nits-deferred; post a round log comment each round

## 9. Goldenset verification (needs deployment + FASRC VPN)

- [ ] 9.1 Re-execute the benchmark container in place: `docker start benchmarking-ragas-205`. Do **not** redeploy — that re-scrapes the corpus and changes the comparison
- [ ] 9.2 Re-derive degraded counts from the new results with the issue's script; confirm **0** rows with `status="degraded"` across three consecutive runs (5 → 1 does not satisfy this)
- [ ] 9.3 Confirm `question_94` returns a substantive answer citing `https://slurm.schedmd.com/salloc.html` rather than the apology
- [ ] 9.4 Report the result on #235 and note that the #205 group-6 manifest can be re-pre-registered

## 1. Branch and baseline

- [ ] 1.1 Branch from `origin/dev` as `fix/issue-235-in-loop-context-budget` (never commit to `dev`)
- [ ] 1.2 Confirm the two blind spots still exist on the branch base: `grep -n "DEFAULT_TOOL_BUDGETS" src/archi/pipelines/agents/base_react.py` shows only `search_vectorstore_hybrid`, and every `max_prompt_tokens` hit is inside `_prepare_agent_inputs`
- [ ] 1.3 Confirm `_build_static_middleware` still returns `[]` and is passed to `create_agent(..., middleware=...)`
- [ ] 1.4 Record the black-cleanliness of the intended insertion points in `base_react.py` before editing (a reflow of untouched code sinks patch coverage)

## 2. Enforced result ceilings — RED then GREEN

Prerequisite for every size claim later: without these, a "preserved" or "exempted" tool result
is unbounded and no floor arithmetic holds.

- [ ] 2.1 Failing test: `fetch_catalog_document`'s **complete serialized return** is within the ceiling when `max_chars` exceeds it. Clamping the requested value alone is not enough — `_fetch_document` appends a path and up to 800 chars of metadata preview after the text (`local_files.py:530-539`), so a 4000-char request returns ~4800+
- [ ] 2.2 Failing test: `max_chars=0` does **not** disable truncation (today `if max_chars and ...` at `uploader_app/app.py:769` returns the whole document)
- [ ] 2.3 Failing test: negative and non-integer `max_chars` are treated as a request for the ceiling, not as "no limit"
- [ ] 2.4 Failing test: a value *below* the ceiling is still honoured — clamping must not flatten legitimate smaller reads
- [ ] 2.5 Failing test: the retriever tool's **complete serialized output** is clamped — with documents whose `title`/`url`/`resource_hash` metadata is pathologically large, the returned string stays within the ceiling even though `max_chars` bounds only `page_content` (`retriever.py:42-57`)
- [ ] 2.6 Failing test: a normal retrieval result well under the ceiling is returned unmodified — the clamp must not truncate ordinary output
- [ ] 2.7 Watch 2.1–2.6 fail, then implement both clamps, each ceiling configurable and defaulting to today's effective behaviour
- [x] 2.8 File the follow-up issue for the unclamped `max_chars` in `api_catalog_document` (`src/interfaces/uploader_app/app.py:761-770`), which this change deliberately leaves open for non-agent callers — filed as #260

## 3. Budget derivation helper — RED then GREEN

- [ ] 3.1 Write failing unit tests for a new `src/archi/pipelines/agents/utils/context_budget.py`: budget = window − reserve, where reserve = `max(percentage_floor, ModelInfo.max_output_tokens)`
- [ ] 3.2 Write failing tests for fail-open: `None`, zero, negative, and non-integer windows all produce no middleware; likewise when the reserve would consume the whole window (non-positive budget)
- [ ] 3.3 Write failing tests for the three-layer config lookup (class default → `services.chat_app.context_editing` → `pipeline_config.context_editing`), later layers overriding earlier
- [ ] 3.4 Write failing tests for invalid config values (non-numeric / out-of-range reserve, preserve count, exemption fraction): warn, use the default for that value, still install the bound
- [ ] 3.5 Write a failing test that `enabled: false` installs no middleware
- [ ] 3.6 Failing test: the exemption floor is computed as `tool_budget("search_vectorstore_hybrid") × per_result_**token**_ceiling` — both terms in the budget's own unit, measured with the same counter — reading the call budget through the existing `_tool_budgets()` lookup, **not** from the formatter's `max_documents`/`max_chars`, which no call site passes and no config path reaches
- [ ] 3.7 Failing test: the exemption is dropped with a warning when that floor exceeds the configured fraction of the budget; retained when it does not
- [ ] 3.8 Failing test: raising `services.chat_app.tool_budgets.search_vectorstore_hybrid` alone is enough to flip the exemption off — the check must track the runtime value, not a constant
- [ ] 3.9 Failing test: a model whose effective output cap exceeds the percentage reserve gets a budget leaving at least that much free. Regression case from review: Claude Sonnet 4 is `context_window=200000, max_output_tokens=64000` (`anthropic_provider.py:20-29`) and `get_chat_model` applies it as `max_tokens` **when the caller sets none** (`:91-97`), so a flat 15% would permit a 170 K prompt alongside 64 K of requested generation against a 200 K window
- [ ] 3.10 Failing test: the reserve reads the **effective** cap, not the metadata, on both branches — a configured `max_tokens` larger than `ModelInfo.max_output_tokens` wins, and declared metadata a provider never applies does not inflate the reserve. `LocalProvider` declares `max_output_tokens=8192` (`local_provider.py:184-192`) but passes it to neither constructor (`:94-125`), so the SUT budget is 27853 (percentage) when `extra_kwargs` sets no cap and smaller when it does. **Do not** assert "the local provider declares none" — an earlier revision claimed that and it is false
- [ ] 3.11 Failing test: the exemption is bounded **by count** — with more retrieval-named results present than the per-turn call budget (the surplus being post-budget synthetic refusals from `base_react.py:1827-1834` returned via `retriever.py:114-121`), only the most recent up to the call budget are exempt and the rest are cleared
- [ ] 3.12 Watch all of 3.1–3.11 fail for the right reason (module does not exist / returns nothing)
- [ ] 3.13 Implement `context_budget.py` to the minimum that passes: config read + validation, budget derivation, exemption sizing, middleware construction
- [ ] 3.14 Assert no hard-coded context length: `git diff origin/dev -- src/ | grep -E '^\+.*\b(32768|16384|8192)\b'` returns nothing

## 4. Middleware wrapper and complete-request counting — RED then GREEN

The wrapper is ours; only `ClearToolUsesEdit` comes from langchain. Do **not** subclass
`ContextEditingMiddleware` — in 1.0.3 the counter is a closure inside each wrapper body, so
"overriding" it means copying both upstream implementations.

- [ ] 4.1 Contract test: `ClearToolUsesEdit` accepts the constructor options used here, and `apply(messages, count_tokens=...)` **mutates the list in place and returns `None`** — assert the mutation, not just the signature, so an upgrade that switched to returning a new list (which would make the wrapper silently discard every reduction) fails here rather than in production
- [ ] 4.2 Failing test: the counter includes the system prompt — identical messages with and without a large `request.system_prompt` yield different counts
- [ ] 4.3 Failing test: the counter includes tool schemas — identical messages with and without `request.tools` yield different counts
- [ ] 4.4 Failing test: messages alone within budget but complete request over budget triggers reduction (the exact case upstream `"approximate"` misses)
- [ ] 4.5 Failing test: the counter never calls `get_num_tokens_from_messages` — a model stub whose tokenizer raises still counts successfully (no tiktoken dependency)
- [ ] 4.6 Failing test: after reduction the wrapper re-measures, and when still over budget logs a warning carrying the measured overage. Interpolate the numbers into the message string — `setup_logging` renders `%(message)s` only, so `extra={...}` passes `caplog` and emits nothing in production
- [ ] 4.7 Failing test: sync and async paths produce identical reductions on identical input (parity — they must delegate to one shared helper, not two copies)
- [ ] 4.8 Failing test (**the universal ceiling**): a preserved most-recent result from a tool named by *no* part of this change — a stand-in for an MCP or caller-supplied tool — is truncated to the per-result ceiling and marked partial, and the complete post-reduction request is within budget. `ClearToolUsesEdit` selects by recency across all tools, so a per-tool ceiling lapses the moment another tool is enabled
- [ ] 4.9 Failing test: an *exempted* retrieval result over the ceiling is truncated too — exemption from clearing is not exemption from the ceiling
- [ ] 4.10 Failing test: a surviving result within the ceiling is passed through byte-identical with no truncation marker
- [ ] 4.11 Contract test: `ModelRequest` exposes `system_prompt` (a review round asserted the field is named `system_message`; `dataclasses.fields()` on the pinned 1.0.3 says otherwise, and upstream's own `wrap_model_call` reads `system_prompt`). Pin it so a rename fails loudly here rather than at runtime
- [ ] 4.12 Failing test (**ordering**): the per-result ceiling is applied *before* the clearing decision. With one oversized newest result and several older reducible ones that together fit once it is clamped, assert the older results are **not** cleared
- [ ] 4.13 Watch 4.1–4.12 fail, then implement the `AgentMiddleware` wrapper: complete-request counter, universal per-result token ceiling over survivors applied first, delegation to upstream `ClearToolUsesEdit`, and the post-reduction re-measure

## 5. Middleware construction and its options — RED then GREEN

- [ ] 5.1 Failing test: the constructed edit carries `trigger` equal to the derived budget
- [ ] 5.2 Failing test: `keep` defaults to 3 and honours a config override
- [ ] 5.3 Failing test: `search_vectorstore_hybrid` is present in `exclude_tools` under default retrieval caps, and absent when the exemption is oversized
- [ ] 5.4 Failing test: `clear_tool_inputs` is `False` (the model must retain the record of its own call)
- [ ] 5.5 Failing test: the placeholder text states the result was cleared for context reasons and instructs the model not to re-request it
- [ ] 5.6 Watch 5.1–5.5 fail, then implement to green

## 6. Wire into the agent — RED then GREEN

- [ ] 6.1 Failing test in `tests/unit/test_react_agent_tool_budget.py`: with a known context window, `_build_static_middleware()` returns a non-empty middleware list (fails on `origin/dev`, which returns `[]`)
- [ ] 6.2 Failing test: with an undeterminable context window, `_build_static_middleware()` returns `[]` (behaviour identical to today)
- [ ] 6.3 Failing test: the middleware list reaches `create_agent(...)` — assert on what `_create_agent` is called with
- [ ] 6.4 Watch 6.1–6.3 fail, then make `_build_static_middleware` a thin call site delegating to `context_budget.py`
- [ ] 6.5 Verify the `base_react.py` diff is a handful of lines with no black reflow of surrounding code
- [ ] 6.6 Wiring test against the **real** agent, not helper stubs: construct `FASRCDocsAgent` with an overridden retrieval output ceiling and call budget, then assert the tool it builds emits results within that ceiling *and* the middleware it builds sized its exemption from those same values. A synthetic-input test alone would pass while production wiring used stale defaults

## 7. Request-local model overrides — RED then GREEN

- [ ] 7.1 Failing test: a request-local view built from a pipeline whose default has a large window, overridden to a model with a smaller one, derives its budget from the **overriding** model. Fails today twice over — `_get_model_context_window()` resolves the retained `default_provider`/`default_model` (`base_react.py:1597-1616`), and `_build_request_local_pipeline` never resets `_static_middleware`, so `refresh_agent` reuses the source's cached list (`base_react.py:1240-1250`)
- [ ] 7.2 Failing test: the view builds its own middleware rather than inheriting the source's cached `_static_middleware`
- [ ] 7.3 Failing test: building the view leaves the **shared** pipeline's budget and cached state untouched (the issue #86 invariant this function exists to hold)
- [ ] 7.4 Watch 7.1–7.3 fail, then pass provider/model into `_build_request_local_pipeline` (both are already in scope at `app.py:2130-2140`), assign them on the view, and reset `_static_middleware = None` beside the existing `_static_tools` reset. Keep `app.py` to assignments — the derivation stays in the tested helper
- [ ] 7.5 Verify the `app.py` diff adds no logic that diff-cover cannot reach

## 8. Behavioural tests — the acceptance criteria

- [ ] 8.1 Failing test: given an accumulated message list over the budget with more tool results than the preserve count, the oldest tool results are reduced before the model call
- [ ] 8.2 Failing test (**the bound itself**): after reduction, the *complete* request — system prompt, tool schemas and messages together — is within the budget whenever the non-reducible content fits. Assert on the measured post-reduction size, not merely that clearing occurred
- [ ] 8.3 Failing test (**the residual**): with many *small* tool rounds, so that cleared-message framing, retained tool-call arguments and placeholders alone cross the threshold, the runtime does not raise and logs the measured overage rather than reporting success. Record the measured per-round residue in the PR — this is the number that says whether removing whole paired rounds is ever needed
- [ ] 8.4 Failing test: when non-reducible content alone exceeds the budget, reduction clears everything it can and does not raise; the reactive handler covers the remainder
- [ ] 8.5 Failing test: a message list within budget is passed through untouched
- [ ] 8.6 Failing test (the boundary criterion): a run performing many document reads still returns a substantive answer, not the canned apology
- [ ] 8.7 Failing test: the N most recent tool results are not cleared — they retain original content when within the per-result ceiling, and the ceiling-truncated partial form when over it. Preservation exempts from *clearing*, never from the ceiling
- [ ] 8.8 Failing test: retrieval-tool results are not cleared regardless of age under default caps — same ceiling qualification as 7.7
- [ ] 8.9 Failing test: with retrieval caps raised past the exemption fraction, retrieval results become clearable and the bound still holds
- [ ] 8.10 Failing test: tool-call/tool-result pairing survives reduction — no dangling `tool_call_id`
- [ ] 8.11 Failing test: reduction is applied on a *later* model call when the budget is first exceeded mid-loop, not only before the loop
- [ ] 8.12 Watch 7.1–7.10 fail, then implement to green

## 9. Regression surface

- [ ] 9.1 `tests/unit/test_react_agent_context_overflow.py` passes unchanged — the reactive handler is retained as the last-resort net
- [ ] 9.2 Existing tool-budget tests (`test_react_agent_tool_budget.py`, `test_retriever_tool_budget.py`, `test_subclass_agent_memory_binding.py`, `test_active_memory_contextvar.py`) pass unchanged
- [ ] 9.3 The pre-loop budget in `_prepare_agent_inputs` is unmodified

## 10. Gate and pre-PR review loop

- [ ] 10.1 `bash scripts/gate.sh` exits 0 with patch coverage ≥ 80% against `origin/dev` (run bare, never piped; never `--no-verify`)
- [ ] 10.2 Run `/codex:adversarial-review --wait` on the branch; verify each finding against the code, fix what holds (TDD), push back with reasons on what does not
- [ ] 10.3 Re-run the adversarial review; repeat until a round returns zero findings or only nits (bound ~3–4 rounds)
- [ ] 10.4 File remaining nits as tracked issues rather than blocking the PR
- [ ] 10.5 Document the config seam in `docs/` alongside the existing `tool_budgets` documentation

## 11. PR

- [ ] 11.1 Open the PR with `gh pr create --repo fasrc/archi --base dev`; no `Co-Authored-By` or session trailers
- [ ] 11.2 PR body records the Phase 1 token accounting table, names option (c) as chosen, and states that (a) and (b) were rejected and why
- [ ] 11.3 PR body records the two corrections to the issue: the `DEFAULT_TOOL_BUDGETS` entry would be inert (no `enforce_budget` seam on `create_document_fetch_tool`), and source count is decoupled from context cost by construction
- [ ] 11.4 PR body states explicitly that the `fetch_catalog_document` call cap is deliberately out of scope, and why
- [ ] 11.5 PR body states which acceptance criteria could not be verified locally (goldenset runs need the deployment + VPN) and carries the pre-PR review summary
- [ ] 11.6 Request `@codex review` as a PR comment (never in the PR body)
- [ ] 11.7 Post-PR review loop: triage → fix (TDD) → reply in-thread per finding → push → re-request, until a clean round or only-nits-deferred; post a round log comment each round

## 12. Goldenset verification (needs deployment + FASRC VPN)

- [ ] 12.1 Re-execute the benchmark container in place: `docker start benchmarking-ragas-205`. Do **not** redeploy — that re-scrapes the corpus and changes the comparison
- [ ] 12.2 Re-derive degraded counts from the new results with the issue's script; confirm **0** rows with `status="degraded"` across three consecutive runs (5 → 1 does not satisfy this)
- [ ] 12.3 Confirm `question_94` returns a substantive answer citing `https://slurm.schedmd.com/salloc.html` rather than the apology
- [ ] 12.4 Report the result on #235 and note that the #205 group-6 manifest can be re-pre-registered

## 1. Branch and baseline

- [x] 1.1 Branch from `origin/dev` as `fix/issue-235-in-loop-context-budget` (never commit to `dev`)
- [x] 1.2 Confirm the two blind spots still exist on the branch base: `grep -n "DEFAULT_TOOL_BUDGETS" src/archi/pipelines/agents/base_react.py` shows only `search_vectorstore_hybrid`, and every `max_prompt_tokens` hit is inside `_prepare_agent_inputs`
- [x] 1.3 Confirm `_build_static_middleware` still returns `[]` and is passed to `create_agent(..., middleware=...)`
- [x] 1.4 Record the black-cleanliness of the intended insertion points in `base_react.py` before editing (a reflow of untouched code sinks patch coverage)

## 2. Enforced result ceilings — RED then GREEN

Prerequisite for every size claim later: without these, a "preserved" or "exempted" tool result
is unbounded and no floor arithmetic holds.

- [x] 2.1 Failing test: `fetch_catalog_document`'s **complete serialized return** is within the ceiling when `max_chars` exceeds it. Clamping the requested value alone is not enough — `_fetch_document` appends a path and up to 800 chars of metadata preview after the text (`local_files.py:530-539`), so a 4000-char request returns ~4800+
- [x] 2.2 Failing test: `max_chars=0` does **not** disable truncation (today `if max_chars and ...` at `uploader_app/app.py:769` returns the whole document)
- [x] 2.3 Failing test: negative and non-integer `max_chars` are treated as a request for the ceiling, not as "no limit"
- [x] 2.4 Failing test: a value *below* the ceiling is still honoured — clamping must not flatten legitimate smaller reads
- [x] 2.5 Failing test: the retriever tool's **complete serialized output** is clamped — with documents whose `title`/`url`/`resource_hash` metadata is pathologically large, the returned string stays within the ceiling even though `max_chars` bounds only `page_content` (`retriever.py:42-57`)
- [x] 2.6 Failing test: a normal retrieval result well under the ceiling is returned unmodified — the clamp must not truncate ordinary output
- [x] 2.7 Watch 2.1–2.6 fail, then implement both clamps, each ceiling configurable and defaulting to today's effective behaviour
- [x] 2.8 File the follow-up issue for the unclamped `max_chars` in `api_catalog_document` (`src/interfaces/uploader_app/app.py:761-770`), which this change deliberately leaves open for non-agent callers — filed as #260

## 3. Budget derivation helper — RED then GREEN

- [x] 3.1 Write failing unit tests for a new `src/archi/pipelines/agents/utils/context_budget.py`: budget = window − generation_reserve − counting_margin, with the reserve from the effective output cap (floor: percentage) and the margin a separate configurable term. The reserve cannot double as the margin — fully allocated to a 64 K cap on a 200 K window it leaves nothing to absorb approximation error, and the provider rejects that call before any later re-evaluation can correct it
- [x] 3.2 Write failing tests for fail-open: `None`, zero, negative, and non-integer windows all produce no middleware; likewise when reserve + margin would consume the whole window (non-positive budget). Assert only that no reduction is installed — **not** that behaviour matches a pre-change deployment, since the source clamps are unconditional
- [x] 3.3 Write failing tests for the three-layer config lookup (class default → `services.chat_app.context_editing` → `pipeline_config.context_editing`), later layers overriding earlier
- [x] 3.4 Write failing tests for invalid config values (non-numeric / out-of-range reserve, preserve count, exemption fraction): warn, use the default for that value, still install the bound
- [x] 3.5 Write a failing test that `enabled: false` installs no middleware
- [x] 3.6 Failing test: the exemption floor is computed as `tool_budget("search_vectorstore_hybrid") × per_result_**token**_ceiling` — both terms in the budget's own unit, measured with the same counter — reading the call budget through the existing `_tool_budgets()` lookup, **not** from the formatter's `max_documents`/`max_chars`, which no call site passes and no config path reaches
- [x] 3.7 Failing test: the exemption is dropped with a warning when that floor exceeds the configured fraction of the budget; retained when it does not
- [x] 3.8 Failing test: raising `services.chat_app.tool_budgets.search_vectorstore_hybrid` alone is enough to flip the exemption off — the check must track the runtime value, not a constant
- [x] 3.9 Failing test: a model whose effective output cap exceeds the percentage reserve gets a budget leaving at least that much free. Regression case from review: Claude Sonnet 4 is `context_window=200000, max_output_tokens=64000` (`anthropic_provider.py:20-29`) and `get_chat_model` applies it as `max_tokens` **when the caller sets none** (`:91-97`), so a flat 15% would permit a 170 K prompt alongside 64 K of requested generation against a 200 K window
- [x] 3.10 Failing test: the reserve reads the **effective** cap, not the metadata, on both branches — a configured `max_tokens` larger than `ModelInfo.max_output_tokens` wins, and declared metadata a provider never applies does not inflate the reserve. `LocalProvider` declares `max_output_tokens=8192` (`local_provider.py:184-192`) but passes it to neither constructor (`:94-125`), so the SUT budget is 27853 (percentage) when `extra_kwargs` sets no cap and smaller when it does. **Do not** assert "the local provider declares none" — an earlier revision claimed that and it is false
- [x] 3.11 Failing test: the exemption is bounded **by count and selects the earliest** — with more retrieval-named results present than the per-turn call budget (the surplus being post-budget synthetic refusals from `base_react.py:1827-1834` returned via `retriever.py:114-121`), the *first* N are exempt and the refusals after them are cleared. Assert the evidence survives and a refusal does not: selecting the newest N inverts this and protects refusals while clearing evidence
- [x] 3.12 Watch all of 3.1–3.11 fail for the right reason (module does not exist / returns nothing)
- [x] 3.13 Implement `context_budget.py` to the minimum that passes: config read + validation, budget derivation, exemption sizing, middleware construction
- [x] 3.14 Assert no hard-coded context length: `git diff origin/dev -- src/ | grep -E '^\+.*\b(32768|16384|8192)\b'` returns nothing

## 4. Middleware wrapper and complete-request counting — RED then GREEN

The wrapper is ours; only `ClearToolUsesEdit` comes from langchain. Do **not** subclass
`ContextEditingMiddleware` — in 1.0.3 the counter is a closure inside each wrapper body, so
"overriding" it means copying both upstream implementations.

- [x] 4.1 Contract test: `ClearToolUsesEdit` accepts the constructor options used here, and `apply(messages, count_tokens=...)` **mutates the list in place and returns `None`** — assert the mutation, not just the signature, so an upgrade that switched to returning a new list (which would make the wrapper silently discard every reduction) fails here rather than in production
- [x] 4.2 Failing test: the counter includes the system prompt — identical messages with and without a large `request.system_prompt` yield different counts
- [x] 4.3 Failing test: the counter includes tool schemas — identical messages with and without `request.tools` yield different counts
- [x] 4.4 Failing test: messages alone within budget but complete request over budget triggers reduction (the exact case upstream `"approximate"` misses)
- [x] 4.5 Failing test: the counter never calls `get_num_tokens_from_messages` — a model stub whose tokenizer raises still counts successfully (no tiktoken dependency)
- [x] 4.6 Failing test: after reduction the wrapper re-measures, and when still over budget logs a warning carrying the measured overage. Interpolate the numbers into the message string — `setup_logging` renders `%(message)s` only, so `extra={...}` passes `caplog` and emits nothing in production
- [x] 4.7 Failing test: sync and async paths produce identical reductions on identical input (parity — they must delegate to one shared helper, not two copies)
- [x] 4.8 Failing test (**the universal ceiling**): a preserved most-recent result from a tool named by *no* part of this change — a stand-in for an MCP or caller-supplied tool — is truncated to the per-result ceiling and marked partial, and the complete post-reduction request is within budget. `ClearToolUsesEdit` selects by recency across all tools, so a per-tool ceiling lapses the moment another tool is enabled
- [x] 4.9 Failing test: an *exempted* retrieval result over the ceiling is truncated too — exemption from clearing is not exemption from the ceiling
- [x] 4.10 Failing test: a surviving result within the ceiling is passed through byte-identical with no truncation marker
- [x] 4.11 Contract test: `ModelRequest` exposes `system_prompt` (a review round asserted the field is named `system_message`; `dataclasses.fields()` on the pinned 1.0.3 says otherwise, and upstream's own `wrap_model_call` reads `system_prompt`). Pin it so a rename fails loudly here rather than at runtime
- [x] 4.12 Failing test (**ordering**): the per-result ceiling is applied *before* the clearing decision. With one oversized newest result and several older reducible ones that together fit once it is clamped, assert the older results are **not** cleared
- [x] 4.13 Failing test (**state isolation**): the wrapper copies the message list before applying, so reduction never reaches conversation state. `apply` replaces list elements (`messages[idx] = ...model_copy(...)`) without mutating the `ToolMessage` objects, so a shallow `list(...)` copy suffices — verified on the pinned version: copied list ⇒ state keeps 4000 chars, view gets the placeholder. Assert state retains originals after a reduced call, and that a following turn is not served placeholder content
- [x] 4.14 Watch 4.1–4.12 fail, then implement the `AgentMiddleware` wrapper: complete-request counter, universal per-result token ceiling over survivors applied first, delegation to upstream `ClearToolUsesEdit`, and the post-reduction re-measure

## 5. Middleware construction and its options — RED then GREEN

- [x] 5.1 Failing test: the constructed edit carries `trigger` equal to the derived budget. Assert on the result of `build_context_middleware(...)`, never on a hand-written `ContextBudget` handed to the constructor — that only tests that a dataclass stores its argument. Assert **both** `edit.trigger` and `budget.trigger`: the edit's own gate reads the first, this wrapper's early return and shed loop read the second
- [x] 5.2 Failing test: `keep` defaults to 3 and honours a config override. Assert the literal `3`, not `DEFAULT_KEEP` — a constant asserted against itself passes whatever it is changed to. Cover the pipeline layer separately from the service layer: a factory that drops `pipeline_config` passes the service-layer test alone
- [x] 5.3 Failing test: `exclude_tools` is **not** used — the exemption is selected in our wrapper. Upstream's option exempts every message bearing the name, which cannot compose with the count bound in 3.11; asserting both would be asserting a contradiction. Needs a **non-default** tool name too: the test module's `RETRIEVAL` equals the constructor default, so a test using it cannot tell a forwarded name from an ignored one
- [x] 5.4 Failing test: `clear_tool_inputs` is `False` (the model must retain the record of its own call). Assert it behaviourally — inspect `tool_calls` after a real reduction, per spec.md:369 — not just as a constructor flag
- [x] 5.5 Failing test: the placeholder text states the result was cleared **to stay within the context window** and instructs the model not to re-request it (spec.md:318 — the test pins the spec's own words, so reword the spec first). Route it through the factory: the wording is a design decision, and a `placeholder` parameter would let a call site silently reinstate an uninformative marker
- [x] 5.6 Watch 5.1–5.5 fail, then implement to green. The RED is **two-phase** — adding the factory import fails the whole module's collection, which masks 5.5's wording failure. Watch 5.5 fail on its own first
- [x] 5.7 Pin the delegation, not two arithmetic results: a factory that inlines the reserve/margin arithmetic reproduces every asserted trigger while skipping `resolve_budget`'s irreducible-floor guard. Only a small window (32768) separates them

## 6. Wire into the agent — RED then GREEN

- [x] 6.1 Failing test in `tests/unit/test_react_agent_tool_budget.py`: with a known context window, `_build_static_middleware()` returns a middleware list whose **trigger value** is correct — not merely non-empty (fails on `origin/dev`, which returns `[]`). The value matters because nothing in the existing suite reaches this method: measured, the whole 1910-test suite calls it **zero** times, every test double overriding it
- [x] 6.2 Failing test: with an undeterminable context window, `_build_static_middleware()` returns `[]` (behaviour identical to today)
- [x] 6.3 Failing test: the middleware list reaches `create_agent(...)` — assert on what `_create_agent` is called with
- [x] 6.4 Watch 6.1–6.3 fail, then make `_build_static_middleware` a thin call site delegating to `context_middleware.build_context_middleware`. **Not `context_budget.py`** — it cannot import the middleware class without an import cycle (proved: `cannot import name 'ContextBudget' from partially initialized module`)
- [x] 6.5 Verify the `base_react.py` diff is a handful of lines with no black reflow of surrounding code
- [x] 6.6 Wiring test against the **real** agent, not helper stubs: construct `FASRCDocsAgent` with an overridden retrieval output ceiling and call budget, then assert the tool it builds emits results within that ceiling *and* the middleware it builds sized its exemption from those same values. A synthetic-input test alone would pass while production wiring used stale defaults. **Deviation:** the output ceiling is *not* config-overridable — `_update_vector_retrievers` calls `create_retriever_tool` without `max_result_chars`, so the tool always uses `DEFAULT_RETRIEVER_RESULT_CHARS`. The test overrides the call budget only, and asserts the coupling end-to-end instead: the real tool's actual oversized output is fed through the real `clamp_tool_results` at the resolved `per_result_tokens` and must come back **unmodified**. Mutation-checked — lowering `DEFAULT_PER_RESULT_TOKENS` to 1700 fails it

## 7. Request-local model overrides — RED then GREEN

- [x] 7.1 Failing test: a request-local view built from a pipeline whose default has a large window, overridden to a model with a smaller one, derives its budget from the **overriding** model. Fails today twice over — `_get_model_context_window()` resolves the retained `default_provider`/`default_model` (`base_react.py:1597-1616`), and `_build_request_local_pipeline` never resets `_static_middleware`, so `refresh_agent` reuses the source's cached list (`base_react.py:1240-1250`)
- [x] 7.2 Failing test: the view builds its own middleware rather than inheriting the source's cached `_static_middleware`
- [x] 7.3 Failing test: building the view leaves the **shared** pipeline's budget and cached state untouched (the issue #86 invariant this function exists to hold)
- [x] 7.4 Failing test on the **custom-provider** override path: `_create_provider_llm` builds a non-cached provider from the YAML `ProviderConfig` (`app.py:1640-1649`) while `_get_model_context_window()` re-resolves by name with no config (`base_react.py:1606-1616`) — for a custom model ID that yields no metadata and silently disables the middleware. Assert the view's budget comes from the bound model's resolved window, not a name lookup
- [x] 7.5 Watch 7.1–7.4 fail, then carry provider, model **and the resolved window/metadata** onto the view (all available at `app.py:2130-2140`) and reset `_static_middleware = None` beside the existing `_static_tools` reset. Keep `app.py` to assignments — the derivation stays in the tested helper
- [x] 7.6 Verify the `app.py` diff adds no logic that diff-cover cannot reach
- [x] 7.7 `model` and `context_window` MUST describe the **same** model. Measured on the real `_build_request_local_pipeline`: the naive fix (reset `_static_middleware`, rebuild) pairs the *override's* 64000 output cap with the *source's* 128000 window and derives a 57600 trigger for a model whose real window is 200000. Assert the trigger reflects the override's window, not the source's
- [x] 7.8 `rebuild_static_middleware()` alone is a silent no-op: `refresh_agent`'s `requires_refresh` reads `middleware` (`base_react.py:1281`) but compares only the toolset (`:1283-1288`), so a middleware-only change never reaches the compiled agent. Use `refresh_agent(force=True)` — as `app.py:227` already happens to — or extend `requires_refresh` to compare `_active_middleware` by identity

## 7A. The bound must actually install on a real deployment — BLOCKER

**Verified 2026-08-16, and it invalidates "ship it" for the whole change.** On the dev
deployment's own config, `_get_model_context_window()` returns `None`, so the factory
correctly returns `[]` and **nothing is installed**. Every group 5-8 test still passes.

```
local      palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4  -> None    (default_provider)
anthropic  claude-sonnet-4-6                     -> None    (the standby)
anthropic  claude-sonnet-4-20250514              -> 200000
```

Root cause: `base_react.py:1609` calls `get_provider(self.default_provider)` with no config,
so the window can only come from an **exact** match against a provider's hardcoded `ModelInfo`
list. `local` has an empty list; the Anthropic list holds four IDs and `claude-sonnet-4-6` is
not among them. Any self-hosted or newly-released model therefore yields `None`.

- [x] 7A.1 Decide the remedy with the user — this adds a config field, so it needs a spec delta
- [x] 7A.2 Operator override `services.chat_app.context_editing.context_window`, validated in
      `read_settings` and preferred over the derived window by `build_context_middleware`.
      Rides in on the `config` dict the builder already takes, so the call site is unchanged.
      Unlike every other setting it has **no default** — absent and invalid both mean "use the
      provider's" — and `positive_int` rejects `True`, which would otherwise be a 1-token window
- [x] 7A.3 Rejected alternatives recorded in design.md Decision 11: routing
      `_build_provider_config` into `_get_model_context_window` (its `models` are raw YAML
      strings and `get_model_info` crashes on them — `deploy/fasrc-dev/config.yaml:41-45`
      documents that exact crash), and widening the providers' hardcoded model lists (fixes
      today's two names, reintroduces the defect at the next release, never covers self-hosted)
- [x] 7A.4 An uninstallable limit now logs a warning naming the provider and model, so the
      difference between "protected" and "installed nothing" is visible. A deliberate
      `enabled: false` stays silent — a warning that fires when nothing is wrong is ignored
- [x] 7A.5 Set `context_window` in `deploy/fasrc-dev/config.yaml` before the goldenset run in
      group 12, or that run measures an agent with no in-loop limit installed. **Set to
      32768** — measured from both vLLM servers' `/v1/models` (`max_model_len`), not the model
      card, which claims 262144 for Qwen3.8-27B; the server's launch flag is what rejects a
      request. Paired with `keep: 2`, because 32768 sits below the 39375 threshold at which
      the retrieval exemption survives the irreducible-floor guard at the default `keep: 3`.
      Verified: `trigger=26215 reserve=4915 keep=2 exempt_count=2`, and 26215 + 4915 + 1638 =
      32768 exactly. The file is git-excluded, so this is not in the diff — it must be
      re-applied on any host that redeploys

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

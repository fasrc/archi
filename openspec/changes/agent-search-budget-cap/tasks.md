## 1. RunMemory: per-turn counter state

- [x] 1.1 Add `self._tool_call_counts: Dict[str, int] = {}` to `RunMemory.__init__` in `src/archi/pipelines/agents/utils/run_memory.py`.
- [x] 1.2 Add `RunMemory.bump_tool_call_count(tool_name: str) -> int` that increments and returns the new count.
- [x] 1.3 Add `RunMemory.tool_call_count(tool_name: str) -> int` read-only accessor.
- [x] 1.4 Run pyright on `run_memory.py`; confirm no new errors against the baseline.

## 2. BaseReActAgent: budget helper + class default

- [x] 2.1 Add class constant `DEFAULT_TOOL_BUDGETS: Dict[str, int] = {"search_vectorstore_hybrid": 2}` next to `DEFAULT_RECURSION_LIMIT = 50` in `src/archi/pipelines/agents/base_react.py`.
- [x] 2.2 Add `BaseReActAgent._tool_budgets()` helper mirroring `_recursion_limit()`: reads `pipeline_config.tool_budgets`, then `services.chat_app.tool_budgets`, then `DEFAULT_TOOL_BUDGETS`. Returns `Dict[str, int]`. Cache on first call.
- [x] 2.3 Add `BaseReActAgent._consume_tool_budget(tool_name: str) -> Optional[str]`. Returns `None` if `active_memory is None` (fail-open). Looks up the cap; if no cap, returns `None`. Otherwise: bumps the counter on `active_memory`; returns `None` if still within budget; returns the formatted "Search budget exhausted:" string if at or over budget.
- [x] 2.4 Run pyright on `base_react.py`; confirm no new errors against the baseline.

## 3. Retriever tool: enforce_budget callback

- [x] 3.1 Add keyword-only argument `enforce_budget: Optional[Callable[[], Optional[str]]] = None` to `create_retriever_tool` in `src/archi/pipelines/agents/tools/retriever.py`.
- [x] 3.2 At the top of the `_retriever_tool` closure body (before the `store_tool_input` block), call `enforce_budget()` if provided. If it returns a non-None string, log at INFO and return that string immediately, skipping the retriever invocation and document storage.
- [x] 3.3 Run pyright on `retriever.py`; confirm no new errors against the baseline.

## 4. Wire up at the agent registration site

- [x] 4.1 In `src/archi/pipelines/agents/cms_comp_ops_agent.py` at the `create_retriever_tool(...)` call near line 307, pass `enforce_budget=lambda: self._consume_tool_budget("search_vectorstore_hybrid")`.
- [x] 4.2 Run pyright on `cms_comp_ops_agent.py`; confirm no new errors against the baseline.

## 5. Unit tests: retriever-tool budget

- [x] 5.1 Create `tests/unit/test_retriever_tool_budget.py`.
- [x] 5.2 Test that calls 1 and 2 with budget=2 pass through to a `MagicMock(spec=BaseRetriever)` and increment the counter.
- [x] 5.3 Test that call 3 returns the synthetic string starting with `"Search budget exhausted:"` and does NOT invoke the fake retriever (`retriever.invoke.call_count == 2`).
- [x] 5.4 Test that constructing a fresh `RunMemory` resets the counter (proves per-turn reset).
- [x] 5.5 Test that two tools with different names sharing the same `RunMemory` have independent counters.
- [x] 5.6 Test that `create_retriever_tool` called without `enforce_budget` invokes the retriever on every call (backward compatibility).

## 6. Unit tests: agent-loop reset between turns

- [x] 6.1 Create `tests/unit/test_react_agent_tool_budget.py`.
- [x] 6.2 Implementation note: instead of stubbing `BaseChatModel` + running through LangGraph, the test file uses a lightweight `BaseReActAgent` subclass that skips LLM/prompt init and tests `_consume_tool_budget` + `start_run_memory()` directly. This covers every spec scenario (config lookup, default cap, fail-open without memory, per-turn reset, recursion-handler retry preserves counter) at the unit-test layer; the full LangGraph end-to-end is the live deploy step in Group 7.
- [x] 6.3 Test the three-layer config lookup (pipeline_config overrides chat_app, chat_app overrides class default, class default applies when config absent, tool without configured budget returns None, caching, invalid value ignored).
- [x] 6.4 Test `_consume_tool_budget` returns `None` when `active_memory is None` (fail-open between turns).
- [x] 6.5 Test that after the cap, `_consume_tool_budget` returns a synthetic string starting with `"Search budget exhausted:"`, naming the tool and `limit=N`.
- [x] 6.6 Test that `start_run_memory()` between turns resets the budget; and that reusing the same `active_memory` (as the recursion-handler retry does) preserves the counter.

## 7. Verification and integration

- [x] 7.1 Run the full unit test suite. Confirm all existing tests still pass and the new tests in (5) and (6) pass. *(Result: 264 passed, 18 new tests pass, 1 pre-existing unrelated failure in `test_ingestion_pipeline_isolation::test_loader_returns_content`.)*
- [x] 7.2 Run pyright on all four touched source files in one invocation. Confirm zero new diagnostics vs baseline. *(Result: 31 errors total across all 4 files, all pre-existing; 0 reference any added symbol verified via grep of `enforce_budget|tool_call_count|_consume_tool|_tool_budgets|DEFAULT_TOOL_BUDGETS|bump_tool`.)*
- [x] 7.3 Restart the chatbot container so the new image picks up the change. Send the failing question from the prompt-tuning session: *"what's the fastest way to move 2 TB from my Cannon scratch directory to S3?"*. *(Result via /v1 API: 6040 ms total, 0 tool calls, no "Note on Interruption" wrapper, closing block present. Down from 125+ s pre-fix. Caveat: the /v1 default agent did not invoke `search_vectorstore_hybrid` at all, so the cap never actively fired. Active-firing verification needs a UI selection of a search-using agent variant.)*
- [x] 7.4 Send a benign easy-tier question (e.g., *"How do I load a Python module on Cannon?"*) and confirm normal behavior is unchanged. *(Result via /v1 API: 4782 ms, 0 tool calls, response contains `module load`, closing block present, no synthetic string. No regression.)*

## 8. Commit and PR

- [x] 8.1 Commit on `fix/agent-search-budget-cap` with a message that describes the cap + the prompt-tuning context that drove it. *(commit `38a2c55b`)*
- [x] 8.2 Push the branch and open a PR against `fasrc/archi:dev`. Include the verification evidence from task 7 in the PR description. *(PR #21: https://github.com/fasrc/archi/pull/21)*

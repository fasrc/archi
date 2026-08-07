## Context

The FASRC Archi agent uses a LangGraph ReAct loop (`BaseReActAgent`) where the LLM (Qwen3.5-35B-A3B-GPTQ-Int4) chooses tools to call. The `search_vectorstore_hybrid` tool is registered through the `create_retriever_tool` factory in `src/archi/pipelines/agents/tools/retriever.py`. The agent loop has a single backstop — `recursion_limit=50` at `BaseReActAgent.DEFAULT_RECURSION_LIMIT` — which fires `GraphRecursionError` and dumps the partial state via `_handle_recursion_limit_error` with a user-visible "Best Possible Answer / Note on Interruption" wrapper.

Three prompt-only attempts to stop the model from looping (`fasrc-archi` v10, v11, v12) all failed in opposite directions: v10 didn't search, v11/v12 looped to the limit. The model's stop-after-N discipline cannot be elicited via prompt.

`RunMemory` (`src/archi/pipelines/agents/utils/run_memory.py`) is already constructed fresh once per user turn at `BaseReActAgent.start_run_memory()` (called from `_prepare_agent_inputs`, which is the single chokepoint for all four entry paths: `invoke`, `stream`, `astream`, and the recursion-error retry blocks reuse the same instance — correct, since those are still the same turn). Per-turn state belongs there.

## Goals / Non-Goals

**Goals:**
- Cap `search_vectorstore_hybrid` at N=2 calls per user turn. Third+ calls return a synthetic "search budget exhausted" string without invoking the retriever.
- The model treats the synthetic result as a normal `ToolMessage` and finalizes cleanly — no `GraphRecursionError`, no recursion-handler wrapper.
- Counter resets to 0 at the start of every new user turn, automatically.
- Cap value is configurable per pipeline / chat-app config, with a sensible code default.
- The pattern is reusable: adding a budget for `search_local_files` later is a one-line config addition, not new code.
- Backward-compatible: callers of `create_retriever_tool` that don't pass `enforce_budget=` behave exactly as today.

**Non-Goals:**
- Lowering the framework `recursion_limit=50`. That's a separate concern and remains as the second-line backstop.
- Capping MCP tools or any retriever factory other than `create_retriever_tool`.
- Refactoring `_handle_recursion_limit_error` or any of its callers.
- Changing the `fasrc-archi` prompt (v13 lands separately, after this code).
- Hard-failing the agent. Over-budget calls return a string the model can act on; they do not raise.

## Decisions

### D1. Counter lives on `RunMemory`

Per-turn state already has a single lifecycle hook (`start_run_memory`), and `RunMemory` already tracks per-turn-scoped data (notes, tool runs, documents). Adding `_tool_call_counts: Dict[str, int]` and two methods (`bump_tool_call_count`, `tool_call_count`) is the smallest possible surface change. Counter resets automatically because `__init__` reinitializes the dict every turn.

**Alternatives considered:** (a) Instance variable on `BaseReActAgent` — would require a manual reset hook in every entry path. (b) LangGraph state field — would require thread-through across every tool call, more invasive.

### D2. Injection via closure callback on `create_retriever_tool`

`create_retriever_tool` already accepts `store_docs` and `store_tool_input` callbacks (retriever.py:69-71). Adding a third callback `enforce_budget: Optional[Callable[[], Optional[str]]]` follows the established pattern. The callback returns `None` to allow the call or a string to short-circuit. The closure invokes it at the top of `_retriever_tool` before any work.

**Alternatives considered:** (a) LangChain middleware — `_build_static_middleware` currently returns `[]` and the only sketch in `cms_comp_ops_agent.py:271-281` is commented out. Adopting middleware would mean threading per-turn state via the LangGraph config and would create a second tool-call interception path. (b) Generic decorator stack alongside `@require_tool_permission` — `require_tool_permission` reads from Flask session, not per-turn state, so the decorator would still need per-turn state passed in at build time. Same shape as the closure callback, with an extra decorator layer.

### D3. The over-budget tool result is a plain string returned by the closure

LangGraph automatically wraps tool returns as `ToolMessage(content=...)`. Returning a string from the closure is the simplest path. No need to construct messages manually or insert anything into the message history.

The string starts with `"Search budget exhausted:"` (distinct from the existing "No documents found in the knowledge base for this query." sentinel). It names the tool, the limit, the available fallbacks (answer from existing chunks / disclose no-coverage), and an explicit "do not call this tool again on this turn." Chunks retrieved by earlier calls are already in the message history as `ToolMessage` content — preserved automatically by LangGraph.

### D4. Tool-name passed at registration time, bound in the closure

The agent registers the tool with a known name (`"search_vectorstore_hybrid"`). Binding that name into the `enforce_budget` lambda at registration (rather than passing it through the closure on each call) keeps the closure signature `Callable[[], Optional[str]]` — cleaner type, easier to mock in tests, and the counter key is determined at build time.

```python
enforce_budget=lambda: self._consume_tool_budget("search_vectorstore_hybrid")
```

### D5. Config lookup mirrors `_recursion_limit()`

A new helper `_tool_budgets()` on `BaseReActAgent` reads `pipeline_config.tool_budgets` first, then `services.chat_app.tool_budgets`, then a class-level default `DEFAULT_TOOL_BUDGETS = {"search_vectorstore_hybrid": 2}`. Cached on first call. Same lookup order, same caching, same error semantics as `_recursion_limit()` at `base_react.py:1389`.

**Alternatives considered:** (a) Hardcoded — would require code change to tune. (b) Agent-spec frontmatter — wrong place; budgets are runtime guards, not behavioral prompts. (c) Env var — too global; we want per-pipeline tuning.

### D6. Reset is implicit, not explicit

No new reset hook. The counter is a dict on `RunMemory`, and `RunMemory.__init__` runs once per `start_run_memory()` call, which runs once per call to `_prepare_agent_inputs()`. All four entry points (`invoke`, `stream`, `astream`, plus the recursion-error handlers that reuse `active_memory` — correctly, since they're still the same turn) get correct semantics for free.

## Risks / Trade-offs

[Model ignores the synthetic result and calls again anyway] → The closure short-circuits on every subsequent call too; each over-budget call is now a fast string return rather than a retriever round-trip. Even pathological 10× calling adds milliseconds, not seconds. If empirical testing shows the model keeps calling after seeing the same response twice, we can escalate (e.g., raise from the closure to trigger a clean LangGraph exit), but start with the soft return.

[Counter key collision across tools sharing a name] → Names like `"search_vectorstore_hybrid"` are unique within an agent's tool registry. Two tools built with the same `name=` would share the counter, which is the desired behavior (the budget is per *capability*, not per *closure instance*).

[`create_retriever_tool` called outside an agent (e.g., `tests/smoke/tools_smoke.py`)] → `enforce_budget` defaults to `None` and the closure behaves identically to today. No backward-compat break.

[`self.active_memory is None` between turns] → `_consume_tool_budget` fails open (returns `None`) in that case, matching how `_store_documents` and `_store_tool_input` already early-out when memory is absent (`base_react.py:1112`, `1126`).

[Recursion-limit handler interacts with budget] → The recursion handler reuses `self.active_memory`. If the counter is already at the cap when the handler retries with trimmed inputs (`base_react.py:1470`), the retry will hit the synthetic string immediately. That is correct — the budget is per turn, not per LangGraph invocation, and the handler is still inside the same turn.

[Cap too low / too high] → The default `2` is the right starting point per the prompt-tuning telemetry (the working v8 prompt called search at most once per turn in nearly every successful response; the failed v11/v12 looped 8–50). The config knob lets us widen to `3` without code change if grading data shows the model would benefit from one more reformulation pass.

## Migration Plan

No data migration; no config required.

**Deploy:**
1. Land code (this change). No agent behavior change for tools other than `search_vectorstore_hybrid`.
2. Restart the chatbot container so the new image loads. No config edits needed; the class default applies.
3. Validate on the "2 TB Cannon → external Globus" question that previously took 125+ s on v12: expected total tool calls ≤ 2 (the cap), wall-clock < 30 s, no "Note on Interruption" wrapper.
4. Once verified, switch the agent prompt to `fasrc-archi-v13.md` (a follow-on change) that drops the now-redundant prompt-level stop language.

**Rollback:** Single-file revert of any of the four touched code files restores prior behavior. The change is additive: removing `enforce_budget=...` from the call site disables the budget without removing the helper code. No DB or persistent-state migration to undo.

## Open Questions

- **Should `search_local_files` get the same cap on day one?** Cms-comp-ops agent lists it. The failing prompt variants only abused vector search, so we ship just `search_vectorstore_hybrid` first; extend to `search_local_files` once we observe a loop on it. Adding it later is a one-line `DEFAULT_TOOL_BUDGETS` update plus the same `enforce_budget=` wiring at its registration site.
- **Should the synthetic string be model-tuned?** The exact wording is a hyperparameter. We start with the proposed string (see D3) and iterate if grading shows the model misinterpreting it (e.g., apologizing instead of answering from context).

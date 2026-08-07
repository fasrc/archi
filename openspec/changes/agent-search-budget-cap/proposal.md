## Why

Qwen3.5-35B-A3B-GPTQ-Int4 ignores prompt-level "stop after N searches" instructions. Three prompt variants tested on the FASRC Archi agent (v10, v11, v12) all failed: v10 did not search at all and fabricated answers from training data, while v11 and v12 looped `search_vectorstore_hybrid` 8–50 times per question, hit the framework `recursion_limit=50`, took 125+ seconds wall-clock, and ended with a `_handle_recursion_limit_error` wrapper that produced an ugly "Best Possible Answer / Note on Interruption" output. The model's stop-after-N discipline cannot be elicited via prompt — it has to be enforced at the tool layer.

## What Changes

- **Per-turn budget on tool calls**, enforced by the tool itself. After the configured cap is reached for a tool in a single user turn, the tool returns a synthetic "search budget exhausted" string instead of invoking the underlying retriever. The model treats this as a normal tool result and finalizes naturally — no `GraphRecursionError`, no recursion-handler wrapper.
- **Default cap of 2 for `search_vectorstore_hybrid`**, with the structure to extend to other expensive tools (e.g., `search_local_files`) by adding entries to a config map. No change to those other tools in this proposal.
- **Per-turn reset** by piggybacking the counter on `RunMemory`, which is already constructed fresh once per user turn at the single chokepoint `BaseReActAgent.start_run_memory()`. No new turn-lifecycle hook needed.
- **Configurable** via `pipeline_config.tool_budgets` → `services.chat_app.tool_budgets` → class default, mirroring the existing `_recursion_limit()` lookup pattern.

## Capabilities

### New Capabilities
- `agent-tool-budgets`: Per-turn enforcement of a per-tool call budget on the ReAct agent loop, with a synthetic over-budget tool result that lets the model finalize cleanly. Configurable cap; reusable across multiple expensive tools.

### Modified Capabilities

None. The existing `cli-dev-mode` and `dev-mode-mounts` specs are unaffected.

## Impact

- **Code**: `src/archi/pipelines/agents/utils/run_memory.py` (counter state + accessor methods), `src/archi/pipelines/agents/base_react.py` (budget helper + class default), `src/archi/pipelines/agents/tools/retriever.py` (`create_retriever_tool` gains an optional `enforce_budget` callback), `src/archi/pipelines/agents/cms_comp_ops_agent.py` (wiring at the tool registration site).
- **Config**: New optional key `services.chat_app.tool_budgets` (a `Dict[str, int]`). Absence falls back to the class default. No required config changes for existing deployments.
- **Tests**: New unit tests for the tool-closure budget mechanic and for the agent-loop reset between turns. No changes to existing tests expected.
- **Behavior for downstream agents**: Backward-compatible. `create_retriever_tool` called without `enforce_budget=` behaves identically to today (e.g., the existing smoke tests at `tests/smoke/tools_smoke.py`).
- **Prompt strategy follow-on**: Once the cap is structural, the `fasrc-archi` prompt can stop carrying "do not call this tool again" language. That prompt change is out of scope for this proposal; it lands separately as a v13 agent spec after this code ships.
- **Not in scope**: lowering the framework `recursion_limit=50` default, applying the budget to MCP tools or other retriever factories, and refactoring `_handle_recursion_limit_error` (the recursion-limit handler stays as the second-line backstop).

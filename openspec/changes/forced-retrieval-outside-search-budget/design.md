## Context

`BaseReActAgent` enforces a per-turn, per-tool call cap inside the retriever tool closure:
`create_retriever_tool(..., enforce_budget=lambda: self._consume_tool_budget("search_vectorstore_hybrid"))`
(`fasrc_docs_agent.py:225-234`, `cms_comp_ops_agent.py:323-333`). The closure (`tools/retriever.py:163-190`)
runs `enforce_budget()`, then `store_tool_input()`, then the retriever, then `store_docs()`.

`_inject_forced_retrieval` (`fasrc_docs_agent.py:236-304`, twin in `cms_comp_ops_agent.py`) calls
`tools[0].invoke({"query": question})` on that same closure before the model's first turn and
prefills an `AIMessage` tool call + `ToolMessage` with a `forced_search_<uuid>` id. Two things follow:

1. The counter in `RunMemory._tool_call_counts` is bumped to 1 before the model has done anything;
   with the default cap of 2 the model gets one search, then refusals.
2. `store_tool_input` queues `{"query": question}` in `RunMemory._pending_tool_inputs_by_name`.
   Streamed model tool calls arrive with empty args and `resolve_tool_input` pops the *oldest*
   pending entry, so call N is recorded with the input of call N-1 (`run_memory.py:67-100`); the
   forced `AIMessage` never streams, so nothing ever claims the first entry.

The context-editing middleware sizes its retrieval exemption from the same budget
(`base_react.py:1614` → `context_middleware.py:468` → `context_budget.py:569-592`): the earliest
`exempt_count = budget` results named `search_vectorstore_hybrid` are protected from clearing, and
the exemption is dropped entirely when `(budget + keep) × per_result_tokens` exceeds a third of the
trigger. On dev (32768 window → trigger 19661, allowance 6553; `keep: 1`) the shipped derivation is
`(2 + 1) × 2100 = 6300 ≤ 6553` (`config/environments/dev.yaml:70-77`).

## Goals / Non-Goals

**Goals:**
- The model keeps the full configured budget of its own searches regardless of the forced round.
- The forced round is recorded under its own tool-call id; model calls resolve their own arguments.
- Grounding guarantees are unchanged: the forced round still runs, still feeds `store_docs`
  (source links), still prefills the completed tool round, still fails open on retriever errors.
- Budget semantics are stated once, in a spec, and covered by tests on both agents.

**Non-Goals:**
- Raising or re-keying `DEFAULT_TOOL_BUDGETS`; adding config keys.
- Re-sizing the context-editing exemption to budget + 1 (needs archi-config changes; see Risks).
- De-duplicating identical queries (the model does not re-issue the verbatim question — that was
  an artifact of the shifted trace arguments).
- Changing what the retriever tool renders (separate change: matched-chunk rendering).

## Decisions

**D1 — A second, unbudgeted tool instance for the forced round** (chosen) vs. (a) raising the
cap to 3, (b) a "forced" flag threaded through the closure, (c) exempting the first call by name.
The closure's public contract is `(query: str) -> str`; threading a flag means a new closure
signature or module state. Raising the cap keeps the forced round masquerading as a model call,
re-sizes the exemption to 3 and silently drops it on the 32k derivation (`(3+1)×2100 = 8400 > 6553`).
Building the tool twice from the same retriever is explicit, needs no change to
`create_retriever_tool`, and is directly testable: the budgeted instance's counter must read 0
after injection. Both instances share `store_docs`, so `source_documents` behaves as today.

**D2 — Register the forced call id in run memory instead of queueing a pending input.** The
forced `AIMessage` never passes through `record_tool_calls_from_message` (it is input state, not a
node output), so `store_tool_input` can only ever leave an orphan. `RunMemory.record_tool_call(
call_id, "search_vectorstore_hybrid", {"query": query})` records the input where every other call's
input lives (`tool_inputs_by_id`), and a non-empty input is never matched by `record_tool_input`'s
"fill the first empty entry" scan, so the model's own call keeps its own arguments.

**D3 — Leave the exemption at `budget`.** Today the two exempt results already are the forced
round and the first model search (earliest-by-position selection, `context_budget.py:606-629`); this
change does not alter which results are protected, it only adds a *third* successful result that is
clearable under pressure. Protecting it too would require `retrieval_call_budget = budget + 1`, i.e.
`(3 + 1) × 2100 = 8400`, which exceeds the 6553 allowance and would drop the exemption to zero — a
regression — unless `context_editing.exemption_fraction` is raised to ≥ 0.43 (8400 / 19661) in the
pinned archi-config. That re-derivation is tracked as a follow-up, not bundled.

**D4 — Fail-open stays fail-open.** A retriever error still returns the messages unchanged. If an
agent reaches `_inject_forced_retrieval` without an unbudgeted instance (only possible for a
subclass that builds its tools elsewhere), it falls back to the budgeted tool with a warning that
names the cost, so the grounding guarantee is never silently lost.

## Risks / Trade-offs

- [One more real retriever call per turn, ≈10 s on dev] → only when the model chooses to search a
  second time; today that attempt already happens and is refused.
- [One more ≤2100-token tool result in context, not exempt] → clearable by the existing bound when
  the prompt exceeds the trigger; the exemption follow-up (D3) removes this if adopted.
- [`tool_inputs_by_id` now contains a `forced_search_*` id] → consumers treat ids opaquely; the
  chat app builds events from streamed calls only. Covered by the registration test.
- [Golden-set comparability] → runs after this change form a new era; note in the report.
- [Existing tests that build the forced round with a bare fake tool] → `_make_agent` in
  `test_forced_retrieval.py` sets `_vector_tools` only; it gains the unbudgeted instance so the
  cases keep exercising the production path rather than the fallback.

## Migration Plan

No configuration or schema changes. Ships with the next dev redeploy; rollback is reverting the
PR. Verification after deploy: a Phoenix trace shows forced + two model-initiated
`LlamaIndexHierarchicalRetriever` spans before any "Search budget exhausted" tool output, and the
`agent_traces` `tool_start` args equal the retriever spans' `input.value`.

## Open Questions

- Whether to adopt the exemption re-derivation (`exemption_fraction ≥ 0.43` at 32k, or a smaller
  `keep`) in archi-config — decided separately with the deploy owner.

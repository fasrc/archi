## Why

A per-request provider/model override in the chat app is applied by mutating the **process-wide shared** pipeline (`ChatWrapper` is a singleton — `src/interfaces/chat_app/app.py:2612`), and the override is held for the entire streamed turn. Two overlapping requests therefore race: request B captures request A's override as its "original" LLM, and when B's `finally` runs it restores *A's override* onto the shared pipeline — leaving every later user pinned to A's model and its `extra_kwargs` (e.g. `enable_thinking`).

This is reachable from the A/B comparison UI, which deliberately starts two streaming requests in parallel. PR #85 (`a8d61bcb`) made the swap atomic and restored on completion, which closed *permanent* poisoning from a single request, but it cannot close the concurrent case: the window is the whole turn, not the swap. A mutex is not an acceptable fix — serializing the two requests defeats the parallel comparison the A/B path exists for.

The same shared-state hazard extends past the LLM: the retrieved-document sink and the reported model name are both instance state on the same singleton, so an override bleeds those too (see design D1a/D3).

## What Changes

- Add a **request-local execution path**: when a request carries a provider/model override, build a per-request pipeline view bound to the override LLM and stream on that, instead of mutating `self.archi.pipeline.agent_llm`.
- Allow the `archi` orchestrator's `stream`/`invoke`/`astream` to execute against a **caller-supplied pipeline** instead of only `self.pipeline`, so the chat app can drive a request-local pipeline through the existing vectorstore/`PipelineOutput` plumbing rather than around it.
- Rebuild the request-local pipeline's **tools** so their document/tool-budget callbacks are bound to the request-local instance. The cached static tools close over `self._store_documents`, so sharing them would route an overridden request's retrieved documents into the *shared* pipeline's run memory — the same bleed, moved from the LLM to the sources.
- Keep the reported model name (`current_model_used`) request-local on the override path **and thread it through the persistence path**, so an overridden request no longer writes the shared field that later default requests read back, while the conversation row still records the model that actually answered.
- **Remove** the shared-pipeline swap/restore dance (`_swap_pipeline_llm` / `_restore_pipeline_llm` and the `override_applied` / `override_original_llm` / `override_original_model` bookkeeping) from the override path once the request-local path replaces it.
- Leave the **non-override path byte-for-byte behaviourally unchanged** — it continues to use the shared pipeline with no extra construction cost.

Not a breaking change: the HTTP contract, the streamed event shapes, and the default (no-override) behaviour are all preserved.

## Capabilities

### New Capabilities
- `request-local-llm-override`: a request-time provider/model override MUST affect only the request that carried it — never the shared pipeline, never a concurrent request, and never a subsequent request — while concurrent overridden requests still run in parallel without serialization.

### Modified Capabilities
<!-- None. No existing spec in openspec/specs/ covers chat-app LLM override behaviour. -->

## Impact

- **`src/interfaces/chat_app/app.py`** — the override block in `ChatWrapper.stream()` (~2003-2031), the stream call site (~2045), the `finally` restore (~2528-2535), the module-level `_swap_pipeline_llm` / `_restore_pipeline_llm` helpers (~163-192), and the reported-model reads in `insert_conversation` (~1398/1409/1423), `_finalize_result` (~1779), and the response payload (~2484).
- **`src/archi/archi.py`** — `stream()` / `invoke()` / `astream()` gain an optional caller-supplied pipeline; `_prepare_call_kwargs` and `_ensure_pipeline_output` are reused unchanged.
- **`src/archi/pipelines/agents/base_react.py`** — the per-request pipeline view relies on `refresh_agent(force=True)` rebuilding `self.agent` from `self.agent_llm` (~1188-1222, `model=self.agent_llm` at ~1244) and on `tools` rebuilding from `_static_tools` (~1159-1169). One small change expected: a lock around the lazy MCP-tools build (~1200-1204), which is currently an unguarded check-then-assign.
- **Tests** — new concurrency regression coverage under `tests/unit/` proving no cross-request and no subsequent-request bleed, and that two overridden requests overlap rather than serialize.
- **Risk**: the per-request pipeline view shares tool/prompt/vectorstore objects with the shared pipeline by design (construction cost); the design must state exactly which attributes are rebound per request and confirm none of the shared internals are mutated during a turn.
- No config, deployment, dependency, or public-API surface changes.

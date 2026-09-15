## Context

`ChatWrapper` is instantiated once per process (`src/interfaces/chat_app/app.py:2612`), so `self.archi`, `self.archi.pipeline`, and `self.current_model_used` are all shared across every concurrent request.

The current override path (`app.py:2003-2031`) calls `_swap_pipeline_llm(self.archi.pipeline, override_llm)` (`app.py:163-185`), which rebinds `pipeline.agent_llm` and calls `refresh_agent(force=True)` to rebuild the compiled agent from that LLM (`base_react.py:1244` passes `model=self.agent_llm` into `_create_agent`). The restore happens in the stream's `finally` (`app.py:2528-2535`), i.e. **after the whole turn**.

PR #85 made the swap itself atomic, which fixes single-request poisoning. It cannot fix the concurrent case, because the hazard is the *duration* of the swap, not its atomicity:

```
A: swap(default → X); orig_A = default
B: swap(X       → Y); orig_B = X          # B captures A's override as "original"
A: restore(orig_A = default)              # shared pipeline back to default
B: restore(orig_B = X)                    # shared pipeline pinned to A's override, forever
```

The single consumption point is `self.archi.stream(...)` at `app.py:2045`, and `archi.stream()` (`src/archi/archi.py:91-101`) hardcodes `self.pipeline`. So today there is no way to run a turn against anything other than the shared pipeline — which is the root constraint this design removes.

A mutex is explicitly rejected: the override spans the entire streamed turn, so a lock would serialize the A/B comparison UI's two parallel requests and defeat the feature that path exists for.

## Goals / Non-Goals

**Goals:**
- An overridden request never writes shared cross-request state (`pipeline.agent_llm`, `pipeline.agent`, `current_model_used`).
- Concurrent overridden requests each keep their own LLM for the whole turn and still run in parallel.
- The non-override path keeps using the shared pipeline with zero added construction cost.
- Delete the swap/restore machinery once the request-local path replaces it, so the hazardous pattern cannot be reintroduced by copy-paste.

**Non-Goals:**
- Making the pipeline fully thread-safe. `BaseReActAgent.start_run_memory()` (`base_react.py:1345`) writes `self._active_memory` on the shared instance every run — a **pre-existing** cross-request hazard independent of LLM overrides. This design incidentally isolates it for *overridden* requests, but does not fix it for concurrent *default* requests. See Open Questions.
- Changing the HTTP contract, streamed event shapes, or the A/B UI.
- Introducing per-request pipeline construction for the common (non-override) path.

## Decisions

### D1: Request-local **pipeline view** via shallow copy, not a rebuilt pipeline

Build the per-request pipeline with `copy.copy(shared_pipeline)`, then rebind on the copy only:
- `view.agent_llm = override_llm`
- `view.agent = None`, `view._active_tools = []`, `view._active_middleware = []` (force a rebuild rather than inheriting the shared compiled agent)
- `view._active_memory = None` (never share the shared instance's run memory)
- `view._static_tools = None`, plus `view._vector_tools = None` / `view._vector_retrievers = None` where present — **see D1a; this is not optional**

then call `view.refresh_agent(force=True)` to compile an agent bound to the override LLM.

`copy.copy` gives the view its own `__dict__`, so every assignment above is invisible to the shared pipeline, while genuinely stateless collaborators (prompts, config dicts, catalog service, vectorstore connector) are shared by reference at near-zero cost.

**Invariant — state it in the code:** the request-local path performs **zero writes to the shared pipeline instance**, with exactly one carved-out exception (the idempotent MCP memoization in D6). The guard is the `is`-identity assertion on the shared pipeline's `agent` and `agent_llm` in the tests.

*Alternatives considered:*
- **Full `_create_pipeline_instance()` per overridden request** — correct but rebuilds LLMs, prompts, and re-initializes the MCP client on every A/B keystroke. Rejected on cost.
- **Thread the LLM through call kwargs** (`pipeline.stream(..., llm=override)`) — would require changing the `stream`/`invoke`/`astream` signature of *every* pipeline and every code path that reads `self.agent_llm` (`base_react.py` reads it in at least 8 places). Rejected on blast radius.
- **Lock around the swap** — rejected by the issue: serializes the A/B pair.

### D1a: The view MUST rebuild its tools — sharing them re-creates the bug

Static tools are cached on the instance (`_static_tools`, rebuilt via `rebuild_static_tools()`; the `tools` property returns `list(self._static_tools)` — `base_react.py:1159-1169`) and each tool is constructed with **callbacks bound to the instance that built it**: `store_docs=self._store_documents`, `store_tool_input=self._store_tool_input` (`fasrc_docs_agent.py:135-136,144-145,160`; `cms_comp_ops_agent.py:218,227,243`) and `enforce_budget=lambda: self._consume_tool_budget(...)` (`fasrc_docs_agent.py:231`). Those callbacks resolve `self.active_memory` → `self._active_memory` on the **source** pipeline (`base_react.py:1309-1311`).

So if the view shares `_static_tools`, an overridden request's agent calls tools that record documents into the **shared** pipeline's run memory. Two consequences, both the exact bug class #86 exists to close, merely relocated from the LLM to the sources:
1. The overridden request's own memory stays empty, so `finalize_output` returns no `source_documents` and the UI renders "Link unavailable" (`app.py:1772`).
2. A concurrent default request owns `shared._active_memory`, so the overridden request's retrieved documents and tool inputs are silently attributed to *its* answer, and `_consume_tool_budget` is charged against the wrong memory.

This is invisible to a vectorstore-only test, because `_vector_tools` happen to be rebuilt per run against the running instance (`_prepare_agent_inputs` → `_update_vector_retrievers`, `fasrc_docs_agent.py:222-235`). Only the cached *static* tools leak — and the live agent spec selects exactly those (`fetch_catalog_document`, `search_local_files`, `search_metadata_index`).

**Decision:** reset `_static_tools = None` on the view so `refresh_agent(force=True)` rebuilds tools bound to the view. Cost: one static-tool build per overridden request, accepted for correctness.

*Alternative considered:* move `_store_documents` / `_store_tool_input` / `_consume_tool_budget` off `self` and onto a `contextvars.ContextVar` holding the active memory. Strictly better — it would fix the adjacent default-request memory race too (see Open Questions) — but it touches every tool constructor and every agent subclass. Rejected as scope for this issue; recorded as the follow-up.

### D2: `archi.stream()` accepts a caller-supplied pipeline

Add an optional keyword (e.g. `pipeline=None`) to `archi.stream()` / `invoke()` / `astream()` that defaults to `self.pipeline`. This keeps the vectorstore injection (`_prepare_call_kwargs`) and the `PipelineOutput` validation (`_ensure_pipeline_output`) in one place, so the request-local path goes *through* the orchestrator rather than around it.

`supports_stream()` must be evaluated against the pipeline actually being used, not unconditionally against `self.pipeline`.

*Alternative:* have the chat app call `view.stream(...)` directly and re-implement vectorstore injection + output validation at the call site. Rejected — duplicates orchestrator logic and would silently skip `_ensure_pipeline_output`.

### D3: Keep the reported model request-local — including the persistence path

`self.current_model_used` is shared instance state with the same race. On the override path, resolve the reported model into a local variable and do not write the shared attribute.

**But the reported model is not confined to the response.** It is read in four places (`app.py`): the streamed response payload (`:2484`), and three times inside `insert_conversation` (`:1398`, `:1409`, `:1423`) to populate the persisted `model_used` column. `insert_conversation` (`:1361`) is reached from `_finalize_result` (`:1779`), which the stream calls at `:2413`. Neither takes a model parameter.

Today the override path sets the shared field at `:2020` *before* finalization, so an overridden turn is persisted with the override's model, and `:2536` restores it after. The existing comment at `:2534-2535` says so explicitly.

Therefore: simply *stopping* the write would make every overridden turn persist the **default** model — silently mislabelling exactly the A/B comparison rows the override feature exists to collect, while a test that only asserts "`current_model_used` is unchanged" passes.

**Decision:** thread the request-local model through. Add an optional `model_used: Optional[str] = None` parameter to `insert_conversation` and `_finalize_result`, defaulting to `self.current_model_used` so the non-override and non-streaming (`__call__`, `app.py:1852/1861`) paths are byte-for-byte unchanged, and pass the override's `f"{provider}/{model}"` from the request-local stream path. The acceptance test asserts the *persisted* value equals the override, not merely that the shared field is untouched.

### D6: Make the lazy MCP build idempotent and thread-safe

`refresh_agent` populates MCP tools with an unguarded check-then-assign — `if self._mcp_tools is None: self._mcp_tools = list(self._build_mcp_tools() or [])` (`base_react.py:1200-1204`) — and `_build_mcp_tools` overwrites `self.mcp_client` (`:1259-1267`). Flask serves these requests on threads (`Flask.run` defaults `threaded=True`; `src/bin/service_chat.py:47` passes no override), and `update_config` rebuilds the pipeline at the top of every turn (`app.py:505` → `archi.py:34`), resetting `_mcp_tools` to `None`. So the A/B pair — two parallel overridden requests, the very thing this change enables — can both see `None`, both initialize an MCP client, and leak the loser's sessions on the shared `AsyncLoopThread`. The live agent selects `mcp`, so this is reachable.

**Decision:** guard the `_mcp_tools is None` check-and-build with an instance-level `threading.Lock` created in `BaseReActAgent.__init__`, so any number of concurrent view builds yields exactly one `initialize_mcp_client()` per pipeline instance.

Filling `_mcp_tools` is the **one permitted write to shared state** (D1's carve-out): it is a pure, idempotent memoization of a value that does not vary per request, it leaves `agent` / `agent_llm` / `_active_*` untouched, and it is behaviour-neutral. Note what was rejected: an earlier draft of this design told the implementer to call `refresh_agent()` on the shared pipeline before copying. That is **wrong and must not be reintroduced** — `_prepare_agent_inputs` calls `refresh_agent(extra_tools=self._vector_tools)` (`base_react.py:1360`), so after any prior turn the shared `_active_tools` includes the vector tool; a bare `refresh_agent()` builds a shorter toolset, trips `requires_refresh` (`:1211-1216`), and **replaces the shared `self.agent` with one that has no retrieval tool** (`:1217-1221`) — breaking concurrent default requests, which dereference `self.agent` only later at `:375`.

### D4: Delete the swap/restore helpers

Once D1-D3 land, `_swap_pipeline_llm` / `_restore_pipeline_llm` (`app.py:163-192`) and the `override_applied` / `override_original_llm` / `override_original_model` locals plus their `finally` block have no remaining callers. Remove them in the same change — leaving them invites reintroducing the exact hazard. The `finally` keeps its cursor/connection cleanup.

### D5: Error handling preserves the existing contract

Override-LLM construction currently distinguishes `ValueError` (→ HTTP 400, return) from other exceptions (→ warning event, fall through to the default LLM). Preserve both. A failure while building the *view* (copy or `refresh_agent`) must be treated as the second case — warn and fall back to the shared pipeline — and, because nothing shared was ever mutated, needs no unwind.

## Risks / Trade-offs

- **Shallow copy shares mutable internals** → The view must explicitly reset every attribute representing *per-run* state **or holding a collaborator bound to the source instance**: `agent`, `_active_tools`, `_active_middleware`, `_active_memory`, `_static_tools`, `_vector_tools`, `_vector_retrievers`. Task list pins these; a test asserts the shared pipeline's `agent_llm` and `agent` are identical objects before and after an overridden turn.
- **A future pipeline attribute holding per-run state or a `self`-bound callback would silently be shared** → This is the sharpest risk in the change: it is invisible in review and only shows up as cross-request contamination under concurrency. Mitigate by resetting in one clearly-named helper (`_build_request_local_pipeline`) whose comment states the real invariant — *any attribute that is per-run state, or that closes over a bound method of the pipeline, must be rebuilt on the view, never shared* — so the reset list has one obvious home.
- **Per-request `refresh_agent(force=True)` costs an agent recompile, and now a static-tool rebuild too** → The recompile matches what the current code already pays per overridden request (the swap also forces a refresh). The static-tool rebuild (D1a) is genuinely new cost on the override path, accepted because sharing them is incorrect; the non-override path pays nothing.
- **Only the *static* tool leak is fixed** → `_vector_tools` were already rebuilt per run, which is why a vectorstore-only test would show green while catalog/local-file tools contaminate shared memory. The regression test must exercise a **static** tool, not just retrieval.
- **`copy.copy` on a pipeline subclass with a custom `__copy__`/`__reduce__`** → Only `BaseReActAgent` subclasses are in play and none define one today; the test suite exercises the real class.
- **Adjacent unfixed hazard**: concurrent *default* (non-overridden) requests still share `_active_memory` on the shared pipeline. Out of scope here; must be filed separately so it is not lost.

## Migration Plan

Pure in-process behavioural fix — no data, config, or schema migration. Ships with the normal deploy (the container runs a non-editable install, so the change is live only after a redeploy). Rollback is a revert of the single commit range; nothing persists state that would survive it.

## Open Questions

- Should the shared-pipeline `_active_memory` race (concurrent *default* requests) be fixed by making the request-local view the path for **every** request, not just overridden ones? That would unify the two paths and close the adjacent hazard, at the cost of an agent recompile per request. Deferred: out of scope for this issue, and the cost question needs a measurement. **Action: file a follow-up issue** rather than widening this change.
- The structurally better fix for the whole class is to stop hanging per-run state off `self` at all — hold the active `RunMemory` in a `contextvars.ContextVar` so `_store_documents` / `_store_tool_input` / `_consume_tool_budget` resolve the *caller's* memory regardless of which instance built the tool. That would make D1a's tool rebuild unnecessary and fix default-request contamination in the same stroke, but it touches every tool constructor and agent subclass. Fold into the same follow-up issue.

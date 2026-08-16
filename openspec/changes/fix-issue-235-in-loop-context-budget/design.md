## Context

`BaseReActAgent` builds its agent with `create_agent(model, tools, middleware, system_prompt)`
(`base_react.py:1316-1321`, LangChain 1.0.3). Three context-budget mechanisms already exist:

| # | Mechanism | Where | Runs |
|---|---|---|---|
| 1 | Per-tool **call-count** budget | `:81`, `:1741`, `:1809` | inside the retrieval tool |
| 2 | Prompt **token** budget | `:1445-1480` (in `_prepare_agent_inputs`) | **once, before** `agent.invoke()` |
| 3 | Reactive overflow handler | `:1877-1963` | **after** the provider rejects the prompt |

The defect is the gap between #2 and #3. Mechanism #2 does the correct arithmetic — reads
`_get_model_context_window()`, subtracts 15%, compresses — but only over `history_messages`,
before the loop starts. Every `ToolMessage` produced *during* the loop bypasses it. Mechanism
#1 does run in-loop, but it counts calls, and it is wired to exactly one tool.

### Token accounting (issue Phase 1)

Derived from the code paths and the caps they enforce, plus the three committed benchmark
artifacts under `bench_out/`.

| Contributor | Per unit | Cap | Max share of a 32 K window |
|---|---|---|---|
| `search_vectorstore_hybrid` result | `max_documents=4` × `max_chars=800` + headers ≈ 3.6 KB ≈ **900 tok** (`retriever.py:71-72`) | **2 calls** (`DEFAULT_TOOL_BUDGETS`) | ~1.8 K — **5.5%** |
| `fetch_catalog_document` result | `max_chars` **default** 4000 + path + metadata preview (≤800) ≈ **1.05 K tok** (`local_files.py:121,477-539`) | **none** — only `recursion_limit=50` | ~24 K — **73%** |
| system prompt + tool schemas | measured per request (see Decision 3) | — | varies with config and toolset |

~20–24 `fetch_catalog_document` calls at the default size fill a 32 K window;
`recursion_limit=50` permits ~24 tool/model round trips. The two numbers meet, which is the
whole failure.

**`max_chars=4000` is a default at three layers and a ceiling at none.** `_fetch_document`
exposes `max_chars` as a *tool argument* (`local_files.py:506`) and forwards the
model-supplied value to `catalog.get_document`, which passes it as a query parameter to
`api_catalog_document` (`uploader_app/app.py:761-770`), where it is read with
`request.args.get("max_chars", default=4000, type=int)` and applied as
`if max_chars and len(text) > max_chars`. There is no upper clamp anywhere on that path, and
because `0` is falsy, `max_chars=0` disables truncation entirely and returns the **whole
document**. So a single tool result is model-controlled and effectively unbounded. Any
preserve-count safety argument that rests on ~1.05 K per retained result is invalid until that
ceiling is enforced — see Decision 8.

**The source-count story is falsified.** Across the three runs, `ok` rows reached **67** and
**65** recorded sources while `question_94` overflowed with 18. The decoupling is structural:
`retriever.py:134-138` passes the *full* document list to `store_docs` but formats only
`docs[:max_documents]` for the model, so `sources_metadata` counts what was retrieved and
recorded, never what the model saw. Retrieval's context cost is capped at ~900 tok/call
regardless of source count.

**The artifacts cannot attribute tokens per call.** `sources_trunc_content` is a fixed 300-char
preview (5400/18 = 300 exactly) and a degraded row's `messages` array holds only the final
apology. The accounting above is therefore a *bound* derived from the caps, not a sample —
which is the stronger claim, and needs no live instrumentation.

**Retrieval worked on the reproduction case.** `question_94`'s `sources_metadata` contains the
correct reference URL (`https://slurm.schedmd.com/salloc.html`) in all three runs. The evidence
was retrieved and recorded; the loop then drowned it.

### Constraints

- Do not touch retrieval scoring, weights, or the goldenset bank — #205 is in flight on the
  same subsystem.
- `base_react.py` is ~2200 lines; keep the insertion point small and black-clean so a reflow
  does not sink patch coverage.
- New logic must be unit-testable to clear `diff-cover --fail-under=80`.

## Goals / Non-Goals

**Goals:**

- Bound accumulated tool content by **tokens** on every in-loop model call, counting the
  complete request the provider will receive.
- Derive the budget from `_get_model_context_window()`; no hard-coded context length.
- Make every term of that budget an *enforced* ceiling rather than a default, so the arithmetic
  is a bound and not an expectation.
- Preserve the answer: recent reads stay at full fidelity, grounding evidence is kept while
  keeping it is provably cheap.
- Make the canned apology genuinely abnormal rather than merely rarer.
- Fail open in every unknown or misconfigured case; fail *toward the bound* when a protective
  exemption would undermine it.

**Non-Goals:**

- Capping `fetch_catalog_document` call *counts* (see Decision 5). Its result *size* is capped
  (Decision 8) because the bound depends on it.
- Clamping `max_chars` server-side in `api_catalog_document` for non-agent callers — filed as
  a follow-up (Decision 8).
- Eliminating the residual case where irreducible content alone exceeds the budget; that is
  bounded and measured, not removed (see Risks).
- Removing or weakening the reactive `_handle_context_overflow` path — it stays as the
  last-resort net.
- Reducing agent latency or the number of tool round trips.
- Any change to retrieval, scoring, or the benchmark harness.

## Decisions

### Decision 1 — Clear the oldest tool results in-loop, rather than refusing or shrinking new ones

The issue poses a two-way fork. Both options were rejected in favour of a third.

| Option | Behaviour | Verdict |
|---|---|---|
| (a) Cumulative budget + graceful stop | refuse new tool content once consumed; tell the model to answer from what it holds | **Rejected.** The model keeps requesting reads it cannot receive, and answers from partial evidence with no signal about which evidence it lost. Wastes the remaining recursion budget on refused calls. |
| (b) Adaptive per-read shrinking | lower effective `max_chars` as the accumulated total grows | **Rejected.** Degrades *every* read including the decisive one, and the shrink schedule is a second, independent notion of the budget — precisely what the issue warns against. |
| **(c) Clear oldest tool results** | keep the N most recent at full fidelity; replace older ones with a placeholder | **Chosen.** |

(c) drops what the model has already reasoned over and keeps what it is currently reasoning
about. Recent reads stay complete, so the answer is computed on whole evidence rather than
uniformly degraded fragments. It bounds tokens directly, satisfying the issue's
"correlation, not causation" constraint.

### Decision 2 — Use the installed `ContextEditingMiddleware`, not a hand-rolled accumulator

LangChain 1.0.3 — already pinned, already the source of `create_agent` — ships
`ContextEditingMiddleware` with the `ClearToolUsesEdit` strategy, mirroring Anthropic's
`clear_tool_uses_20250919`. Its `wrap_model_call` hook fires on **every** model call with the
accumulated message list in hand: exactly the position mechanism #2 leaves vacant.

Alternative considered: write our own `AgentMiddleware` from scratch. Rejected — it would
reimplement tool-call/tool-result pairing, placeholder substitution, and re-clear idempotency,
all of which are the parts most likely to produce a malformed message sequence that the
provider rejects. Adopting the upstream component means the risky logic is maintained
elsewhere and our diff carries budget derivation, wiring, and one overridden token counter
(Decision 3) — not a message rewriter.

Alternative considered: `SummarizationMiddleware`. Rejected — it spends an extra model call
per compaction and introduces summarization loss into the evidence chain that the citation
layer depends on.

**Behavioural note.** `ClearToolUsesEdit.apply` mutates `request.messages` in place; the graph
**state** retains full history. Each model call therefore receives a freshly pruned *view* and
nothing is permanently destroyed. State still grows across the run, but it never reaches the
provider — which is the property that matters.

### Decision 3 — Count the **complete** request approximately, and reserve output tokens explicitly

`ContextEditingMiddleware` offers two counting modes, and **neither is correct here**:

- `"model"` calls `request.model.get_num_tokens_from_messages(system + messages, tools)` —
  complete, but **unguarded on every model turn**. The pre-loop budget already wraps that same
  call in `try/except` (`:1509`) because it is exception-prone, and the SUT's model name
  (`palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4`) is unknown to tiktoken. An exception there breaks
  every model call in the loop — a worse failure than the one being fixed.
- `"approximate"` calls `count_tokens_approximately(messages)` — safe, but counts **messages
  only**: no system prompt, no tool schemas (`use_usage_metadata_scaling` defaults to `False`).

An earlier revision of this design took `"approximate"` and argued the 15% margin would absorb
the omission at "~1–2 K". That estimate was unsupported. System prompts are configurable, tool
schemas grow with the selected toolset and any dynamically loaded MCP tools, and the margin
shrinks with the window — so the approximate count can sit below the trigger while the real
request exceeds the model's window. The argument is withdrawn.

**Take the complete count without the tiktoken risk.** `count_tokens_approximately` accepts a
`tools=` argument, and `ModelRequest` exposes both `system_prompt` and `tools` inside
`wrap_model_call`. A thin subclass of `ContextEditingMiddleware` overriding only the counter —
`count_tokens_approximately([SystemMessage(system_prompt)] + messages, tools=request.tools)` —
is complete *and* has no tokenizer dependency. All clearing logic stays upstream (Decision 2);
the subclass swaps one function.

**The safety margin then becomes what it actually needs to be: an output-token reserve.**
`ModelInfo.context_window` (`providers/base.py:40`) is the **total** sequence length, not an
input budget — 200000 for Anthropic models, a static 32768 for the local provider
(`local_provider.py:188`). vLLM enforces prompt + generation against `max_model_len`, which is
exactly why the error phrasing the detector already matches reads "the model's context length
is only N, resulting in a maximum input length of M". With prompt overhead now counted
exactly, the margin no longer has to absorb anything unknown, so it is documented and
configured as the generation reserve. Default stays 15% to keep one convention with the
pre-loop budget.

### Decision 4 — Exempt `search_vectorstore_hybrid` results, but only while the exemption is provably small

Retrieval results are the citation-bearing grounding evidence, and under today's defaults they
are bounded: 4 documents × 800 chars per call under a 2-call budget ≈ 1.8 K tokens, a 5.5%
floor on a 32 K window. Protecting them costs a small fixed floor and keeps the answer
grounded; the unbounded accumulator is what gets cleared.

Alternative considered: exempt nothing. Rejected — under a deep read loop the forced initial
retrieval is the oldest tool result and would be the first cleared, leaving the model to answer
about documents it can no longer see. (Citations themselves would survive either way:
`store_docs` records documents into `RunMemory`, so `source_documents` and links are
independent of the message stream. The exemption is about the *model's* ability to cite
accurately, not the citation plumbing.)

**But the numbers above are configurable defaults, not invariants.** `max_documents`,
`max_chars`, and the `search_vectorstore_hybrid` call budget are all overridable, and a blanket
exemption turns a raised retrieval budget into a second unbounded floor sitting outside the
clearing strategy — reintroducing the very defect this change fixes, at a different tool.

So the exemption is **conditional and self-checking**: at construction the runtime computes the
exempt floor from the values actually in force (retrieval call budget × per-call ceiling) and
compares it against the derived budget. Above a configurable fraction (default one third), it
logs a warning naming the offending values and **drops the exemption**, making retrieval
results clearable like any other. The design fails toward the bound holding, never toward a
silent floor.

### Decision 5 — Do not add a `fetch_catalog_document` call cap in this change

The issue calls a `DEFAULT_TOOL_BUDGETS` entry "the cheapest partial mitigation". It is
actually **inert**. `_consume_tool_budget` is never invoked by the framework; it reaches a tool
only through an explicit `enforce_budget=` callback, and that parameter exists on exactly one
factory (`retriever.py:76`, wired at `fasrc_docs_agent.py:231` and
`cms_comp_ops_agent.py:330`). `create_document_fetch_tool` (`local_files.py:477-485`) accepts
no such parameter, so `_tool_budgets()` would merge the entry and nothing would ever consult it.

Beyond that, a call cap cannot bound tokens — one 4000-char read is worth ~20 short ones — so
it could not satisfy the requirement on its own even if wired.

A cap would address *latency* and *answer shallowness* (a 50-step read-everything loop), which
are real but separate concerns. Doing it properly means either building the missing
`enforce_budget` seam into `create_document_fetch_tool` or adopting upstream
`ToolCallLimitMiddleware`. Out of scope; recorded here so the next person does not re-derive it.

### Decision 6 — New logic in a helper module; `base_react.py` gets a thin call site

`_build_static_middleware` becomes a small override that delegates to a new module under
`src/archi/pipelines/agents/utils/`. The module owns: reading and validating config through
the three-layer lookup, deriving the trigger from a context window, and constructing the
middleware list. That keeps the budget arithmetic directly unit-testable, mirrors the
`config_fingerprint.py` precedent named in `CLAUDE.md`, and keeps the `base_react.py` diff to
a handful of lines in a file large enough that a black reflow would sink patch coverage.

### Decision 7 — `clear_tool_inputs=False` and a custom placeholder

`ClearToolUsesEdit` defaults to leaving the originating tool call's arguments on the assistant
message. Keep that: the model still sees *that* it fetched hash X, only not the content. With
the arguments cleared as well, the model has no record of the call and re-fetches the same
document, spinning until `recursion_limit` — converting an overflow into a timeout.

The default placeholder `[cleared]` is uninformative. Replace it with text that states the
result was cleared to stay within the context window and directs the model not to re-request
it, so the model's next step is to answer rather than retry.

### Decision 8 — Enforce a `max_chars` ceiling on `fetch_catalog_document`

The preserve-count safety argument requires a *bounded* retained result, and today there is no
bound: `max_chars` is a tool argument the model chooses, forwarded unclamped all the way to
`api_catalog_document`, where `max_chars=0` disables truncation and returns the whole document
(see Context). Three preserved results are only ~3.2 K tokens if 4000 is a ceiling; if the
model asks for 200 000 characters, reduction is defeated and the loop overflows *more* readily
than today, because the middleware will have cleared the older evidence first.

`create_document_fetch_tool` therefore clamps the effective value to an enforced maximum
(configurable, defaulting to the current 4000) before it reaches the catalog client, treating
non-positive and non-integer inputs as "use the ceiling" rather than "no limit". This is
in-scope because the bound in the spec cannot hold without it.

The server-side gap is broader than this change — `api_catalog_document` will still honour an
unbounded `max_chars` for any other caller — so the endpoint clamp is filed as a separate
follow-up rather than smuggled into an agent-context PR.

Alternative considered: remove `max_chars` from the tool signature entirely so the model cannot
influence it. Rejected — a smaller value is a legitimate and useful request, and removing the
argument changes the tool contract the prompt documents. Clamping preserves the useful
direction and closes the harmful one.

## Risks / Trade-offs

- **The approximate counter is still an approximation** → It now counts the complete request
  (system prompt, tool schemas, messages) rather than messages alone, so the error is a
  chars-per-token estimate rather than a missing term. The generation reserve absorbs it, and
  reduction is idempotent across turns, so an underestimate at one model call is re-evaluated
  at the next.

- **Irreducible content can still exceed the budget** → `keep` preserves the N most recent
  results regardless of size, and exempt retrieval results are not clearable, so a floor
  remains. With Decision 8's enforced ceiling that floor is ~3.2 K at N=3, and Decision 4 drops
  the exemption when it grows too large — but the residual is real, not zero. The spec states
  the bound as reducible-content reduction with an explicit residual scenario, and the reactive
  handler is retained to cover it. A test asserts the *complete* post-reduction request size,
  so the residual is measured rather than assumed.

- **The model may answer from cleared-away evidence it half-remembers** → Recent reads stay at
  full fidelity and retrieval results are exempt, so the grounding chain is intact. The
  placeholder explicitly tells the model the content is gone rather than letting it silently
  infer.

- **State keeps growing even though the model view is pruned** → Memory only, no provider
  impact. A long-lived thread grows message state as it does today; nothing regresses.

- **Middleware changes the message list the provider sees** → Only `ToolMessage.content` is
  replaced; `tool_call_id` pairing is preserved, so no dangling tool call is produced. Covered
  by a test asserting call/result pairing survives reduction.

- **The default becomes active on the production chat path, not just the benchmark** → That is
  intended (`base_react.py` is the shared base class), and it is the safer direction: today
  that path can return the apology mid-conversation. The config seam allows disabling it.

## Migration Plan

No data migration and no config migration: absent configuration yields the protective default,
so existing deployments gain the bound on redeploy without edits. Because running config is
seeded into Postgres from `config.yaml` at deploy, any operator override requires
`redeploy.sh`, not a container restart.

Rollback is a config flag (`services.chat_app.context_editing.enabled: false`), which restores
exactly today's behaviour without reverting code.

## Open Questions

- **Preserve count default.** N=3 (the upstream default) is the starting point. Whether the
  agent answers better with more recent reads retained is an empirical question for the
  goldenset, not something to settle here. The value is config-overridable so it can be swept
  as an arm without a code change.

- **Goldenset verification cannot run in the development environment.** Two acceptance criteria
  — three consecutive runs at 0 degraded, and `question_94` returning a substantive answer
  citing `slurm.schedmd.com/salloc.html` — need the deployment and the FASRC VPN at ~50 min
  per run. Every other criterion is reachable locally. Re-execute the existing benchmark
  container in place (`docker start benchmarking-ragas-205`); do **not** redeploy, which
  re-scrapes the corpus and changes the comparison out from under it.

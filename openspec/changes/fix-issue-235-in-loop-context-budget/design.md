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
| `search_vectorstore_hybrid` result | `max_documents=4` × `max_chars=800` + headers ≈ 3.6 KB ≈ **900 tok** at ordinary metadata sizes (`retriever.py:71-72`; the header itself is uncapped — see Decision 8) | **2 calls** (`DEFAULT_TOOL_BUDGETS`) | ~1.8 K — **5.5%** |
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
- Preserve the answer: recent reads are never cleared, grounding evidence is kept while
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
| **(c) Clear oldest tool results** | keep the N most recent unreduced; replace older ones with a placeholder | **Chosen.** |

(c) drops what the model has already reasoned over and keeps what it is currently reasoning
about. Recent reads stay complete, so the answer is computed on whole evidence rather than
uniformly degraded fragments. It bounds tokens directly, satisfying the issue's
"correlation, not causation" constraint.

### Decision 2 — Use the installed `ContextEditingMiddleware`, not a hand-rolled accumulator

LangChain 1.0.3 — already pinned, already the source of `create_agent` — ships
`ContextEditingMiddleware` with the `ClearToolUsesEdit` strategy, mirroring Anthropic's
`clear_tool_uses_20250919`. Its `wrap_model_call` hook fires on **every** model call with the
accumulated message list in hand: exactly the position mechanism #2 leaves vacant.

Alternative considered: write our own `AgentMiddleware` including its own message rewriter.
Rejected — that would reimplement tool-call/tool-result pairing, placeholder substitution, and
re-clear idempotency, all of which are the parts most likely to produce a malformed message
sequence that the provider rejects.

**Split the component along its real seam.** `ContextEditingMiddleware` is two things of very
unequal value: `ClearToolUsesEdit.apply` — the message rewriter, which is the risky, subtle,
well-tested part — and `wrap_model_call` / `awrap_model_call`, which are ~20 lines of
boilerplate that pick a token counter and call `edit.apply(request.messages,
count_tokens=...)`. Only the first is worth depending on.

So: **use upstream's `ClearToolUsesEdit`, supply our own middleware wrapper.** The wrapper is
where the complete-request counter (Decision 3) and the convergence check (Decision 9) belong,
and both sync and async paths delegate to one shared helper so they cannot drift. This is
composition, not subclassing — the `ContextEdit` protocol takes `count_tokens` as a parameter
precisely so a caller can supply one.

The dependency this creates is narrow and explicit: the `ClearToolUsesEdit(...)` constructor
options and the `apply(messages, *, count_tokens)` signature. A contract test constructs and
applies it directly, so a langchain upgrade that changes either fails loudly in the unit suite
rather than silently disabling the bound.

Alternative considered: subclass `ContextEditingMiddleware` and override the counter. **Not
possible** — in langchain 1.0.3 the counter is a closure built inside each wrapper body, not a
method, so "overriding the counter" means copying both wrapper implementations. That is
strictly worse than owning a wrapper we wrote deliberately: same duplication, but disguised as
inheritance, and with upstream's sync/async bodies to keep in sync on every upgrade.

Alternative considered: `SummarizationMiddleware`. Rejected — it spends an extra model call
per compaction and introduces summarization loss into the evidence chain that the citation
layer depends on.

**Behavioural note — and the copy step it depends on.** `ClearToolUsesEdit.apply` performs
`messages[idx] = tool_message.model_copy(...)`: it replaces **list elements** and never mutates
the `ToolMessage` objects themselves. Measured on the pinned version — after `apply`, the
original object still holds its 4000 characters while the list slot holds `[cleared]`.

That distinction is the whole safety argument, and it cuts both ways. Because the objects are
untouched, a **shallow** `list(...)` copy is sufficient to protect the graph state — verified:
with a copied list, state keeps 4000 characters and the view gets the placeholder. But without
that copy, passing the state's own list means the replacement lands in state, permanently
replacing prior results with placeholders in subsequent turns, streamed events, and persisted
traces. An earlier revision of this document asserted "the graph state retains full history"
as though it were a property of the edit; it is a property of a copy step that revision never
specified. The wrapper copies the list before applying and forwards the copy. No deep copy is
required, and specifying one would be waste. Each model call therefore receives a freshly pruned *view* and
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
`wrap_model_call`. Counting
`count_tokens_approximately([SystemMessage(system_prompt)] + messages, tools=request.tools)`
is complete *and* has no tokenizer dependency. Because we own the wrapper (Decision 2), this
counter is simply the function passed to `ClearToolUsesEdit.apply` — no upstream code is
overridden or copied to obtain it.

**The safety margin then becomes what it actually needs to be: an output-token reserve —
and a percentage is not a safe way to size it.** `ModelInfo.context_window`
(`providers/base.py:40`) is the **total** sequence length, not an input budget: 200000 for
Anthropic models, a static 32768 for the local provider (`local_provider.py:188`). vLLM
enforces prompt + generation against `max_model_len`, which is exactly why the error phrasing
the detector already matches reads "the model's context length is only N, resulting in a
maximum input length of M".

A flat 15% is demonstrably insufficient for models that declare a large output limit. Claude
Sonnet 4 is configured `context_window=200000, max_output_tokens=64000`
(`anthropic_provider.py:20-29`), and `get_chat_model` passes that straight through as
`max_tokens` when the caller does not set one (`anthropic_provider.py:91-97`). A 15% reserve
would permit a **170 K** prompt while the provider is simultaneously asked to allow **64 K** of
generation — 234 K against a 200 K window. The provider rejects the request before this
middleware's trigger is ever reached, so the mitigation would be inert exactly where the window
is largest.

So the reserve is `max(percentage_floor, effective_output_limit)`. **But "effective" has to mean
the cap the model call actually carries, not the number in the metadata** — those differ in both
directions:

- `AnthropicProvider.get_chat_model` applies `ModelInfo.max_output_tokens` **only if the caller
  did not already supply `max_tokens`** (`anthropic_provider.py:91-97`), and `extra_kwargs` can
  supply one. An operator-configured cap larger than the metadata therefore wins at runtime while
  the metadata is what the budget was sized against — the same overflow, re-opened.
- `LocalProvider` never passes `max_output_tokens` to either constructor. Both
  `_get_ollama_model` and `_get_openai_compat_model` build `model_kwargs` from
  `config.extra_kwargs` and the caller's `kwargs` only (`local_provider.py:94-125`), so the
  declared 8192 is inert unless an operator sets it.

The reserve is therefore read from the **bound model's configured cap** where one is set, falling
back to `ModelInfo.max_output_tokens`, and to the percentage only when neither exists.

**Correction to an earlier revision of this document.** It claimed "the local provider declares
no `max_output_tokens`, so the reserve stays 15% and the benchmark path behaves as analysed."
That is false: `local_provider.py:184-192` declares `context_window=32768,
max_output_tokens=8192`. Under `max(percentage, metadata)` the SUT reserve would be
`max(4915, 8192) = 8192` and the budget 24576, not 27853 — so the benchmark path **does** move,
which is precisely what that paragraph promised it would not. Reading the effective cap rather
than the metadata resolves it in the common case (the local provider passes no cap, so the
percentage applies and the budget is 27853 as analysed), but the honest statement is that the
SUT budget depends on whether `extra_kwargs` sets `max_tokens` in the deployment being measured.
Task 3.10 now asserts the effective-cap behaviour on both branches instead of asserting a
premise that was never true.

**The reserve cannot double as the counting margin.** Once the reserve is fully allocated to the
effective output cap, nothing is left to absorb approximation error. On a 200 K window with a
64 K cap the trigger sits at 136 K of *approximately* counted prompt; any underestimate means
real prompt plus permitted generation exceeds the window. And the self-correcting property
claimed elsewhere in this document does not save it — the provider rejects *that* call, the
reactive handler returns the canned degradation, and there is no subsequent model call at which
re-evaluation could correct the estimate.

So the budget carries an explicit, separately configurable **counting margin** on top of the
reserve:

    budget = context_window − generation_reserve − counting_margin

The margin exists solely to cover the gap between `count_tokens_approximately` and the
provider's real tokenizer, and is documented as such so it is not silently re-purposed the way
the 15% was.

If the reserve and margin together consume the whole window, the budget is not positive and the
runtime fails open rather than installing a middleware that would clear everything.

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

**But those numbers are defaults, not invariants — and worse, two of them are not even
bounds.** `max_chars=800` caps `page_content` only, leaving the metadata-derived header
uncapped (Decision 8), and the `search_vectorstore_hybrid` call budget is operator-overridable.
A blanket exemption turns either into a second unbounded floor sitting outside the clearing
strategy — reintroducing the very defect this change fixes, at a different tool.

**A second count problem: the call budget does not cap the number of retrieval results.** Once
`_consume_tool_budget` is exhausted it returns a synthetic over-budget *string*
(`base_react.py:1827-1834`), and the retriever returns that string as its result
(`retriever.py:114-121`) — a `ToolMessage` carrying the same tool name on every subsequent call.
A model that ignores the instruction and keeps calling therefore accumulates exempt messages up
to `recursion_limit`, not up to the call budget, and a floor written as
`tool_budget × ceiling` undercounts them.

These refusals carry no evidence — they are an instruction, and a stale one after the first —
so the fix is to make them clearable rather than to inflate the floor.

**Two corrections to how an earlier revision proposed doing that.**

First, it specified both `exclude_tools=("search_vectorstore_hybrid",)` *and* a count bound.
Those are mutually exclusive: `exclude_tools` exempts **every** message bearing the name, so the
count bound could never take effect. Since the wrapper is ours (Decision 2), the exemption is
selected in the wrapper and `exclude_tools` is not used at all. Upstream's option is global by
design; ours needs to be conditional, so we do not delegate it.

Second — and this one was backwards — it exempted the **most recent** N retrieval results. The
refusals are precisely the most recent ones, since they can only be produced *after* the budget
is spent. With a budget of 2 and five calls, the newest two are refusals: that revision would
have protected the refusals and made the two genuine retrievals clearable, which is worse than
having no exemption at all.

The ordering is the fix, and it needs no content inspection or marking: **exempt the first
(oldest) results up to the call budget.** By construction the budget permits exactly N successes
before it starts refusing, so the first N retrieval-named results *are* the successful ones and
everything after them is a refusal. Selecting oldest-N therefore exempts exactly the evidence
and leaves every refusal reducible.

So the exemption rests on the enforced serialized ceiling from Decision 8, and is
**conditional and self-checking**: at construction the runtime computes

    exempt_floor = tool_budget("search_vectorstore_hybrid") × retrieval_output_ceiling

from the values actually in force — the call budget via the existing `_tool_budgets()` lookup,
the ceiling from the same config key the tool reads — and compares it against the derived
budget. Above a configurable fraction (default one third), it logs a warning naming the
offending values and **drops the exemption**, making retrieval results clearable like any
other. The design fails toward the bound holding, never toward a silent floor.

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

### Decision 8 — Enforce a serialized-size ceiling on every result that can be preserved or exempted

Both the preserve-count floor and the exemption floor are statements about *retained* tool
results, so both are worthless unless a retained result has an enforced size. Today neither
does.

**The ceiling has to be tool-agnostic, and that is not a detail.** `ClearToolUsesEdit` selects
what to preserve by **recency across all tool results** (`candidates[:-keep]`), not by tool
name. So the preserved set can contain any tool the agent has: the catalog search tools, MCP
tools loaded at runtime, or caller-supplied `extra_tools` — none of which this change can
enumerate, and MCP tools least of all, since their outputs come from servers outside the
repository. An invariant expressed as "we capped the two tools we know about" is therefore
false the moment an operator enables a third.

So the ceiling is applied **in our own middleware wrapper**, to every `ToolMessage` that
survives reduction — preserved-by-recency and exempted-by-tool alike — truncating anything over
a configurable per-result ceiling and marking the truncation so the model knows the content is
partial. Being tool-agnostic, it holds for tools that do not exist yet.

**The middleware ceiling is denominated in tokens, not characters.** An earlier revision wrote
the floors as `count × per_result_ceiling` with a ceiling inherited from the 4000-*character*
fetch limit, then compared the product against a *token* budget. That is a unit error, and it
fails in the unsafe direction: externally sourced Unicode and structured tool output can encode
to well over one token per character, so `2 × 4000` could be classified as comfortably under a
one-third threshold while the two exempted results actually consume far more. Since exempt
results cannot subsequently be cleared, the "provably cheap" exemption would then be neither.
The wrapper already computes tokens, so it measures each surviving result with the **same
counter** used for the request. The per-tool source clamps stay in characters, because that is
the unit those APIs take — but no floor arithmetic is ever done in characters.

That makes the two floors true by construction and readable at runtime:

    preserve_floor = keep × per_result_token_ceiling
    exempt_floor   = tool_budget("search_vectorstore_hybrid") × per_result_token_ceiling

**Order matters: clamp first, then clear.** The ceiling is applied to the message view *before*
delegating to `ClearToolUsesEdit`, not after. Applied after, an oversized newest result would
still be present while the edit measured the total, so the edit could clear every older
non-exempt result — evidence the model was relying on — and still not fix the overage, because
the oversized result is the one it must preserve. Clamping first means the edit sees an already
bounded total and clears only what genuinely has to go, which is frequently nothing.

The per-tool clamps below remain worth having — they stop pointless transfer and work at the
source, and one of them fixes an outright bug — but **the bound no longer depends on them**,
which is the point.

**`fetch_catalog_document`.** `max_chars` is a tool argument the model chooses, forwarded
unclamped all the way to `api_catalog_document`, where `max_chars=0` disables truncation and
returns the whole document (see Context). Three preserved results are only ~3.2 K tokens if
4000 is a ceiling; if the model asks for 200 000 characters, reduction is defeated and the loop
overflows *more* readily than today, because the middleware will have cleared the older
evidence first. `create_document_fetch_tool` therefore clamps the effective value to an
enforced maximum (configurable, defaulting to the current 4000) before it reaches the catalog
client, treating non-positive and non-integer inputs as "use the ceiling" rather than "no
limit". Clamping the *requested* value is not by itself enough: `_fetch_document` returns
`f"Path: {path}\nMetadata:\n{meta_preview}\n\nContent:\n{text}"` (`local_files.py:530-539`),
appending a path and up to 800 characters of metadata preview *after* the server-limited text,
so a 4000-character request still returns ~4800+. Like the retriever, it clamps its **complete
serialized return value**, not just the size it asks the catalog for.

**`search_vectorstore_hybrid`.** Its `max_chars=800` bounds only `doc.page_content`
(`retriever.py:51-53`). The rendered header interpolates `title`, `url` and `resource_hash`
straight from document metadata (`retriever.py:42-57`) with no cap at all, so a single scraped
page with a pathological title or URL produces an arbitrarily large result — and under the
exemption that result is *not reducible*. An exemption check computed from
`max_documents × max_chars` would report such a floor as safe while the request overflows.

So the retriever tool gains an enforced ceiling on its **complete serialized output**, applied
after formatting, covering headers and metadata rather than page content alone.

**Why the exemption arithmetic is knowable at runtime.** Computing the floor from
`max_documents` and `max_chars` cannot work: neither call site passes them
(`fasrc_docs_agent.py:224-235`, `cms_comp_ops_agent.py`), so they are closure-local defaults
with no configuration path and nothing for the budget module to read. Introducing a shared
limits object threaded into both the tool and the budget builder was considered and rejected as
more plumbing than the problem needs — with a per-result ceiling the floor is
`tool_budget("search_vectorstore_hybrid") × per_result_ceiling`, and **both terms are already
first-class runtime values**: the call budget comes from `_tool_budgets()`, the existing
three-layer lookup, and the ceiling is one config key read by the wrapper and the budget module
from the same place. The formatter's internal limits stop being load-bearing entirely.

The server-side gap is broader than this change — `api_catalog_document` will still honour an
unbounded `max_chars` for any other caller — so the endpoint clamp is filed as a separate
follow-up rather than smuggled into an agent-context PR.

Alternative considered: remove `max_chars` from the fetch tool signature entirely so the model
cannot influence it. Rejected — a smaller value is a legitimate and useful request, and
removing the argument changes the tool contract the prompt documents. Clamping preserves the
useful direction and closes the harmful one.

### Decision 9 — Build the budget against the model bound to the request, not the pipeline default

A chat request may override the provider and model. `_build_request_local_pipeline`
(`app.py:184-227`) serves that by shallow-copying the shared pipeline and swapping **only**
`agent_llm`:

```python
view = copy.copy(pipeline)
view.agent_llm = override_llm
view.agent = None
view._active_tools = []
view._active_middleware = []
view._static_tools = None
...
view.refresh_agent(force=True)
```

Two things it does not do, and both defeat the bound:

1. **`default_provider` / `default_model` are inherited from the source.**
   `_get_model_context_window()` resolves exactly those two (`base_react.py:1597-1616`), so the
   view derives its window from the *pipeline default*, not from the model it is actually about
   to call.
2. **`_static_middleware` is not reset.** `_active_middleware` is cleared, but the *cache* is
   not, and `refresh_agent` reads `self.middleware`, which returns the cached list whenever
   `_static_middleware is not None` (`base_react.py:1240-1250`). `force=True` forces
   `_create_agent`, not a middleware rebuild. So `copy.copy` carries the source's already-built
   middleware into the view intact.

Net effect: a request overriding a 200 K-window default down to a 32 K model gets a trigger
sized for 200 K and no in-loop reduction at all — overflowing exactly the way this change exists
to prevent, on the path where a user deliberately selected a smaller model.

**Passing the provider *name* is not enough.** `_create_provider_llm` builds a non-cached
provider from the active YAML `ProviderConfig` (`app.py:1640-1649`), whereas
`_get_model_context_window()` calls `get_provider(self.default_provider)` with no config
(`base_react.py:1606-1616`). For a custom model ID that lookup returns no metadata — silently
disabling the middleware, the failure this decision exists to prevent — and for a built-in ID
with deployment-specific metadata it can size the budget from stale registry defaults. The
window must come from the model actually bound: the view carries the resolved window (or the
configured provider metadata that yields it) rather than two strings to be re-resolved later,
and re-resolution by name is the fallback, not the mechanism.

**Fix:** the view is given its own identity, its own resolved window, and its own middleware.
`_build_request_local_pipeline` takes the provider, model and resolved metadata already
available at its call site (`app.py:2130-2140`), assigns them to the view, and resets
`_static_middleware = None` alongside the `_static_tools` reset it already performs — so `refresh_agent(force=True)` rebuilds the middleware against the
overridden model. The budget derivation itself stays in the helper module and is unit-tested
there; `app.py` gains only assignments, per its no-coverage constraint.

This is the same class of defect as issue #86, which that function exists to close: state that
should be per-view silently shared from the source. The docstring's own invariant — "any
attribute that is per-run state … must be rebuilt on the view, never shared" — already covers
the middleware cache; it simply predates there being any middleware to cache.

### Decision 11 — An operator-declared context window, because the derived one is usually absent

Everything above derives the budget from `_get_model_context_window()`. Measured against this
repository's own dev deployment config on 2026-08-16, that function returns `None` for both the
configured provider and the standby:

```
local      palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4  -> None    (services.chat_app.default_provider)
anthropic  claude-sonnet-4-6                     -> None    (the documented fallback)
anthropic  claude-sonnet-4-20250514              -> 200000
```

`base_react.py:1609` calls `get_provider(self.default_provider)` with **no config**, so the
window can only come from an exact match against a `ModelInfo` list compiled into the provider.
`LocalProvider`'s list is empty (it probes Ollama, which is not what this deployment runs);
`AnthropicProvider`'s holds four IDs and the configured `claude-sonnet-4-6` is not one of them.
Self-hosted models never match by construction, and hosted ones stop matching whenever a vendor
ships a name the pinned list predates.

The consequence is not a wrong budget — `resolve_budget` correctly returns `None` and the
factory correctly returns `[]`. The consequence is that **the entire change is inert on the
deployment the issue was filed against**, while every unit test passes, because unit tests
supply the window the production path cannot produce.

So: `services.chat_app.context_editing.context_window`, validated in `read_settings` beside the
other knobs and preferred over the derived value inside `build_context_middleware`. It reuses
the three-layer lookup and rides in on the `config` dict the builder already receives, so the
setting costs the call site nothing.

The invalid-value convention differs here in one way worth stating. Every other setting falls
back to *its own default*; this one has no default to fall back to, because "absent" and
"invalid" both mean the same thing — use whatever the provider reports. A bad value therefore
costs the operator the override and nothing else, and in particular never disables the limit.
`positive_int` also rejects `True`, which matters more here than elsewhere: `True` is an `int`
in Python, and a one-token window would clear every message on every call.

One parameter *is* added: `model_label`, naming the provider and model in the log line below.
It is not needed for the arithmetic, only so the failure has a subject.

Two alternatives were rejected. **Routing `_build_provider_config` into
`_get_model_context_window`** so the provider is built with real config: that config's `models`
are raw YAML strings, and `get_model_info` does `model.id` on them — the deploy config documents
that exact crash at `deploy/fasrc-dev/config.yaml:41-45`. **Widening the providers' hardcoded
`ModelInfo` lists**: it fixes today's two names and reintroduces the same defect at the next
model release, and cannot cover self-hosted deployments at all.

Failing open stays the behaviour when neither source yields a window, but it stops being
*silent*: an inert bound now logs the provider and model responsible, so the difference between
"protected" and "installed nothing" is visible in the logs instead of only in a token count.

### Decision 12 — The declared window describes the deployment's model, and does not follow an override

Decisions 9 and 11 interact, and the combination was measured only once both were built. The
declared `context_editing.context_window` takes precedence over the derived one (Decision 11),
and a request-local view shares the `config` dict with its source by reference — so the declared
value followed the view onto whatever model a request selected. That reintroduces Decision 9's
own failure through a second route: the window describes one model while `agent_llm` is another.

Measured on the real `_build_request_local_pipeline`, with a declared 32768 and a request
overriding to a 200000-window model carrying a 64000 output cap: the reserve (64000) exceeds the
declared window, `resolve_budget` returns `None`, and **no bound is installed at all** for that
request. A conservative-looking setting became the reason the protection disappeared.

`build_context_middleware` therefore takes `declared_window_applies`, which a view passes as
`False` **when it is bound to a different model**. The declared value is an operator's
statement about the model this deployment serves; it is not evidence about a model the
operator never named.

The qualifier is load-bearing, and an earlier revision of this decision omitted it. The chat UI
posts `provider` and `model` with *every* message — `chat.js` reads `state.selectedProvider`
and includes it on each send, not only when the user changes the dropdown — so the
request-local path is the **ordinary** path, not an exceptional one. Suppressing the
declaration on every request-local view therefore suppressed it on every request, and on a
self-hosted deployment, where nothing resolves a window by name, that installed no bound at
all. The change would have shipped inert on the deployment it was written for, by the same
route as the group 7A blocker and for the same underlying reason: a value that exists only in
config never reaching the model it describes.

`adopt_request_local_model` therefore compares the incoming `(provider, model)` against the
ones the pipeline was configured with, and treats a match as *not* a model change. Exact
identity is the right test — narrower than the provider-match heuristic rejected below, and it
keeps the invariant literal: the window and the model describe the same thing because they are
the same model. Three alternatives were
rejected:

* **`min(declared, resolved)`** — provably safe, and wrong in a way that is invisible: a
  deployment declaring 32768 for its self-hosted default would cap an override to Claude at
  32768, silently discarding six-sevenths of that model's window with nothing in the logs.
* **Apply the declared value whenever the override shares the default's provider.** A plausible
  heuristic — one vLLM server, one `--max-model-len` — but it encodes a deployment topology the
  config does not state, and it fails the moment a provider fronts two servers.
* **Keep the declared value as a last resort when the override resolves nothing.** This is the
  case that now installs no bound, so the cost is real. It was still rejected: an unresolvable
  override is exactly where a borrowed number is least likely to be right, and a budget derived
  from the wrong model overflows rather than merely under-using the window.

The consequence is explicit: on a deployment whose models resolve no metadata, a request that
overrides the model gets no in-loop bound. The source clamps in `tools/result_limits.py` still
apply — they are unconditional — so this is a weaker guarantee, not an absent one, and the
warning from Decision 11 names the model responsible.

### Decision 10 — Name the residual that clearing cannot remove, and measure it

`ClearToolUsesEdit` does not *delete* a tool result — it replaces the content with the
placeholder and keeps the `ToolMessage`, its `tool_call_id`, and the originating `AIMessage`
with its `tool_calls` intact. That framing is deliberate (it is what keeps the sequence
well-formed and stops the model re-fetching, Decision 7), but it means clearing has a floor:
per cleared round, the message framing, the retained tool-call arguments, and the placeholder
itself all survive.

That floor is **bounded but not zero**. It scales with the number of tool rounds, which
`recursion_limit` caps at 50. It is real and not clearable, so it belongs in the spec's
definition of non-reducible content rather than being quietly excluded from the bound. The
spec now lists it.

**Measured** (group 8, on the pinned `langchain-core` 1.2.13, by differencing fully-cleared
threads of 1 / 11 / 51 / 101 rounds):

| quantity | tokens |
| --- | --- |
| a full-size tool round, unreduced | 1543 |
| the same round once cleared | **51.9** |
| reclaimed by clearing | 96.6% |

At the deployed trigger of 19661 the residue alone would not exhaust the budget until roughly
**379 rounds** — an order of magnitude beyond the 50 the recursion limit permits. At that
ceiling the residue is ~2600 tokens, about 13% of the budget. This settles the open question
below: **removing whole paired rounds is not needed**, and the alternative considered at the
end of this decision stays unbuilt. `test_the_measured_residue_per_cleared_round` pins the
figure, so a placeholder or framing change that moves it fails there rather than silently
eroding the margin.

Because we own the wrapper (Decision 2), the wrapper **re-measures after applying the edits**.
If the complete request is still over budget, it logs a warning carrying the measured overage
and the tool-round count, then proceeds — the reactive handler covers the outcome. This turns
the residual from an assumption into an observation: if the floor ever does matter in
production, the logs say so, with numbers, instead of a canned apology appearing with no
explanation.

Alternative considered: iterate, removing whole paired tool-use rounds (the `AIMessage` and its
`ToolMessage` together) until the budget is met. Rejected for now — deleting messages from the
middle of a ReAct trace is exactly the malformed-sequence risk Decision 2 avoids, and the
measurement above will show whether it is ever needed. Adding it later is cheap; shipping it
speculatively is not.

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
  uncleared and retrieval results are exempt, so the grounding chain is intact. The
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

Rollback is a config flag (`services.chat_app.context_editing.enabled: false`) — but it is
narrower than "restores today's behaviour", and saying so matters more than the convenience of
claiming otherwise. **The flag disables in-loop context editing only.** The source-level clamps
from Decision 8 are unconditional, so with the flag off:

- `fetch_catalog_document` still clamps its serialized return, and `max_chars=0` still returns
  the ceiling rather than the whole document;
- `search_vectorstore_hybrid` still clamps its serialized output.

Those are deliberately not gated. The `max_chars=0` behaviour is a defect, not a feature, and a
rollback switch that restores it would be a switch for reintroducing a bug. An operator who
needs the pre-change tool semantics reverts the code; the flag exists to disable the in-loop
reduction, which is the part with runtime behaviour worth toggling. Documented here so nobody
plans a rollback around a guarantee this flag does not make.

## Open Questions

- ~~**Is clearing alone enough, or must whole paired rounds be removed?**~~ **Settled in group
  8.** A cleared round costs 51.9 tokens against 1543 unreduced, so clearing reclaims 96.6% and
  the residue would need ~505 rounds to exhaust the deployed budget — ten times the recursion
  limit. Round removal stays unbuilt (Decision 10).

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

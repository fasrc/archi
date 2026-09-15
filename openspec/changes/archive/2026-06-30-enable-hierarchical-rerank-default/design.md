## Context

`hierarchical_rerank.enabled` is read in two places, with two different defaults:

1. **Template default** — `src/cli/templates/base-config.yaml:214`:
   `enabled: {{ ...hierarchical_rerank.enabled | default(false, true) }}`. This is the value
   that gets *rendered into a deployment's `config.yaml`* when the operator's source config
   omits the key. This is "the shipped default."
2. **Runtime fallback** — `src/data_manager/vectorstore/retrievers/factory.py:54`:
   `hierarchical_cfg.get("enabled", False)`. This only fires if the *entire*
   `hierarchical_rerank` block is missing from an already-rendered config (e.g. a hand-written
   or legacy config). For any config rendered from the template, the `enabled` key is always
   present, so this fallback never decides the outcome.

ADR 0003 recommends shipping the reranker on by default. The question is which seam(s) to
flip.

## Goals / Non-Goals

**Goals:**
- A newly rendered deployment config enables hierarchical-rerank retrieval by default.
- Operators can still opt out with an explicit `enabled: false`.
- The change is config-default only — no schema, retriever, agent, prompt, or tool change.

**Non-Goals:**
- Re-rendering or migrating *existing* deployments (they carry their own rendered config;
  picking up the new default is a normal redeploy/re-render, out of scope here).
- Tuning chunk sizes or `bm25_weight` (deferred sweeps, ADR 0003 §2–3).
- Changing the runtime retriever selection logic in `factory.py`.

## Decisions

**1. Flip the template default `false → true` (the primary change).**
Change `default(false, true)` → **`default(true)`** on line 214 and update the adjacent
comment from "Disabled by default" to state it is enabled by default and how to opt out. This
is the single line that determines what new deployments get.

The boolean arg is dropped on purpose. Jinja's `default(x, true)` substitutes the default
whenever the input is *falsy*, not just *undefined* — fine when the default is `false`
(explicit `false` → `false` either way), but with a `true` default it would treat an explicit
`enabled: false` (falsy) as "unset" and render `true`, silently swallowing the operator
opt-out. Bare `default(true)` substitutes only when the key is undefined, so `unset → true`,
`false → false`, `true → true`. The opt-out guard test (tasks 1.2) verifies this and fails on
the boolean form.

**2. Leave the `factory.py` runtime fallback at `False` (conservative).**
The code-level `.get("enabled", False)` is a defensive default for a config that is *missing
the entire block* — an abnormal/legacy shape. Keeping it `False` means "if I can't even find
the feature's config, do the safe, cheap thing (HybridRetriever)" rather than silently
spinning up the cross-encoder (which downloads an ONNX model and pays the ~50 s cold load) on
a malformed config. Every template-rendered config sets the key explicitly, so this fallback
is never the deciding factor for real deployments — flipping it would change nothing for them
while making the missing-block failure mode heavier. The two defaults answer different
questions ("what should a normal deployment get?" vs. "what if the config is broken?") and
correctly have different answers.

**3. Flip the paired `chunking.strategy` default `character → sentence` (added after review).**
The reranker and hierarchical chunking are one package, not two independent knobs. The
retriever only returns *parent context* when ingestion built parent/child nodes, which happens
only for `sentence`/`markdown` strategies (`manager.py`: `hierarchical_chunking = strategy in
{sentence, markdown}`). With the legacy `character` default the retriever still runs — its
passthrough path returns the bare child chunk (`hierarchical_retriever.py:253`) — but it pays
the FlashRank cost and yields no parent expansion, i.e. *not* the ADR 0003 treatment that
produced +19%. So flipping only `enabled` ships a config that looks on but underdelivers.
Flipping both defaults together makes "default-on" mean the benchmarked package
(`sentence` chunking + rerank). The runtime fallback `manager.py`
`chunking_cfg.get("strategy", "character")` is left conservative for the same reason as
decision #2 (a config missing the whole block stays on the cheap legacy path).

**4. TDD via template-render assertions.**
Render the template with keys omitted and assert: `hierarchical_rerank.enabled: true`,
`chunking.strategy: sentence`, and the two together (coherence guard); plus a test that an
explicit `enabled: false` still renders `false`. This guards the defaults at the seam that
actually ships them.

## Risks / Trade-offs

- **`should_refuse` regressed −0.056 (n=3)** in the A/B (richer context → slightly less likely
  to refuse out-of-scope questions). Small sample; ADR 0003 flags it to monitor. Accepted: the
  net quality win (+0.108) dominates and the refuse set is tiny.
- **Cold-load latency:** the first query after each (re)deploy pays a one-time ~50 s FlashRank
  ONNX load. Accepted and documented; warm latency is +2.0 s/q.
- **Two defaults could be seen as inconsistent.** Mitigated by decision #2's rationale — they
  intentionally cover different cases; the template default is the operative one.
- **Existing deployments are unaffected until re-rendered**, which is the desired (non-breaking)
  behavior, but means the live dev instance only gets the new default on its next redeploy.

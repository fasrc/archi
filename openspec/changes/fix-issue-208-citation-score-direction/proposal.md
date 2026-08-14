## Why

The citation layer treats retriever scores as **distances** (lower = better). Every producer
that reaches it under the default `cosine` metric returns a **similarity** (higher = better).
The two consumers are therefore inverted, and both are user-visible.

Verified at `origin/dev@0a157cdc`:

| Site | What it does | Correct for a similarity? |
|---|---|---|
| `citation_formatter.py:18-19` | docstring: scores are "lower = more relevant, as they represent distances" | no |
| `citation_formatter.py:49` | dedup keeps the **lowest** score for a repeated display name (`score < existing_score`) | no — keeps the worst |
| `citation_formatter.py:61-68` | sorts **ascending**, commented "lower is better" | no — worst source first |
| `app.py:633` | `np.argsort(scores)`, ascending | no — worst source first |
| `app.py:647` | `break` when `score > self.similarity_score_reference` — a distance **ceiling** | no — should be a similarity floor |
| `postgres_vectorstore.py:396-401` | cosine producer returns `1.0 - distance` | this one is correct; it is the producer |

So on the default deployment the `(relevance: N.NN)` figure a user reads is ranked backwards,
and `get_top_sources` puts the least relevant source first. The threshold guard is inert
rather than wrong today — its default of `10` is a distance ceiling and cosine similarities
live in `0..1`, so `score > 10` is never true — which is why the defect has stayed invisible.

This is not a fresh discovery. `fix-issue-205-hybrid-search-binding` already recorded it:
`tasks.md:82` names `app.py:628-651` and `citation_formatter.py:18,49,61,76` as consumers with
"lower-is-better assumptions", and `design.md:128` tabulates the same two sites. Both defer
the fix here, to #208.

## What Changes

- **`format_citations`** sorts descending, dedups by keeping the **highest** score, and says
  so in its docstring. The `-1.0` "no score" sentinel keeps its current position — last, and
  rendered without a `(relevance: …)` suffix.
- **`get_top_sources`** sorts descending (`np.argsort(scores)[::-1]`) and applies
  `similarity_score_reference` as a **floor** (`score < threshold` → stop) rather than a
  ceiling. The `break` is kept: on a best-first ordering, the first source below the floor
  means every remaining source is too.
- **`similarity_score_reference` default** goes from `10` to `0.0` in
  `src/cli/templates/base-config.yaml:182,191`, so the guard stays effectively disabled —
  the same behaviour as today — and an operator who wants a real minimum can set e.g. `0.3`.
- **A threshold that cannot be a similarity is ignored, with a warning.** See below; this is
  the one addition to the issue's plan, and it is what makes the flip safe to deploy.
- The three other in-repo configs and the docs that still carry the distance-era `10` are
  migrated in the same change, because after the flip that value means "cite nothing".

## The stale-threshold hazard, and why this change must handle it

Flipping the comparison without more would be safe in this repo and **catastrophic on any
already-deployed config**. Under a similarity floor, a threshold of `10` makes
`score < 10` true for every score in `0..1`, so `get_top_sources` breaks on the first
document and the response cites **zero sources**. Today that same config cites everything.

Live deployments read a config that is fetched at deploy time and does not live in this
repo, so their `similarity_score_reference: 10` survives this PR. The local gate cannot see
that, and neither can CI.

The issue's own decision text is explicit that the guard should remain "effectively disabled
(same behavior as today)". Honouring that intent on a stale config *requires* the writer to
reject a threshold that cannot be a similarity: a value above `1.0` is not expressible as a
cosine similarity, so it can only be a leftover distance ceiling. This change therefore
treats a configured threshold `> 1.0` as "no floor" and logs a warning naming the value. That
is a derivation of the stated intent, not a new requirement bolted on — and it is why this
change can ship before every deployment's config is updated, rather than needing to land in
lockstep with one.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `source-citations`: gains the score-direction contract. The capability exists in
  `openspec/specs/source-citations/spec.md` but says nothing today about ordering, dedup, or
  the relevance threshold, so every requirement here is an `ADDED` one — there is no existing
  requirement to modify and no contradiction with the four already present.

## Impact

**Code**
- `src/archi/utils/citation_formatter.py` — docstring, the dedup comparison at `:49`, the sort
  key at `:61-68`. A small module of pure functions.
- `src/interfaces/chat_app/app.py` — `get_top_sources` (`:628-651`) and the threshold read in
  `__init__` (`:401-403`) for the out-of-range guard. Both files are `black --check` clean at
  `origin/dev@0a157cdc`, so an in-place edit carries no reflow risk.

**Config and docs carrying the distance-era `10`** — all must move together or they mean
"cite nothing" after the flip:
- `src/cli/templates/base-config.yaml:182,191` (the template the issue names)
- `examples/deployments/basic-agent/local-config.yaml:51`
- `tests/pr_preview_config/pr_preview_config.yaml:40`
- `docs/docs/models_providers.md:150,169` and `docs/docs/configuration.md:410`

**Tests** — `tests/unit/test_citation_formatter.py` exists and pins the current, wrong
direction: `test_sorting_lower_is_better` (`:90`) and
`test_duplicate_chunks_deduplicated_best_score_kept` (`:63`) must flip. `get_top_sources` has
no unit test today; this change adds one.

**Behaviour visible to users** — cited sources reverse order, and the `(relevance: …)` figures
that accompany them become meaningful. On a cosine deployment this is strictly a correction.

**Out of scope** (from the issue, unchanged): non-cosine metrics — `postgres_vectorstore.py`
returns a raw distance for `l2` / `inner_product`, so the consumer-side fix is wrong for
those, and a producer-side normalization is a separate P3 follow-up filed by this change.
Also out of scope: retiring `similarity_score_reference`, touching producers or the
vectorstore layer, and anything under `deploy/`.

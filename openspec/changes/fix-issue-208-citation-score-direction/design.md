# Design

## Context

Two consumers read retriever scores with the wrong polarity. The fix itself is four small
edits. Everything hard about this change is in the blast radius of flipping a comparison that
a live config participates in, so most of this document is about what must move with it.

Ground truth re-derived at `origin/dev@0a157cdc` (the issue's anchors were recorded at
`bd2d519c`; line numbers drifted, the code did not).

## Decision 1: keep the `break` in `get_top_sources`, do not turn it into `continue`

Today `get_top_sources` sorts ascending and `break`s on the first score above the ceiling.
With the list sorted best-first and the test inverted to a floor, `break` remains exactly
right: the first source below the floor guarantees every later one is too. Rewriting it as a
`continue` would change the shape of the loop for no behavioural gain and would obscure the
fact that the ordering is what makes the early exit sound.

The two guards in front of it are preserved verbatim: `score is not None` and
`score != -1.0`. The sentinel must never be compared against the threshold.

## Decision 2: a threshold above `1.0` is not a floor

This is the one place the change goes beyond the issue's plan, and the reason is that the
plan as written is unsafe to deploy.

A cosine similarity is in `0..1`. A configured `similarity_score_reference` above `1.0`
therefore cannot be a similarity floor — it can only be a distance ceiling left over from the
convention this change retires. Applied literally as a floor it filters *everything*, and the
user sees a response with no sources at all.

That is not a hypothetical. Deployments read a config fetched at deploy time from outside
this repo, so a live `similarity_score_reference: 10` is unaffected by anything in this PR.
The gate cannot catch it; CI cannot catch it; the first signal would be users reporting
missing citations.

The issue's decision text says the new default exists "so the guard remains effectively
disabled (same behavior as today)". Preserving that property on a config this PR cannot edit
is only possible if the writer refuses a value that cannot be a similarity. So: read the
threshold, and if it is `> 1.0`, treat it as `0.0` (no floor) and log a warning naming the
configured value and the file it should be changed in.

**Where:** at the read site in `__init__` (`app.py:401-403`), not inside the loop. Normalizing
once at startup means the loop keeps a single meaning for the attribute, the warning is
emitted once per process rather than once per document, and the guard is trivially testable
without constructing a retrieval.

**Why `> 1.0` and not `>= 1.0`:** a floor of exactly `1.0` is degenerate but coherent — it
means "only cite an exact-match similarity". Refusing it would be the tool second-guessing a
legal, if strict, choice. `10` is not coherent under any reading.

**Rejected alternative — clamp to `1.0` instead of `0.0`.** Clamping keeps the guard live and
still filters nearly everything, which fails the "same behavior as today" requirement in a
quieter and more confusing way. Disabling is the honest reading of a value that means
"threshold from the old convention".

**Rejected alternative — ship the flip and rely on a redeploy.** This makes correctness depend
on an out-of-band step that this PR cannot verify, ordered *before* the code lands. The repo
has a standing record of exactly that assumption failing (#180: migrations are not applied to
existing deployments).

## Decision 3: migrate every in-repo config carrying `10`, not just the template

The issue names `src/cli/templates/base-config.yaml:182,191`. A repo-wide grep finds the same
distance-era value in three more places that are read, not illustrative:

- `examples/deployments/basic-agent/local-config.yaml:51`
- `tests/pr_preview_config/pr_preview_config.yaml:40` — this one drives the PR preview
  deployment, so leaving it would make previews cite nothing and hand reviewers a false
  signal about the very change under review
- `docs/docs/models_providers.md:150,169` and `docs/docs/configuration.md:410`

Decision 2 means none of these can *break* after the flip — they would be warned about and
disabled rather than silently filtering. They are still migrated, because leaving a value the
code now warns about in the repo's own examples teaches the wrong convention.

Nothing under `deploy/` is touched; the issue puts it out of scope and it is off-limits to
this workflow regardless.

## Decision 4: the sentinel keeps its current handling exactly

`-1.0` means "no score". It must not become the best entry when the sort reverses.

In `format_citations` the sort key is already a tuple whose first element partitions real
scores from the sentinel; only the second element's direction changes. The sentinel therefore
stays last for free, and it still renders without a `(relevance: …)` suffix.

In `get_top_sources` there is no such partition — `np.argsort` puts `-1.0` first today,
because it is the numerically lowest value. Reversing the sort moves the sentinel to the end,
which is an incidental improvement in the right direction. It is worth a test precisely
because it is incidental: nothing in the code states it.

## Decision 5: non-cosine metrics stay out of scope

`postgres_vectorstore.py:396-401` converts to a similarity only for `cosine` and returns a raw
distance for `l2` and `inner_product`. On those metrics the consumers are correct today and
this change would invert them. The correct fix is producer-side normalization, which is a
different module and a different risk profile.

Per the issue's resolved decision: scope out, and file a P3 follow-up. `cosine` is the default
and the only metric in production, so the exposure is bounded. The follow-up issue number goes
in the PR body.

## Risks

| Risk | Mitigation |
|---|---|
| A deployed config still carries a distance ceiling | Decision 2 disables it with a warning instead of filtering everything |
| A non-cosine deployment exists that nobody knows about | It would now be ordered backwards. Bounded and documented; the follow-up carries the real fix. The warning from Decision 2 does not fire for it, since its raw distances can legitimately exceed `1.0` — noted here so the follow-up does not assume the warning provides coverage |
| The `(relevance: …)` figure changes meaning for anyone reading historical output | Cosmetic and strictly a correction; the number was already being shown next to a backwards ranking |

## Verification beyond the gate

The gate proves the unit behaviour. It cannot prove the user-visible ordering end to end,
because that needs a retrieval against a live index. The acceptance criterion "a test fails on
`origin/dev` before the fix" is the substitute, and the PR body should record that failure
output rather than merely assert it happened.

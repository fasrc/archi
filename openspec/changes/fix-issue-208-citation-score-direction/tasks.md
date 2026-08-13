# Tasks — fix-issue-208-citation-score-direction

Every task below ends with the suite **green** and is committed on its own. Write the failing
test and the code that satisfies it inside the same task: a task that ends red cannot be
committed, and an uncommittable task stalls the loop.

Before each commit:

```bash
python -m black src tests          # format BEFORE staging; the hook's black writes, CI's asserts
git add -A && git status --short   # must be empty of unformatted leftovers after the hook runs
bash scripts/gate.sh               # run bare — no pipe, no redirect
```

Capture the red output asked for by the acceptance criteria as you go — task 1 and task 2 each
produce one, and both belong in the PR body.

---

## 1. `format_citations`: sort descending and dedup on the highest score

- [x] 1.1 In `tests/unit/test_citation_formatter.py`, flip the two tests that pin the old
      direction:
      - `test_sorting_lower_is_better` (`:90`) → assert the **higher**-scoring source appears
        first. Rename it to match (`test_sorting_higher_is_better`).
      - `test_duplicate_chunks_deduplicated_best_score_kept` (`:63`) → assert the **highest**
        score is kept for a repeated display name.
- [x] 1.2 Add a test mixing real scores with `-1.0` sentinels, asserting every real score
      sorts before every sentinel and the real ones are ordered highest first.
- [x] 1.3 Run `python -m pytest tests/unit/test_citation_formatter.py -q` and **save the
      failure output** — this is acceptance criterion 1's "fails on `origin/dev` before the
      fix". Paste it into the PR body verbatim.
- [x] 1.4 Fix `src/archi/utils/citation_formatter.py`:
      - docstring `:18-19` — scores are similarities, higher = more relevant; `-1.0` means
        "no score".
      - dedup `:49` — `score > existing_score` instead of `score < existing_score`. Keep the
        `score != -1.0 and (existing_score == -1.0 or …)` structure so a real score still
        beats the sentinel.
      - sort `:61-68` — negate the second element of the key tuple (or pass `reverse` on that
        element only) so real scores order descending. **Do not** put `reverse=True` on the
        whole `sorted()` call: that would also reverse the first element and float the
        sentinels to the top. Update the `# Sort:` comment at `:61`.
- [x] 1.5 Confirm the sentinel still renders with no `(relevance: …)` suffix — `:75-76` is
      untouched, and `test_score_minus_one_omitted` (`:84`) should stay green without edits.
- [x] 1.6 Gate and commit: `fix(citations): order sources by descending similarity`

## 2. `get_top_sources`: reverse the sort and make the threshold a floor

- [x] 2.1 `get_top_sources` has no unit test today. Add `tests/unit/test_get_top_sources.py`
      covering, against a real `ChatWrapper` instance or a minimal stand-in that sets
      `similarity_score_reference`:
      - the highest-scoring document leads the returned list;
      - a document scoring below an operator-set floor (e.g. `0.3`) is excluded, and so is
        every document after it;
      - a `-1.0` sentinel is neither threshold-filtered nor treated as a stopping point, and
        sorts after the real scores;
      - with the shipped default (`0.0`), nothing is filtered.
- [x] 2.2 Run the new file and **save the failure output** for the PR body.
- [x] 2.3 Fix `src/interfaces/chat_app/app.py` in `get_top_sources` (`:628-651`):
      - `:633` — `np.argsort(scores)[::-1]` for descending order.
      - `:647` — `score < self.similarity_score_reference` (a floor). **Keep the `break`**:
        with the list ordered best-first, the first source below the floor guarantees the rest
        are too. Keep the `score is not None` and `score != -1.0` guards exactly as they are.
      - `:648-651` — reword the debug log; "above threshold" is now "below threshold".
- [x] 2.4 Gate and commit: `fix(citations): apply the relevance threshold as a similarity floor`

## 3. Refuse a threshold that cannot be a similarity

Rationale in `design.md`, Decision 2. This is what makes the change safe to deploy ahead of a
config update, so it is not optional.

- [x] 3.1 Add tests: a configured `similarity_score_reference` of `10` behaves as no floor
      (nothing filtered) and logs a warning naming the value; a configured `1.0` is still
      applied as a floor; a configured `0.3` is applied unchanged.
- [x] 3.2 Implement at the read site in `__init__` (`app.py:401-403`), not inside the loop:
      after reading the value, if it is `> 1.0`, log a warning naming the configured value and
      substitute `0.0`. Once per process, so the loop keeps one meaning for the attribute.
- [x] 3.3 Gate and commit: `fix(citations): ignore a distance-era relevance threshold`

## 4. Migrate every in-repo config and doc carrying the distance-era `10`

- [x] 4.1 `src/cli/templates/base-config.yaml:182,191` — `default(10, true)` → `default(0.0,
      true)`. Note the Jinja `true` argument means "use the default when the value is falsey";
      an operator setting `0` and an operator setting nothing both resolve to `0.0`, which is
      the same value, so the filter needs no restructuring.
- [x] 4.2 `examples/deployments/basic-agent/local-config.yaml:51` → `0.0`.
- [x] 4.3 `tests/pr_preview_config/pr_preview_config.yaml:40` → `0.0`. Leaving this one stale
      would make the PR preview cite nothing and give reviewers a false read on this very
      change.
- [x] 4.4 `docs/docs/models_providers.md:150,169` and `docs/docs/configuration.md:410` → `0.0`,
      with a sentence saying the value is a minimum cosine similarity in `0..1` and that `0.0`
      means "cite everything retrieved".
- [x] 4.5 Re-grep to prove none is left:
      `grep -rn 'similarity_score_reference' --include=*.yaml --include=*.md . | grep -v openspec/changes/archive`
- [x] 4.6 Gate and commit: `chore(config): default the relevance threshold to a similarity`

## 5. Follow-up and PR

- [ ] 5.1 File the P3 follow-up for producer-side normalization under `l2` /
      `inner_product` (`postgres_vectorstore.py:396-401` returns a raw distance for both, so
      the consumer-side convention this change establishes is wrong for them). Record its
      number — acceptance criteria require it in the PR body.
- [ ] 5.2 Open the PR against `fasrc/archi:dev` with `Closes #208`. The body must carry:
      - the two saved red-test outputs from tasks 1.3 and 2.2;
      - the non-cosine scope-out and the follow-up issue number;
      - **a deploy note**: the shipped default changed, so a redeploy is needed for a
        deployment to pick it up — and, because of task 3, a deployment that has *not* been
        updated logs a warning and cites normally rather than citing nothing.
- [ ] 5.3 Do **not** merge. A human merges.

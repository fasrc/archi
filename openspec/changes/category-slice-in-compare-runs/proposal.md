## Why

The rung-0 sweep (#540) cannot produce a verdict today. Its primary tests are exact McNemar
tests paired per question, and nothing in this repository computes one (`compare_runs.py`
has no paired binary test; the only implementation is `mcnemar_exact` in `fasrc/archi-bench-out`
`feature_matrix/figures/extract_figure_data.py:67-74`). The plan also requires a per-category
slice that reads only a snapshot bound to the run, and the void and join rules the operator
decided on #524, #525 and #538. This change is the consumer half of plan item W6; it reads the
fields `record-category-map-digest` writes.

## What Changes

- **Paired exact McNemar tests** in a new `scripts/benchmarking/paired_tests.py`, ported from
  `mcnemar_exact`, reported by `compare_runs.py` for every arm against the baseline:
  - *source*: per-question **relative** hit (any declared source matched), on rows with
    declared sources that are clean in both arms (operator decision, 2026-09-24);
  - *completion*: per-question status `ok` versus not `ok`, on the common question set
    (operator decision, 2026-09-24: the plan's "blowout" test is this one).
  - Each reports b, c, n and the two-sided p. `--primary LABEL=source|completion` marks each
    arm's primary; the other test per arm is secondary and gets a Holm-adjusted p across all
    secondaries. The strict source accuracy and the count of questions with no tool call are
    reported beside them as descriptive.
- **A per-category slice** in a new `scripts/benchmarking/category_slice.py`, built only from
  a snapshot that passes four checks — present, corpus fingerprint recorded and unchanged at
  the endpoints, `category_map_unchanged_at_endpoints` is `true`, and `sha256(file)` equals
  `category_map_sha256_end` — with the failing check named.
- **The counting rules** (#525, #538 rule 4): each row owned by its first declared source's
  category; rows spanning categories on their own line and out of per-category source
  accuracy; rows whose first source has no category on an "uncategorized" line; coverage as
  distinct gold articles over every source; underpowered per metric below 3 distinct articles.
- **The cross-arm map rule** (#538 rule 3): `--routes-on-category LABEL` names arms whose
  mechanism reads the map (r0a). A digest mismatch or a failed reading voids every comparison
  that includes such an arm; for other pairs it drops only the slice, unless a
  `search_metadata_index` call appears in either side's benchmark `messages` or joined QA
  `answers.jsonl` `tool_calls`, which voids the comparison too.
- **The QA join rule** (#538 rule 1): for an arm that records `category_map_sha256_end`, a
  `--qa-run` joins only if its recorded start and end readings both equal that digest.

## Capabilities

### New Capabilities
<!-- none: category-slice-provenance is introduced by record-category-map-digest -->

### Modified Capabilities
- `category-slice-provenance`: adds the consumer requirements (paired tests, slice checks,
  counting rules, cross-arm void, QA join). Written as ADDED requirements against the
  capability the producer change introduces.

## Impact

- New `scripts/benchmarking/paired_tests.py`, `scripts/benchmarking/category_slice.py`;
  thin call sites and render sections in `scripts/benchmarking/compare_runs.py`; new tests.
- New CLI flags `--primary`, `--routes-on-category`; no existing flag changes meaning.
- Artifacts without category-map keys (every feature-matrix artifact to date) behave as
  today: no slice, a "no snapshot" note, and no new QA-join refusal.
- Depends on `record-category-map-digest` (fields, URL rule) and `preflight-category-census`
  (`category_attribution.py`); order: producer, census, this change, harness.

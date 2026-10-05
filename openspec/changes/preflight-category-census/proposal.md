## Why

The rung-0 sweep (#540) must not read a verdict from a bank or corpus that cannot support
it. The plan (`docs/docs/proposals/categories-action-plan.md` §6.2, W7) makes three
preflight censuses pass/fail gates — category coverage of KB articles, vocabulary drift
against r0a's routing list, and bank coverage with per-article concentration — and today
they are two hand-typed `psql` queries in an archi-config README and one that does not exist.
The plan also requires two checks that no code does: a unit test pinning the
`_build_extra_text` / `search_metadata` substring fallback r0a depends on (no test covers
either, `src/data_manager/collectors/utils/catalog_postgres.py:474-480`, `:1560-1569`), and
the r0b exemplar-disjointness check (§5 void checks; §6.3).

## What Changes

- **A shared pure attribution module**, `scripts/benchmarking/category_attribution.py`: owner
  by first declared source, the cross-category and uncategorized lines, unresolved URLs,
  coverage over every source, per-article row share over every citation, and per-metric power
  (#525, #538 rule 4). The census uses it now; `category-slice-in-compare-runs` imports it,
  so the preflight and the result cannot disagree about a row.
- **`scripts/benchmarking/category_census.py`**, read-only against Postgres (`--pg-dsn`, as
  `goldenset_maintenance.py` connects), every query filtered `NOT is_deleted`, URLs
  canonicalized with the shared rule from `record-category-map-digest`:
  1. coverage — KB documents (`url LIKE '%/kb/%'`) with a non-empty `category`; fail below 90 %;
  2. vocabulary drift — the distinct `category` set over those KB documents equals the bullet
     list under `## Category routing` in `--routing-prompt`; fail on any difference, naming it;
  3. bank coverage — per category owned gold rows and coverage; fail below 6 categories with
     coverage; fail when any article's row share exceeds 10 %; report underpowered categories,
     the cross-category, uncategorized and unresolved lines.
  Output is a Markdown table (for the PR body) and `--json`, labelled with the corpus
  fingerprint it read; exit 0 pass, 2 on any failed gate.
- **`--exemplar-prompt`** check for r0b: every `Question:` under `## Worked examples` absent
  from the bank's and anchors' questions (normalized exact match, and a word-set Jaccard
  similarity of 0.5 or more counts as present), and every URL cited in its `Answer:` absent
  from their declared sources after canonicalization; fail naming each collision.
- **A unit test** that pins `_build_extra_text`'s `key:value` emission and `search_metadata`'s
  `extra_text ILIKE '%key:value%'` fallback for a key outside `_METADATA_COLUMN_MAP`.

## Capabilities

### New Capabilities
<!-- none: category-slice-provenance is introduced by record-category-map-digest -->

### Modified Capabilities
- `category-slice-provenance`: adds the preflight requirements (shared attribution, three
  censuses, exemplar disjointness, the fallback pin).

## Impact

- New `scripts/benchmarking/category_attribution.py`, `scripts/benchmarking/category_census.py`,
  tests under `tests/unit/`.
- Depends on `record-category-map-digest` for the shared URL rule; order: producer, this
  change, consumer, harness.
- No production code changes; `catalog_postgres.py` gains a test, not an edit.

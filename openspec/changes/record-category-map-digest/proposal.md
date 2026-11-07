## Why

The rung-0 prompt sweep (#540) needs a per-category slice of the golden set, and that slice
is only honest if it reads the URL → category map the run actually used. The corpus
fingerprint cannot prove that: `CORPUS_STATE_QUERY` never hashes `documents.extra_json`,
where `category` lives (`src/bin/service_benchmark.py:106-129`), so a metadata-only change
moves the map at a constant fingerprint. The operator decided how to close that gap on
#524 (capture at the run endpoints) and #538 (the five provenance rules), and the plan
records both in `docs/docs/proposals/categories-action-plan.md` §6.1. This change is the
producer half of plan item W6: nothing writes the digests today.

## What Changes

- A shared URL canonicalizer in `src/utils/benchmark_provenance.py`, moved from
  `Benchmarker._canonical_source` (`service_benchmark.py:1738-1759`, the PR #106 rule), so the
  harness's source matching, the map digest and the later slice join use one rule.
- A category-map digest helper in the same module: one record per non-deleted document with
  a URL, `canonical_url<TAB>category` (empty string for a missing category), escaped, sorted,
  newline-joined and hashed as `sha256:<hex>` — the method `corpus_fingerprint` already uses
  (#538 rule 5).
- A never-raising reader on `ResultHandler`, beside `get_corpus_fingerprint`, that returns the
  records and the digest, or an `<unavailable: …>` marker on failure (#538 rule 2).
- Two readings per arm at the corpus-fingerprint points: before `_process_config` in
  `Benchmarker.run()` (`:2202`) and in `handle_results` (`:471`) (#538 rule 1). Each arm entry
  gains `category_map_sha256_start`, `category_map_sha256_end` and the three-state
  `category_map_unchanged_at_endpoints`.
- The end reading's records written once per arm as
  `<benchmark>-<timestamp>_category_map_<N>.tsv` beside the artifact in `dump_artifacts`, where
  `<N>` is the arm's 1-based position; the arm entry names the file. No reading happens at
  archive time.

## Capabilities

### New Capabilities
- `category-slice-provenance`: recording, per benchmark arm, which URL → category map the run
  used, so a later per-category slice can prove it reads that map (this change adds the
  producer requirements; the consumer, harness and census changes add theirs).

### Modified Capabilities
<!-- none: the corpus fingerprint and the existing artifact keys keep their meaning -->

## Impact

- `src/utils/benchmark_provenance.py` (new helpers), `src/bin/service_benchmark.py` (two call
  sites, the arm entry, `dump_artifacts`), and `_canonical_source` becomes a thin call to the
  shared rule.
- Artifacts gain three keys per arm and one sibling `.tsv` per arm. Readers that ignore
  unknown keys (`compare_runs.py`, `archive_run.sh`, the report) are unaffected.
- `corpus_fingerprint` and every gate that compares it are unchanged (the #524 decision
  rejected folding the category into it).
- Cost: two extra `SELECT url, extra_json->>'category' FROM documents WHERE NOT is_deleted`
  reads per arm (~841 rows on the claw corpus).

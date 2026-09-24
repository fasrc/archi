## Context

The plan (`docs/docs/proposals/categories-action-plan.md` §6.1, W6) and the operator's
decisions on #524 and #538 fix *what* to record. This design fixes *where* in the code.

Facts it builds on (all at `origin/dev` 5d72ed49):

- `ResultHandler.get_corpus_fingerprint` (`src/bin/service_benchmark.py:344-386`) never raises:
  it reads through `PostgresServiceFactory.get_instance().connection_pool.execute(...)` and
  turns any exception into `"<unavailable: …>"`. `corpus_reading_failed` (`:212-217`) tests it.
- The start reading is at `Benchmarker.run()` `:2202`; the end reading and the three-state
  `corpus_unchanged_at_endpoints` are in `handle_results` `:471-487`; the arm dict is built at
  `:489-554` and appended to the class-level `ResultHandler.results`.
- Nothing is written per arm. `dump_artifacts` (`:641-663`) takes one timestamp and writes the
  JSON (`dump`, `:688-715`) and its `_report.md` sibling. Only it knows the file stem.
- `Benchmarker._canonical_source` (`:1738-1759`) is the PR #106 rule the harness matches
  sources with (`get_source_results`, `:1804-1856`); `compare_runs.py` has none.
- `corpus_fingerprint` (`src/utils/benchmark_provenance.py:288-318`) escapes with `_escape`
  (`:283-285`), sorts, joins with `\n`, and returns `sha256:<hex>`.

## Goals / Non-Goals

**Goals:**
- Per arm: two map readings, three-state comparison, and the end reading's exact records on
  disk, hash-bound to `category_map_sha256_end`.
- One canonicalization rule shared by matching, the digest, and (in the consumer change) the
  slice join.

**Non-Goals:**
- The slice itself, the snapshot checks and any gate (`category-slice-in-compare-runs`).
- QA-run readings, the rerun path and the ledger (`sweep-mode-feature-matrix-wrappers`).
- Any change to `corpus_fingerprint` or its gates (#524 rejected that).

## Decisions

1. **Helpers live in `benchmark_provenance.py`, the reader in `ResultHandler`.**
   `canonical_source_url(value)` and `category_map_records(rows) -> list[str]` plus
   `category_map_digest(records) -> str` are pure and unit-tested without Postgres.
   `ResultHandler.get_category_map()` mirrors `get_corpus_fingerprint`: same factory, same
   never-raise shape, returns `(records | None, digest_or_unavailable)`. *Alternative:* put
   everything in `service_benchmark.py` — rejected, the file is large and the pure helpers are
   needed by `compare_runs.py` and the W7 census, which should not import the harness.
2. **`_canonical_source` delegates to the shared rule.** Keeps its call sites and tests; the
   behavior moves, not changes. *Alternative:* adopt `normalize_page_url` (lowercases host,
   drops fragments) — rejected: it would change which gold references match, and the plan
   names the PR #106 rule.
3. **Query:** `SELECT url, extra_json->>'category' FROM documents WHERE NOT is_deleted AND url
   IS NOT NULL`. The join key is the URL, so a document without one cannot be joined and
   contributes no record. Duplicate URLs are kept as separate records; the consumer reports a
   URL with two categories as unresolved rather than choosing one.
4. **Escaping** percent-escapes `%`, tab and newline in both fields (the same scheme as
   `_escape`, with tab in place of `:` because tab is this file's separator), so a value cannot
   add or split a record and the file parses back into the exact pairs that were hashed.
5. **The arm entry holds the records until `dump_artifacts`.** `handle_results` keeps the end
   records on a class-level list parallel to `ResultHandler.results` (not in the JSON), and
   `dump_artifacts` writes `_category_map_<N>.tsv` with `<N>` = 1-based position, then sets
   `category_map_file` on the entry before `dump` serializes it. File bytes are exactly
   `"\n".join(records)`, so `sha256(file) == category_map_sha256_end` by construction.
   *Alternative:* embed the map in the JSON — rejected, it bloats every artifact and every
   leaderboard read by ~841 rows per arm; *alternative:* write in `handle_results` — no file
   stem exists yet.
6. **Start reading is taken beside `corpus_before`** and passed into `handle_results` as a new
   keyword (`category_map_before`), the same shape the corpus reading already uses.

## Risks / Trade-offs

- [`service_benchmark.py` is a black-churn / diff-cover trap] → run the black-seam-scout
  agent before editing; keep new logic in `benchmark_provenance.py` and keep the harness edits
  to thin call sites.
- [End reading comes after RAGAS grading] → accepted by #538 rule 1: an edit during grading is
  a false refusal of the slice, never a wrong number.
- [Two readings add DB load] → ~841-row selects, twice per arm; negligible beside a
  multi-hour arm.
- [A later dump could replace the file] → the consumer requires `sha256(file) ==
  category_map_sha256_end`; a replaced file fails that check.

## Context

At `origin/dev` 5d72ed49: the bank is 105 rows (102 with one source, 3 with none; 52 distinct
URLs) and the anchor set is 5 rows, one with two sources. `documents` keeps `category` inside
`extra_json` (`src/cli/templates/init.sql:235`) with `is_deleted` (`:250`).
`scripts/benchmarking/goldenset_maintenance.py:166-187` already connects with
`psycopg2.connect(dsn)` from `--pg-dsn`. r0a's routing list is a bullet list under
`## Category routing`; r0b's exemplars are `Question:` / `Answer:` blocks under
`## Worked examples` with inline Markdown links (archi-config PR #22). No test covers
`_build_extra_text` (`catalog_postgres.py:1560-1569`) or the fallback clause (`:474-480`).

## Goals / Non-Goals

**Goals:** the shared attribution module; the three censuses as one script with gates and
tests; the exemplar check; the fallback pin.

**Non-Goals:** the per-run slice (consumer change); any bank or prompt edit; running the
census on a live stack (W7's "run it and post the table" is an operator step after merge).

## Decisions

1. **Attribution is pure and data-first.** Functions take `rows: list[list[str]]` (each row's
   declared source URLs, in order) and `category_of: dict[str, str | None]` (canonical URL →
   category, with a sentinel for "URL mapped to two categories"). No I/O, so the census and the
   slice pass the same inputs and get the same answer, and tests need no database.
2. **Coverage and concentration differ on purpose** (#538 rule 4): coverage = distinct
   articles per category over every source; ownership uses only the first source; row share =
   rows citing the article / **gold rows**, where a gold row is a bank row that declares at
   least one source — the same denominator `source_hits` and `_source_scorable_count` use
   (`src/utils/benchmark_resilience.py:102-123`, `service_benchmark.py:1940-1952`). The
   three source-less `should_refuse` rows have no gold article, so they are not gold rows.
   Leaving them out makes every share larger (k/102, not k/105), so the gate is the stricter
   of the two readings; a fixture pins that direction.
3. **The census reads `CORPUS_STATE_QUERY` by AST from `src/bin/service_benchmark.py`**, as
   `fm_fingerprint` does (`feature_matrix/lib.sh:85-99`), so the label matches the harness
   without importing it.
4. **Question similarity:** normalize (casefold, collapse whitespace, strip punctuation), then
   exact match or word-set Jaccard ≥ 0.5. Exact match alone misses a paraphrase; the threshold
   is recorded in the output so a reviewer can see what "absent" meant.
5. **The fallback pin tests the SQL builder, not a database:** call `search_metadata` with a
   stub connection that captures the SQL and parameters. It pins r0a's mechanism without a
   Postgres fixture.
6. **Exit codes follow `compare_runs.py`**: 0 pass, 1 usage, 2 a failed gate.

## Risks / Trade-offs

- [Jaccard threshold is a judgment call] → it is printed and configurable
  (`--similarity-threshold`), default 0.5.
- [`search_metadata` signature drifts] → that is the point of the pin: the test fails and
  names the change r0a depends on.
- [Census run against the wrong stack] → the corpus fingerprint is in every output, and the
  sweep's lock records the fingerprint it pinned.

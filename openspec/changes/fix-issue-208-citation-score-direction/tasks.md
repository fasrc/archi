## 1. Flip the two misdirected tests to red, then fix the implementation to green (TDD)

- [x] 1.1 In `tests/unit/test_citation_formatter.py`, make the two tests that pin the
      wrong score direction fail:
      (a) Rename `test_sorting_lower_is_better` → `test_sorting_higher_is_better` and
      flip its assertion: given docs with scores `[0.90, 0.10]`, `b.md` (score 0.90)
      must appear **before** `a.md` (score 0.10) — the higher score is the better match.
      (b) In `test_duplicate_chunks_deduplicated_best_score_kept`, flip the assertion
      from `"(relevance: 0.50)" in result` to `"(relevance: 0.90)" in result`, and from
      `"(relevance: 0.90)" not in result` to `"(relevance: 0.50)" not in result` — the
      higher score is the better chunk to keep.
      Run `python -m pytest tests/unit/test_citation_formatter.py -q` and confirm both
      tests **fail** on those assertions (not on an import error). This is the red step.

- [x] 1.2 Fix `src/archi/utils/citation_formatter.py` to make 1.1 green:
      (a) In the deduplication block, change the comparator from `score < existing_score`
      to `score > existing_score` so the **highest** score is retained per source.
      (b) In the sort key, negate the score: change
      `item[1]["score"] if item[1]["score"] != -1.0 else 0` to
      `-item[1]["score"] if item[1]["score"] != -1.0 else 0` so entries are ordered
      highest-score-first within the scored tier.
      (c) Update the docstring on `format_citations` and the inline comment from
      "lower = more relevant, as they represent distances" / "ascending — lower is better"
      to "higher = more relevant" / "descending — higher is better".
      Run the test file again and confirm all tests pass.

## 2. Verify the full test suite passes

- [x] 2.1 Run `bash scripts/gate.sh` bare (no pipe, no redirect). Format, lint, tests,
      and ≥80 % diff coverage on changed lines must all pass. Never `--no-verify`.
      The changed lines are in `citation_formatter.py` (importable, fully covered by
      `test_citation_formatter.py`) and the test file itself, so coverage is
      straightforward.
- [x] 2.2 Confirm no other test asserts old ascending-order / lower-is-better semantics
      for `format_citations`:
      `grep -rn "lower.*better\|ascending.*lower\|lower.*relevant" tests/ src/archi/utils/citation_formatter.py`
      must return nothing (or only comments that were already updated in 1.2c).

## 3. Ship it (no merge)

- [ ] 3.1 Open the PR against `dev`:
      `gh pr create --repo fasrc/archi --base dev`. The body MUST contain `closes #208`.
      State in the body that no migration and no deploy ordering is needed — this is a
      pure logic fix in a pure function. Name the follow-up item: `app.py:get_top_sources`
      has the same sort-direction inversion and will be corrected in a separate PR once a
      tested helper module is extracted (design D5). **Never merge** — a human merges in
      daylight.

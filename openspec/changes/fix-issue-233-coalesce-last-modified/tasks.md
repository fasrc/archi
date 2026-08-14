## 1. Preserve a stored last_modified on the conflict path (TDD)

- [x] 1.1 Make the pinned assertion red, then green — **in this one task**, because the
      gate runs before every commit and a task that ends with the suite red can never be
      committed. First edit
      `tests/unit/test_catalog_postgres_upsert_last_modified.py:80`, which today asserts
      `"last_modified = EXCLUDED.last_modified" in sql`, to assert the preserving form
      instead: `"last_modified = COALESCE(EXCLUDED.last_modified, documents.last_modified)"
      in sql` (update the test's name/docstring to match — it currently says the clause
      "must include last_modified = EXCLUDED.last_modified"). Run
      `python -m pytest tests/unit/test_catalog_postgres_upsert_last_modified.py -q` and
      confirm it fails **on that assertion** (not on an import or fixture error) — that is
      the red step, and it proves the test actually pins the clause. Then change the
      `ON CONFLICT (resource_hash) DO UPDATE SET` clause in
      `src/data_manager/collectors/utils/catalog_postgres.py` (`upsert_resource`, the
      `last_modified = EXCLUDED.last_modified,` line) to
      `last_modified = COALESCE(EXCLUDED.last_modified, documents.last_modified),`.
      Re-run: green. Change nothing else in the statement — not the column list, not the
      `VALUES` tuple, not the params (design D1/D2).
- [x] 1.2 Add the two cases the fix exists for, both green against 1.1's implementation.
      (a) A re-upsert whose metadata omits `last_modified` still emits the COALESCE clause
      **and** still passes `None` through in the params tuple — assert both, so the
      preservation is proven to be decided in SQL rather than smuggled into Python by
      dropping the parameter (design D5). (b) A supplied `last_modified` is still parsed
      and passed through so it overwrites — an *older* timestamp than a notionally stored
      one is the sharper case, since `COALESCE` must not be mistaken for "keep the newest".
      Keep the existing two passing tests (`..._includes_column_in_sql`,
      `..._passes_parsed_value`) as-is.
- [x] 1.3 Confirm no other test or caller pins the old clause:
      `grep -rn "last_modified = EXCLUDED" tests/ src/` must return nothing.

## 2. State the contract where the next caller reads it

- [x] 2.1 Extend the `upsert_resource` docstring in
      `src/data_manager/collectors/utils/catalog_postgres.py` to state the semantics
      explicitly: a `last_modified` supplied in metadata is written on insert and on
      conflict-update; an absent one stores `NULL` on insert but **leaves an existing
      stored value unchanged** on conflict-update, because absence means "no new
      information", not "clear it". Note in one clause that this applies to
      `last_modified` only and why (design D4) — the other nullable columns are properties
      observed in the current ingest, where absence legitimately means "not set". This is
      an explicit acceptance criterion of issue #233; do not skip it.

## 3. Verify against the issue's acceptance criteria

- [x] 3.1 Run `bash scripts/gate.sh` **bare — no pipe, no redirect** (it refuses to run
      when its output is piped or redirected). Format, lint, tests, and ≥80% diff coverage
      on changed lines must all pass. Never `--no-verify`. Note that the changed production
      line lives inside a SQL string literal and so carries no executable-line coverage of
      its own (design D5) — the patch coverage comes from the test file.
- [x] 3.2 Run `openspec validate fix-issue-233-coalesce-last-modified --strict` and confirm
      it passes.
- [x] 3.3 Confirm the two behavioural acceptance criteria from issue #233 are covered by
      real tests: re-ingest without a timestamp leaves a stored value intact, and a
      genuinely different timestamp still overwrites. Both are in
      `tests/unit/test_catalog_postgres_upsert_last_modified.py` after task 1.

## 4. Ship it (no merge)

- [ ] 4.1 Open the PR against `dev`:
      `gh pr create --repo fasrc/archi --base dev`. The body MUST contain `closes #233` —
      a closes-keyword in the *title* does not link the issue. State in the body that this
      needs **no migration and no deploy ordering** (the column is already nullable — only
      the update semantics change), and name the accepted trade-off: a legitimately
      withdrawn timestamp keeps its stale value (design D3). **Never merge** — a human
      merges in daylight.

# Tasks — read the golden set in the QA console

Every numbered **section** is one loop turn and ends **green and committed**: write
the failing test, watch it fail *for the right reason*, write the smallest code that
passes, run the gate, commit. Checkboxes inside a section are steps of that turn, so
intermediate ones are expected to be red — only the section boundary is a commit
point. Never end a section with the suite red, and never use `--no-verify`.

Gate: `bash scripts/gate.sh` (see `CLAUDE.md`). On this host it needs the project
interpreter on `PATH`:

```
PATH=/home/a2rchi/miniforge3/envs/archi/bin:$PATH
```

Focused run while working:

```
/home/a2rchi/miniforge3/envs/archi/bin/python -m pytest tests/unit/evaluation/qa/ -q
```

Standing notes:

- **Scope.** This change edits `src/evaluation/qa/dataset.py`,
  `src/evaluation/qa/catalog.py`, and their unit tests. No config, no template, no
  provider. If a task seems to need another file, stop and revise the design.
- **The bank does not change.** `config/benchmarking/fasrc_ragas_queries.json` and
  `examples/benchmarking/anchor_questions.json` are inputs to the tests and MUST NOT
  be edited by this change — the whole point is that the console adapts to the bank.
- **Extras are inert.** After every task, carried fields must still have zero effect
  on preparation, running and scoring.

## 1. RED: an unrecognized field survives the round trip

- [x] Test: parse a row carrying `sources` and `notes`, re-serialize with
      `dataset_item_to_dict`, assert both fields come back with their values. Watch
      it fail first on `ValueError: ... unknown field(s): notes, sources`, which is
      the proof the test binds to the real gap.
- [x] Add `extra: Optional[Dict[str, Any]]` to `DatasetItem`; populate it in the row
      readers; re-emit it in `dataset_item_to_dict`.
- [x] Gate, commit.

## 2. RED: carried fields change the content hash

- [x] Test: two datasets differing only in a carried field produce different
      `canonical_json` output. This is what stops the catalog's `sha256` dedupe from
      collapsing a genuine maintenance edit into "already imported, nothing to do."
- [x] Test at the catalog level: importing the second returns `created: True` rather
      than resolving to the first.
- [x] Include `extra` in the canonical serialization. (Section 1's re-emission in
      `dataset_item_to_dict` already feeds `canonical_json`, so these arrived green —
      they stand as pins; the dialect-level dedupe test that can go red first lands
      with the section-6 adapter, which rewrites the hashed blob.)
- [x] Gate, commit.

## 3. RED: carried fields are inert

- [x] Test: preparation over two datasets identical except for carried fields
      produces identical prepared output. Pins that `extra` is data in transit and
      never reaches scoring.
- [x] Confirm it passes with no new code — if it does not, sections 1-2 over-reached
      and `extra` is leaking into a code path it should not touch. (Confirmed: green
      with no production change.)
- [x] Gate, commit.

## 4. RED: near-miss field names are refused

- [x] Test: a row carrying `expectd_atoms` is rejected, and the error names both the
      offending key and `expected_atoms`. Name the test for the reason — a silently
      dropped `expected_atoms` means the extractor invents the obligations and the
      run still reports success.
- [x] Test: `sources`, `notes`, `status`, `anchor_type`, `source_match_field` are all
      carried, i.e. the rule refuses typos without refusing the real bank.
- [x] Implement the edit-distance-1 check against the known field names.
- [x] Gate, commit.

## 5. RED: known fields keep their contract

- [ ] Test: a known field with the wrong type is still rejected (e.g.
      `time_sensitive` as a string), and a missing required field is still rejected.
      Carrying unknowns must not have loosened anything about the known ones.
- [ ] Confirm these pass without new code; if any regressed, section 1 widened the
      parse path too far.
- [ ] Gate, commit.

## 6. RED: the RAGAS dialect imports unconverted

- [ ] Test: a bank row shaped `{user_input, reference, sources, notes, status,
      anchor_type}` imports successfully; question comes from `user_input`, answer
      from `reference`, `time_sensitive` defaults false, an `id` is synthesized, and
      the remaining four fields are carried.
- [ ] Test: ids are stable — importing the same bank twice yields the same ids.
- [ ] Implement the normalize step in `EvaluationCatalog.import_dataset`, before
      validation. No dialect knowledge in the row parser.
- [ ] Gate, commit.

## 7. RED: the import result reports what it did

- [ ] Test: importing a RAGAS-dialect bank returns a result naming the detected
      dialect and listing the carried field names.
- [ ] Test: importing a native dataset reports no dialect mapping and behaves
      exactly as before (regression guard for every existing caller).
- [ ] Gate, commit.

## 8. The real bank, end to end

- [ ] Test: import the actual 105-row `config/benchmarking/fasrc_ragas_queries.json`
      and the 5-row `examples/benchmarking/anchor_questions.json` from disk; assert
      all 110 rows import, every row has a non-empty question and answer, and
      `sources` is carried on every row. This is the acceptance test for the whole
      change — it fails today with the unknown-field error.
- [ ] Gate, commit.

## 9. Verify and open the PR

- [ ] Full suite plus `bash scripts/gate.sh`; confirm patch coverage clears 80%.
- [ ] **On the dev deployment**, upload the real bank through `/evaluations`,
      confirm it appears in the catalog with 110 rows, then generate and save atoms
      for a small subset and confirm the saved child dataset still carries `sources`.
      That last step is the one a unit test cannot prove, and it is the failure this
      change exists to prevent. Record the dataset ids in the PR body.
- [ ] Push, open the PR against `fasrc/archi` base `dev`, post `@codex review`.

# Design — slice exclusions independent of emitted rows and of the baseline's label (#447)

Anchors are on `origin/dev` `be42428b`, `scripts/benchmarking/compare_runs.py`.

## D1. One membership helper, two consumers

Extract the per-field membership loop of `slice_block` (`:1798-1836`) into a private helper:

    def _slice_membership(
        baseline: Arm, arms: Sequence[Arm], questions: Sequence[str], field: str
    ) -> Tuple[Dict[str, List[str]], int]:
        """Return (groups, mismatched) for one slice field."""

Then add a public function beside `slice_block`:

    def slice_exclusions(
        baseline: Arm, arms: Sequence[Arm], questions: Sequence[str]
    ) -> Dict[str, int]:
        """{field: mismatched} for every SLICE_FIELDS entry that every arm carries."""

Both `slice_block` and `slice_exclusions` apply the same `has_metric` gate
(`if not all(arm.has_metric(field) for arm in arms): continue`) and call
`_slice_membership`. One loop means one definition of a disagreement, so the row value and the
top-level value cannot drift.

`slice_block` keeps its signature and its `List[dict]` return. More than 20 existing tests call
it directly (`tests/unit/test_compare_runs.py:1945` onward); changing the return type would
churn all of them for no gain. Rejected: returning a tuple from `slice_block`.

`build_report` (`:2700`) adds `"slice_exclusions": slice_exclusions(baseline, arms, questions)`
next to `"slices"` (`:2741`). The `--json` writer dumps the report dict, so the key reaches the
JSON with no other change.

## D2. Labelled arms only

Inside `_slice_membership`, for each question:

1. Collect the labels of the arms with a **clean** row (`arm.has_clean_row(question)`), as now.
2. Drop each label that is **unlabelled**: `label is None or label == ""`. `None` covers both a
   missing key and JSON `null`. Any other value — a non-empty string, a number, a bool, a list,
   a dict, an empty list — is a label. Keep the existing `_label_key` canonical-JSON comparison
   (`:867`) unchanged for these.
3. If the labelled set has two or more members whose `_label_key` differs, count one mismatch
   and `continue` (the question joins no group).
4. Otherwise, the question joins a group only when the baseline row is clean **and** the
   baseline's value is a non-empty string. The group key is that string.

The old early `continue` on the baseline's label (`:1801-1803`) moves below the mismatch test,
into step 4. This is the whole gap-B fix: the count no longer depends on which arm is the
baseline. The operator's decision rejected counting an unlabelled row as a disagreement,
because artifacts from before #431 carry no labels and would flood the count.

**Behavior change to watch.** Before this change, a clean non-baseline arm with no label versus
a labelled arm counted as a mismatch (`None` vs `"hard"`). After it, that pair is skipped. If an
existing test asserts the old count for that shape, the test encodes the rejected rule: update
its expectation and docstring, and say so in the commit message. Do not change any test about
`true` vs `1`, nested labels, failed or degraded rows, or genuine relabelling — those must
pass unchanged.

## D3. Renderer

In `render_markdown`, the "## Slices" section (`:2389-2427`) becomes:

1. If `report["slices"]`: print the table as now.
2. Then, for each `(name, count)` in `sorted(report.get("slice_exclusions", {}).items())` with
   `count > 0`, print the existing "carry a different" sentence — unchanged text.
3. If there are no slice rows:
   - If `report.get("slice_exclusions")` is empty (the `has_metric` gate excluded every field),
     print the existing "No slice field (…) is present in every arm." sentence.
   - Else, if no count is non-zero, print
     "No slice group has a paired, scored question in every arm."
   - Else print nothing more — the step-2 sentences already explain the empty section.

`.get(..., {})` keeps a hand-built report without the key renderable (there is one golden test
at `tests/unit/test_compare_runs.py:2305` that builds the report through `build_report`; it
must stay byte-identical unless its fixture triggers a non-zero exclusion, which it does not
today).

## D4. Docs

Rewrite the bullet that begins "Two limits on that, both being tracked in issue #447" in
`docs/docs/interpreting_benchmark_results.md` (around line 290). State that:

- the count lives at the top level of the report (`slice_exclusions` in `--json`), one entry
  per field every arm carries, zero included, and also on each emitted slice row;
- a row with no value for the field is skipped in the comparison, the baseline included, so
  the count is the same whichever arm is `--baseline`;
- the sentence prints even when no slice survives.

Replace the "Do not read the paragraph above…" paragraph of the `slice_block` docstring
(`:1786-1792`) with the new rule; leave no reference to #447 as open work.

## D5. Coverage

The gate measures `--cov=src`. `scripts/` is outside it, so diff-cover scores nothing here and
the 80% bar passes regardless. The acceptance tests in `tasks.md` are the only protection.

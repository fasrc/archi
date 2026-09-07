# Design — a failed row is not evidence of a bank edit

## Context

`scripts/benchmarking/compare_runs.py` compares two or more finished benchmark artifacts. It
is a standalone analysis script, not part of the `src` package. Anchors below are at
`origin/dev` `3170498c`, read on 2026-09-07.

The path in question is one loop. `build_report` (`compare_runs.py:2086`) calls
`slice_block(baseline, arms, questions, sigmas)` (`compare_runs.py:2117`). `slice_block`
(`compare_runs.py:1355`) walks `SLICE_FIELDS = ("anchor_type", "difficulty")`
(`compare_runs.py:85`) and, per field, sorts the paired questions into groups by the value the
baseline recorded. Three lines decide membership (`compare_runs.py:1382-1388`):

```python
value = baseline.rows.get(question, {}).get(field)
if not (isinstance(value, str) and value):
    continue
if any(arm.rows.get(question, {}).get(field) != value for arm in arms):
    mismatched += 1
    continue
groups.setdefault(value, []).append(question)
```

`mismatched` is emitted on every row of that field as `excluded_mismatched`
(`compare_runs.py:1425`) and the renderer turns a non-zero value into a sentence that names a
bank edit as the cause (`compare_runs.py:1897-1903`).

Three facts about the surrounding code shape this design. Each was measured, not assumed.

1. **`has_metric` does not disqualify a partly-present field.** It is
   `any(metric in row for row in self.rows.values())` (`compare_runs.py:209`). With one
   failure row missing `difficulty` and the rest carrying it, the observed value is `True` for
   every arm, so the `all(arm.has_metric(field) for arm in arms)` guard at
   `compare_runs.py:1377` lets the field through to the membership loop.
2. **`baseline` is always an element of `arms`.** It is `arms[0]` (`compare_runs.py:2237`) or
   `matches[0]` where `matches` filters `arms` (`compare_runs.py:2246`). So a rule written as
   `all(arm ... for arm in arms)` covers the baseline arm too, with no extra clause.
3. **A question that is not scorable in every arm already contributes nothing.**
   `paired_deltas` (`compare_runs.py:594-605`) keeps a question only when
   `base.is_scorable(...) and treat.is_scorable(...)`, and `slice_block` drops a group whose
   summary has `n == 0` (`compare_runs.py:1399`). So such a question is invisible in every
   slice number whether it sits in the group or not.

`compare_runs.py` has no top-level `src` import, on purpose: `src/utils/__init__.py` pulls the
config service, which needs `psycopg2`, and an analysis host reading finished artifacts must
not need a database driver. The two `src` imports in the file are lazy and each carries that
reason (`compare_runs.py:1150`, `compare_runs.py:1455`), and
`test_the_anchors_reader_works_without_importing_the_src_package`
(`tests/unit/test_compare_runs.py:1598`) enforces it by deleting `src.utils` from
`sys.modules`.

## Goals / Non-Goals

**Goals:**

- `excluded_mismatched` counts genuine bank relabelling and nothing else, so the sentence the
  renderer prints is true whenever it appears.
- Both `SLICE_FIELDS` behave the same way.
- One definition of "this row failed" in `compare_runs.py`, not two.
- Every slice number — `value`, `n`, `mean`, `se`, `sigma`, `verdict`, `directional` — is
  unchanged for every input shape.
- The pre-#440 artifacts already on disk read honestly, without being rewritten.

**Non-Goals:**

- Changing what the producer writes. That is issue #431 / PR #440.
- Rewriting the committed `bench_out` artifacts. That is issue #426 / PR #438.
- Reporting the questions dropped for having failed. `scored_counts`
  (`compare_runs.py:1042-1090`) already reports, per arm and per metric, how many rows were
  scorable against how many were eligible, so the information is on the report already.
  Adding a second counter for the same fact is scope this issue does not ask for.

## Decisions

### D1 — Where the fix goes: the consumer's membership test

The false count is produced by one expression reading one row. Fix it there, at
`compare_runs.py:1385`, rather than at `has_metric`, at load time, or by normalizing rows on
read. A load-time normalizer would have to invent a `difficulty` value for a row that never
had one, which is exactly the dishonesty the issue objects to.

### D2 — Reuse the existing notion of "failed": extract `Arm.has_clean_row`

The issue asks to reuse `Arm.is_scorable`'s notion rather than invent a second definition.
`is_scorable` (`compare_runs.py:203-207`) is two rules in one function: a status rule and a
finiteness rule.

```python
def is_scorable(self, question: str, metric: str) -> bool:
    row = self.rows.get(question)
    if row is None or row.get("status", "ok") != "ok":
        return False
    return is_finite(row.get(metric))
```

Slice membership needs the status rule alone — it is not about a metric. So the status rule
gets a name and `is_scorable` calls it:

```python
def has_clean_row(self, question: str) -> bool:
    """Whether this arm ran the question to completion."""
    row = self.rows.get(question)
    return row is not None and row.get("status", "ok") == "ok"

def is_scorable(self, question: str, metric: str) -> bool:
    if not self.has_clean_row(question):
        return False
    return is_finite(self.rows[question].get(metric))
```

The `"ok"` default is the load-bearing detail and it is preserved verbatim: an unmarked
legacy row counts as clean. `self.rows[question]` is safe in the rewritten `is_scorable`
because `has_clean_row` returning `True` already proves the key is present.

Rejected: importing `is_scorable` from `src/utils/benchmark_resilience.py:54`
(`q_results.get("status", OK) == OK`, the producer's copy of the same rule). It is the same
rule and it would be the ideal single source, but a top-level `src` import is forbidden here
for the reason recorded in Context, and a lazy import inside a per-question loop buys a
`sys.modules` lookup per row to save four lines. The two definitions agree by construction and
`is_scorable` in `compare_runs.py` was already a separate copy before this change.

Rejected: a module-level function taking a row dict. `Arm` already owns row lookup for
`value`, `is_scorable`, and `has_metric`; a fourth accessor that takes a raw dict would split
that ownership.

### D3 — An unclean question is skipped, not counted

The new guard goes above the mismatch test, so a question that any arm did not run to
completion never reaches it:

```python
if not all(arm.has_clean_row(question) for arm in arms):
    continue
if any(arm.rows.get(question, {}).get(field) != value for arm in arms):
    mismatched += 1
    continue
```

Order matters. Below the mismatch test the guard would be dead code for the pre-#440 shape,
because the mismatch test already fired.

The question is also dropped from `groups`, which is the membership decision the issue asks to
have decided and documented. It is free, by fact 3 in Context: `paired_deltas` filters it out
and an empty group is dropped, so the slice numbers are identical either way. Dropping it is
chosen over grouping it because the group would then contain a member that can never
contribute, which reads as a bug to the next person to touch `slice_block`.

Measured on 2026-09-07 by loading the current file and a patched copy side by side and
comparing `slice_block` output key by key. In all five shapes every key except
`excluded_mismatched` was identical:

| shape | `excluded_mismatched` | `difficulty` slices `(value, n)` |
|---|---|---|
| pre-#440: failure row has no `difficulty` | 1 → **0** | `(easy,2) (hard,1)` both runs |
| post-#440: failure row carries `difficulty` | 0 → 0 | `(easy,2)` both runs |
| genuine relabelling, clean in both arms | 1 → **1** | `(easy,1)` both runs |
| `status: degraded` row | 1 → **0** | `(easy,2)` both runs |
| the baseline arm is the one that failed | 0 → 0 | `(easy,2)` both runs |

Two rows of that table are worth naming. A `degraded` row is caught by the same guard,
because `has_clean_row` tests the status rather than listing failure names — the issue named
only the raising case, and this covers it without a second rule. And the post-#440 row is the
proof that the two changes compose: once the producer writes the field on a failure row, the
values agree, the mismatch test never fires, and this guard is the only thing that changes,
by removing a member that could not contribute.

### D4 — `has_metric` keeps `any(...)`

Making the field-presence guard strict (`all(metric in row ...)`) looks like a cleaner fix at
the same place. It is worse, and measurably so. On the pre-#440 shape and on the degraded
shape, the strict form is `False` for the treatment arm, which skips the field entirely: the
false counter disappears and so do the honest slices, measured as `('easy', n=2)` and
`('hard', n=1)`. Trading a wrong number for a missing measurement is not an improvement on a
benchmark-integrity path. `has_metric` is also read by the metric guard at
`compare_runs.py:1394` and by `paired_block`, so tightening it reaches further than slices.

### D5 — `anchor_block`'s status test is left alone

`compare_runs.py:1310` has a third instance of `row.get("status", "ok") != "ok"`. It is not
the same predicate: it is one side of an `or` whose other side is an anchor-specific "this arm
produced no metrics and is not the baseline" clause, and it evaluates a `row` the enclosing
loop already holds. Rewriting it to `not arm.has_clean_row(question)` would be a readability
change on a path this issue's acceptance never exercises, on the file's most delicate gate
(G8). Left for a separate change if anyone wants it.

### D6 — No docs change

`docs/docs/interpreting_benchmark_results.md` is the page that documents the slice block. It
never mentions `excluded_mismatched` or the dropped-question sentence — checked by grep across
`docs/docs/` for `excluded_mismatched`, `relabell`, and `dropped from every slice`, which
match nothing. There is no false sentence to correct, so the rule is documented where it is
enforced: the `slice_block` docstring and the spec delta. This also keeps the diff off a file
that open PR #440 edits at `:578-586` and `:683`.

## Risks / Trade-offs

- **Patch coverage cannot catch an untested line here.** The gate runs
  `python -m pytest tests/unit/ --cov=src` (`scripts/gate.sh`), and both changed paths are
  outside `src/`, so `diff-cover` scores an empty measurement and the 80% bar passes either
  way. Mitigation: `tasks.md` names every test to add and requires running
  `tests/unit/test_compare_runs.py` by path and reading the collected count, and the tests are
  written before the code so an untested line cannot be the reason a task looks done.
- **A silently smaller slice.** After this change a question that failed in one arm is in no
  group, and nothing in the slice block says so. It was already absent from every slice
  number before this change (fact 3), and `scored_counts` reports the per-arm scorable counts,
  so no information is lost that the report did not already carry. Accepted, per the
  non-goals.
- **`status` is trusted.** An artifact whose failure row was written with `status: "ok"` and no
  metrics still reaches the mismatch test and can still be miscounted. That is the producer's
  contract, `build_failure_entry` sets `status: FAILED`, and inventing a heuristic for a row
  that lies about its own status would be a second definition of failure — the thing the issue
  forbids.
- **Interplay with PR #440.** No shared file (checked by file list, 2026-09-07). Whichever
  lands second needs no reconciliation: #440 makes the producer write the field, this change
  makes the consumer ignore an unclean row, and the measured post-#440 shape above shows the
  two compose with no change to any slice number.

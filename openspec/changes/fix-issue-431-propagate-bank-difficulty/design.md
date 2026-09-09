# Design — bank difficulty in the per-question result

## Context

The path from a bank row to an artifact row has three stops, and only the last one loses
the field.

1. **Load and normalize.** `normalize_record` (`src/utils/benchmark_schema.py:109-124`)
   builds `out = dict(record)` and renames only the four legacy dialect keys. Its
   docstring says "Extension fields are preserved verbatim", and the code matches: an
   unrecognised key such as `difficulty` is neither dropped nor validated.
2. **Score one question.** `_answer_and_score_question`
   (`src/bin/service_benchmark.py:1838`) receives that dict as `question_item` and builds
   `q_results` field by field. It copies exactly one bank field —
   `q_results["anchor_type"]` at :1911-1915.
3. **Assemble the arm.** `current_results["single_question_results"] = results`
   (`src/bin/service_benchmark.py:408`) stores the `q_results` dicts as they are. There is
   no allowlist, no schema filter, and no serializer that would reject a new key.

So the fix is at stop 2 and nowhere else. This matters because the obvious wrong diagnosis
— "the normalizer strips unknown bank fields" — would send an implementer into
`benchmark_schema.py` to add `difficulty` to a passthrough list that does not exist.

The consumer is already built. `compare_runs.py:85` declares
`SLICE_FIELDS: Tuple[str, ...] = ("anchor_type", "difficulty")` and slices on a field when
both arms carry it; `tests/unit/test_compare_runs.py:1148-1175` already exercises the
`difficulty` slice against hand-built artifact rows. Nothing in `scripts/` needs a change.

## Goals / Non-Goals

**Goals:**

- A bank row's `difficulty` reaches `single_question_results` unchanged.
- A bank without the field produces an artifact of exactly the shape it produces today.
- The key the harness writes and the key `compare_runs.py` slices on cannot drift apart
  silently.

**Non-Goals:**

- Validating the value. `easy` / `medium` / `hard` is the convention in
  `ragas-jeopardy-master.json`, but the harness does not police `anchor_type` values
  either, and a bank-side rule belongs to the maintenance tooling
  (`src/utils/goldenset_maintenance.py`), not to the scoring path.
- Pushing `difficulty` to Argilla (Gap 6).
- Editing any bank, in this repository or in `archi-config`.

## Decisions

### Decision: the copy is conditional, where `anchor_type`'s is not

`anchor_type` is written unconditionally with a `""` fallback:

```python
q_results["anchor_type"] = (
    question_item.get("anchor_type", "")
    if isinstance(question_item, dict)
    else ""
)
```

Copying `difficulty` the same way is the smaller diff and the wrong behaviour. Three
reasons:

- **`compare_runs.py` reads presence, not truthiness.** A `difficulty` key present on
  every row with the value `""` is a field "present in both arms" with one degenerate
  group. The tool would report a slice over a single bucket named `""` rather than
  skipping the slice — a worse answer than no answer, because it looks like a result.
- **Artifact shape is compared across runs.** Procedure E asks a reader to confirm two
  runs are comparable. Adding an always-present key changes every artifact from a bank
  that has no `difficulty`, including the FASRC bank, which is the bank most runs use.
- **`anchor_type`'s fallback is not a precedent to extend.** It exists because the anchor
  path sets `anchor_type` on every merged question dict
  (`src/bin/service_benchmark.py:2182`), so `""` marks a non-anchor row rather than a
  missing field. `difficulty` has no such producer.

The issue states this directly as a constraint: "write nothing (not `null`) when it does
not, so existing artifacts and banks without the field stay byte-identical in shape."

### Decision: guard the producer/consumer key by name, not by value

The harness writes the key; `compare_runs.py` reads it. Nothing links them but the
spelling of the string `"difficulty"` in two files that no shared import connects.

The guard is a test asserting that the key `_answer_and_score_question` writes is a member
of `compare_runs.SLICE_FIELDS`. It pins the **agreement between two names**, which is the
thing that can break. It deliberately does not pin *which* values the harness emits or
*what* the slice prints — a test that asserted a particular difficulty value or a
particular slice output would pass at merge and fail later when a bank changed, which is
the failure mode a seam test is supposed to prevent rather than create.

`tests/unit/test_compare_runs.py` already loads `compare_runs.py`, so the import shape is
established and needs no new machinery.

### Decision: the stale admonition is part of the change, not a follow-up

`docs/docs/interpreting_benchmark_results.md:578-586` currently states, as fact, "Artifacts
written by the current harness therefore carry no `difficulty`, and the slice is skipped".
That sentence becomes false the moment this change lands. Leaving it is worse than never
having written it: a reader who hits it stops looking for a slice that is now there.

## Risks / Trade-offs

- **A bank could carry a non-string `difficulty`.** The harness copies it verbatim, and
  `json_safe` (`src/utils/benchmark_schema.py`) handles the serialization boundary for
  non-finite numbers. `compare_runs.py` groups by the value, so a list would raise there,
  not here. Accepted: the harness does not validate `anchor_type` values either, and
  guessing a validation rule the issue did not ask for is how a scoped fix grows.
- **Only `ragas-jeopardy-master.json` carries the field today**, and that bank lives
  outside this repository. The unit tests therefore construct their own bank rows rather
  than reading a real bank; that is also what makes them runnable in the gate, which has
  no network and no config repository.

## Migration Plan

None. The change is additive to a written artifact. Artifacts already on disk are not
rewritten and stay readable — `compare_runs.py` skips the slice for them exactly as it
does today.
